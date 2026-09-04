from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from trend_monitor.notifications import NotificationPolicy, NotificationPolicyConfig
from trend_monitor.providers.hithink import HithinkProvider
from trend_monitor.providers.hithink.transport import HttpResponse
from trend_monitor.runtime import AuctionRunner, AuctionTarget, RuntimeStore
from trend_monitor.runtime.auction import parse_auction_snapshot


SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOLS = ("600487.SH", "002463.SZ")
TARGETS = (
    AuctionTarget("stock.hengtong_optic", SYMBOLS[0], "亨通光电"),
    AuctionTarget("stock.wus_printed_circuit", SYMBOLS[1], "沪电股份"),
)
ROOT = Path(__file__).resolve().parents[1]


def at(clock: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-01T{clock}+08:00")


def final_raw(*, null_unmatched: bool = False) -> dict:
    return {
        "code": 0,
        "message": "success",
        "request_id": "request-1",
        "data": {
            "timestamp": 1788225900000,
            "auction_phase": "closed",
            "data_status": "final",
            "total": 2,
            "item": [
                {
                    "thscode": "600487.SH",
                    "ticker": "600487",
                    "name": "亨通光电",
                    "auction_price": 25.2,
                    "auction_pct": 2.1,
                    "auction_volume": 12345.0,
                    "auction_amount": 23456789.0,
                    "auction_unmatched": None if null_unmatched else 4321.0,
                    "auction_turnover_pct": 0.31,
                    "auction_yesterday_ratio_pct": 1.23,
                    "auction_volume_ratio": 1.8,
                    "pre_close_price": 24.68,
                    "open_price": 25.2,
                    "last_price": 25.2,
                    "float_market_cap": 1000000000.0,
                },
                {
                    "thscode": "002463.SZ",
                    "ticker": "002463",
                    "name": "沪电股份",
                    "auction_price": 45.0,
                    "auction_pct": -0.4,
                    "auction_volume": 6789.0,
                    "auction_amount": 12345678.0,
                    "auction_unmatched": -321.0,
                    "auction_turnover_pct": None,
                    "auction_yesterday_ratio_pct": 0.8,
                    "auction_volume_ratio": 0.7,
                    "pre_close_price": 45.18,
                    "open_price": 45.0,
                    "last_price": None,
                    "float_market_cap": None,
                },
            ],
        },
    }


def not_ready_raw() -> dict:
    return {
        "code": 0,
        "message": "success",
        "request_id": "request-2",
        "data": {
            "timestamp": 1788225840000,
            "auction_phase": "collecting",
            "data_status": "not_ready",
            "total": 0,
            "item": [],
        },
    }


def matched_raw() -> dict:
    raw = final_raw()
    raw["data"]["auction_phase"] = "matched"
    return raw


class Calendar:
    def __init__(self, trading: bool = True):
        self.trading = trading

    def is_trading_day(self, value, *, allow_network, observed_at):
        del value, allow_network, observed_at
        return self.trading, "HITHINK_OFFICIAL_CALENDAR" if self.trading else "WEEKEND"


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def auction_snapshot(self, thscodes, *, stage="final"):
        self.calls.append((tuple(thscodes), stage))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Notifier:
    def __init__(self):
        self.snapshots = []
        self.failures = []

    def process_auction_snapshot(self, snapshot, *, source_result_id, dry_run=False):
        self.snapshots.append((snapshot, source_result_id, dry_run))
        return {"status": "SENT", "event_count": 1}

    def process_auction_failure(self, record, *, dry_run=False):
        self.failures.append((record, dry_run))
        return {"status": "SENT", "event_count": 1}


class CaptureTransport:
    def __init__(self, raw):
        self.raw = raw
        self.url = None
        self.headers = None

    def get(self, url, headers, timeout):
        del timeout
        self.url = url
        self.headers = headers
        return HttpResponse(200, json.dumps(self.raw).encode())


class AuctionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = RuntimeStore(self.root / "data" / "runtime")
        self.notifier = Notifier()
        self.log_stream = io.StringIO()
        self.logger = logging.getLogger(f"auction-test-{id(self)}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.StreamHandler(self.log_stream))

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, provider, *, trading=True):
        return AuctionRunner(
            project_root=self.root,
            calendar=Calendar(trading),
            store=self.store,
            provider_factory=lambda: provider,
            targets=TARGETS,
            notifier=self.notifier,
            logger=self.logger,
            lock_stale_seconds=60,
        )

    def test_provider_request_uses_exact_symbols_and_final_stage(self):
        transport = CaptureTransport(final_raw())
        provider = HithinkProvider(api_key="sensitive-key", transport=transport)
        provider.auction_snapshot(list(SYMBOLS), stage="final")
        parsed = urlparse(transport.url)
        self.assertEqual(parsed.path, "/api/a-share/auction/snapshot")
        self.assertEqual(parse_qs(parsed.query), {"thscodes": [",".join(SYMBOLS)], "stage": ["final"]})
        self.assertEqual(transport.headers["X-api-key"], "sensitive-key")

    def test_final_parses_and_null_remains_null(self):
        parsed = parse_auction_snapshot(final_raw(null_unmatched=True), expected_symbols=SYMBOLS)
        self.assertTrue(parsed["final"])
        self.assertIsNone(parsed["items"][0]["auction_unmatched"])
        self.assertIsNone(parsed["items"][1]["last_price"])

    def test_not_ready_is_not_success(self):
        parsed = parse_auction_snapshot(not_ready_raw(), expected_symbols=SYMBOLS)
        self.assertFalse(parsed["final"])
        self.assertEqual(parsed["data_status"], "not_ready")

    def test_before_0925_skips_without_request(self):
        provider = Provider([final_raw()])
        result = self.runner(provider).run(as_of=at("09:24:59"))
        self.assertEqual((result["status"], result["reason"]), ("SKIPPED", "BEFORE_09_25"))
        self.assertEqual(provider.calls, [])

    def test_window_allows_request_at_both_boundaries(self):
        for clock in ("09:25:00", "09:32:59"):
            with self.subTest(clock=clock):
                provider = Provider([not_ready_raw()])
                result = self.runner(provider).run(as_of=at(clock))
                self.assertEqual(result["status"], "DATA_NOT_READY")
                self.assertEqual(provider.calls, [(SYMBOLS, "final")])

    def test_success_saves_one_raw_event_and_notification(self):
        provider = Provider([final_raw(), final_raw()])
        runner = self.runner(provider)
        first = runner.run(as_of=at("09:25:03"))
        second = runner.run(as_of=at("09:26:03"))
        self.assertEqual(first["status"], "SUCCESS")
        self.assertEqual((second["status"], second["reason"]), ("SKIPPED", "ALREADY_SUCCESSFUL"))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.store.event_entries()), 1)
        self.assertEqual(len(self.notifier.snapshots), 1)
        raw_manifest = (self.root / "data" / "raw" / "manifest.jsonl").read_text()
        self.assertEqual(len(raw_manifest.splitlines()), 1)
        raw = json.loads(Path(first["raw_snapshot_path"]).read_text())
        self.assertEqual(raw["provider"], "HITHINK")
        self.assertEqual(raw["stage"], "final")
        self.assertEqual(raw["raw_response"]["data"]["data_status"], "final")

    def test_not_ready_twice_then_final_retries_on_each_launchd_tick(self):
        provider = Provider([not_ready_raw(), not_ready_raw(), final_raw()])
        runner = self.runner(provider)

        results = [
            runner.run(as_of=at("09:25:00")),
            runner.run(as_of=at("09:26:00")),
            runner.run(as_of=at("09:27:00")),
        ]

        self.assertEqual([item["status"] for item in results], ["DATA_NOT_READY", "DATA_NOT_READY", "SUCCESS"])
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(len(self.notifier.snapshots), 1)
        self.assertEqual(len(self.notifier.failures), 0)
        self.assertEqual(
            [item["status"] for item in self.store.event_entries()],
            ["DATA_NOT_READY", "DATA_NOT_READY", "SUCCESS"],
        )

    def test_matched_through_0927_then_closed_at_0928_succeeds(self):
        provider = Provider([matched_raw(), matched_raw(), matched_raw(), final_raw()])
        runner = self.runner(provider)

        results = [
            runner.run(as_of=at("09:25:00")),
            runner.run(as_of=at("09:26:00")),
            runner.run(as_of=at("09:27:00")),
            runner.run(as_of=at("09:28:00")),
        ]

        self.assertEqual(
            [item["status"] for item in results],
            ["DATA_NOT_READY", "DATA_NOT_READY", "DATA_NOT_READY", "SUCCESS"],
        )
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(len(self.notifier.snapshots), 1)
        self.assertEqual(len(self.notifier.failures), 0)

    def test_matched_through_0931_then_closed_at_0932_succeeds(self):
        provider = Provider([matched_raw() for _ in range(7)] + [final_raw()])
        runner = self.runner(provider)
        clocks = [f"09:{minute:02d}:00" for minute in range(25, 33)]

        results = [runner.run(as_of=at(clock)) for clock in clocks]

        self.assertEqual([item["status"] for item in results[:-1]], ["DATA_NOT_READY"] * 7)
        self.assertEqual(results[-1]["status"], "SUCCESS")
        self.assertEqual(len(provider.calls), 8)
        self.assertEqual(len(self.notifier.snapshots), 1)
        self.assertEqual(len(self.notifier.failures), 0)

    def test_non_trading_day_skips(self):
        provider = Provider([final_raw()])
        result = self.runner(provider, trading=False).run(as_of=at("09:25:00"))
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(provider.calls, [])

    def test_after_deadline_records_one_terminal_failure_and_one_bark(self):
        provider = Provider([not_ready_raw()])
        runner = self.runner(provider)
        attempt = runner.run(as_of=at("09:32:59"))
        first = runner.run(as_of=at("09:33:00"))
        second = runner.run(as_of=at("09:34:00"))
        self.assertEqual(attempt["status"], "DATA_NOT_READY")
        self.assertEqual((first["status"], first["failure_reason"]), ("FAILED", "DATA_NOT_READY"))
        self.assertEqual((second["status"], second["reason"]), ("SKIPPED", "TERMINAL_FAILURE_RECORDED"))
        self.assertEqual(provider.calls, [(SYMBOLS, "final")])
        self.assertEqual(len(self.store.event_entries()), 2)
        self.assertEqual(len(self.notifier.failures), 1)

    def test_all_window_ticks_not_ready_terminalize_once_after_window(self):
        provider = Provider([matched_raw() for _ in range(8)])
        runner = self.runner(provider)

        attempts = [
            runner.run(as_of=at(f"09:{minute:02d}:00"))
            for minute in range(25, 33)
        ]
        first = runner.run(as_of=at("09:33:00"))
        second = runner.run(as_of=at("09:34:00"))

        self.assertEqual([item["status"] for item in attempts], ["DATA_NOT_READY"] * 8)
        self.assertEqual(len(provider.calls), 8)
        self.assertEqual(first["status"], "FAILED")
        self.assertEqual(second["status"], "SKIPPED")
        self.assertEqual(len(self.notifier.failures), 1)
        self.assertEqual(
            [item["status"] for item in self.store.event_entries()],
            ["DATA_NOT_READY"] * 8 + ["FAILED"],
        )

    def test_first_tick_final_prevents_all_later_requests(self):
        provider = Provider([final_raw(), final_raw(), final_raw()])
        runner = self.runner(provider)

        first = runner.run(as_of=at("09:25:00"))
        later = [runner.run(as_of=at(clock)) for clock in ("09:26:00", "09:27:00")]

        self.assertEqual(first["status"], "SUCCESS")
        self.assertTrue(all(item["reason"] == "ALREADY_SUCCESSFUL" for item in later))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.notifier.snapshots), 1)

    def test_delayed_provider_observation_keeps_auction_market_time_at_0925(self):
        provider = Provider([matched_raw(), final_raw()])
        runner = self.runner(provider)

        runner.run(as_of=at("09:28:00"))
        result = runner.run(as_of=at("09:29:00"))

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["auction_market_time"], "2026-09-01T09:25:00+08:00")
        self.assertEqual(result["provider_observed_at"], "2026-09-01T09:29:00+08:00")
        raw = json.loads(Path(result["raw_snapshot_path"]).read_text())
        self.assertEqual(raw["auction_market_time"], result["auction_market_time"])
        self.assertEqual(raw["provider_observed_at"], result["provider_observed_at"])

    def test_late_deployment_without_attempt_does_not_invent_data_failure(self):
        provider = Provider([final_raw()])
        result = self.runner(provider).run(as_of=at("09:33:00"))
        self.assertEqual(
            (result["status"], result["reason"]),
            ("SKIPPED", "MISSED_AUTOMATIC_WINDOW"),
        )
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.store.event_entries(), [])
        self.assertEqual(self.notifier.failures, [])

    def test_catch_up_is_explicitly_labeled_and_can_follow_failure(self):
        provider = Provider([final_raw()])
        runner = self.runner(provider)
        result = runner.run(as_of=at("10:00:00"), catch_up=True)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["execution_mode"], "CATCH_UP")
        self.assertEqual(provider.calls, [(SYMBOLS, "final")])

    def test_no_network_never_requests_or_records_failure(self):
        provider = Provider([final_raw()])
        result = self.runner(provider).run(as_of=at("09:33:00"), no_network=True)
        self.assertEqual((result["status"], result["reason"]), ("SKIPPED", "NO_NETWORK"))
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.store.event_entries(), [])

    def test_secret_does_not_enter_log_cache_or_manifest(self):
        secret = "task016-super-secret"
        transport = CaptureTransport(final_raw())
        provider = HithinkProvider(api_key=secret, transport=transport)
        result = self.runner(provider).run(as_of=at("09:25:00"))
        self.assertEqual(result["status"], "SUCCESS")
        contents = self.log_stream.getvalue()
        for path in (self.root / "data").rglob("*"):
            if path.is_file():
                contents += path.read_text(encoding="utf-8")
        self.assertNotIn(secret, contents)

    def test_notification_is_chinese_factual_and_omits_null(self):
        config = NotificationPolicyConfig.load(ROOT / "config" / "notification_policy.json")
        policy = NotificationPolicy(config)
        parsed = parse_auction_snapshot(final_raw(null_unmatched=True), expected_symbols=SYMBOLS)
        event = policy.evaluate_auction_snapshot(
            {
                "items": parsed["items"],
                "execution_mode": "LIVE_SCHEDULED",
                "trading_date": "2026-09-01",
                "scheduled_at": "2026-09-01T09:25:00+08:00",
            },
            source_result_id="raw-path",
        )[0]
        self.assertEqual(event.title, "TrendMonitor｜9:25集合竞价")
        self.assertIn("竞价：+2.10%", event.body)
        self.assertIn("亨通强于沪电", event.body)
        self.assertIn("状态：集合竞价已完成", event.body)
        self.assertNotIn("未匹配量：0", event.body)
        for forbidden in ("LIVE_SCHEDULED", "final", "closed", "建议买入", "建议卖出", "止损"):
            self.assertNotIn(forbidden, event.body)

    def test_failure_notification_has_no_internal_enum(self):
        config = NotificationPolicyConfig.load(ROOT / "config" / "notification_policy.json")
        event = NotificationPolicy(config).evaluate_auction_failure(
            {
                "run_id": "run-1",
                "trading_date": "2026-09-01",
                "scheduled_at": "2026-09-01T09:25:00+08:00",
                "execution_mode": "LIVE_SCHEDULED",
                "incomplete_names": ["亨通光电", "沪电股份"],
            }
        )[0]
        self.assertEqual(event.title, "TrendMonitor｜集合竞价数据异常")
        self.assertIn("亨通光电：未完成", event.body)
        self.assertNotIn("DATA_NOT_READY", event.body)

    def test_notification_policy_and_frozen_risk_hashes_are_unchanged(self):
        expected = {
            "config/notification_policy.json": "e2aef111ecc0dfc21420ca98a189f64b30886cbd746542f262098cf9481405bc",
            "config/market_60m_risk_rules.json": "0c001733e3986e73bbbe484e40cb483e7705cd2233959a5796146002e89fce82",
            "config/market_15m_internal_rules.json": "1ffd30195c6541b42dd92f9133cd5be422f5c4b38ec3bcd92a690fa9a4619d0d",
            "config/stock_intraday_risk_rules.json": "7fd2ec0b7670ce225ffe6df038967fcf2130bc46e7f517a8d2f84ad47097dd50",
            "config/risk_feature_contract.json": "0871e472babc57c1dc7085710933cbd2dfbf09b06d5308cebff1d1061c2f8528",
        }
        for relative, digest in expected.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
