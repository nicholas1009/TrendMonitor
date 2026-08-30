from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.runtime import (
    ProcessLock,
    RuntimeConfig,
    RuntimeRunner,
    RuntimeSnapshotReader,
    RuntimeStore,
    due_periods,
    retry_action,
)
from trend_monitor.runtime.logging import runtime_logger
from trend_monitor.runtime.security import audit_dotenv
from trend_monitor.runtime.pipeline import (
    PipelineRefreshResult,
    RuntimeStageError,
    build_combined_result,
    classify_stage_failure,
)
from trend_monitor.runtime.schedule import period_identity
from trend_monitor.schemas.runtime import RuntimeRunRecord


ROOT = Path(__file__).resolve().parents[1]
CONFIG = RuntimeConfig.load(ROOT / "config" / "runtime_schedule.json", project_root=ROOT)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 28)


def periods(at: str, *, historical: bool = False):
    return due_periods(
        datetime.combine(DAY, datetime.strptime(at, "%H:%M").time(), tzinfo=SHANGHAI),
        trading_day=DAY,
        periods=CONFIG.raw["periods"],
        buffer_minutes=3,
        live_grace_minutes=10,
        historical_execution=historical,
    )


def source(period_end: str) -> dict:
    market = {
        "last_completed_bar_end": period_end,
        "rules_version": "market_60m_risk_v0.1",
        "risk_score": 2,
        "risk_light": "YELLOW",
        "risk_direction": "FLAT",
        "signal_confidence": "HIGH",
        "breadth": {"advance_count": 4},
        "index_states": [{"instrument_id": str(i)} for i in range(8)],
        "data_quality": {"valid_index_count": 8, "preflight": {str(i): "PASS_WITH_DEGRADATION" for i in range(8)}},
    }
    market15 = {
        "60m_period_end": period_end,
        "rules_version": "market_15m_internal_v0.1",
        "market_internal_state": "INTERNAL_MIXED",
        "data_quality": {"lookahead_safe": True},
    }
    stocks = {}
    for instrument_id, symbol in (("stock.hengtong_optic", "600487"), ("stock.wus_printed_circuit", "002463")):
        stocks[instrument_id] = {
            "stock_60m": {
                "period_end": period_end,
                "rules_version": "stock_60m_risk_v0.1",
                "symbol": symbol,
                "name": instrument_id,
                "risk_score": 1,
                "risk_light": "YELLOW",
                "risk_direction": "FLAT",
                "confidence": "HIGH",
                "data_quality": {"lookahead_safe": True},
            },
            "stock_15m": {
                "rules_version": "stock_15m_internal_v0.1",
                "classification": "MIXED",
                "direction_sequence": ["UP", "DOWN", "UP", "DOWN"],
                "data_quality": {"lookahead_safe": True},
            },
        }
    return {
        "market": market,
        "market_15m": market15,
        "stocks": stocks,
        "source_ids": {
            "market_result_id": "market",
            "market_15m_result_id": "market15",
            "stock_result_ids": {key: key for key in stocks},
        },
        "source_safety": {
            "market_lookahead": True,
            "market_15m_lookahead": True,
            "stock_lookahead": True,
            "stock_score_immutable": True,
        },
    }


class StaticCalendar:
    def __init__(self, trading: bool):
        self.trading = trading

    def is_trading_day(self, value, *, allow_network, observed_at):
        return self.trading, "TEST_CALENDAR"


class FakeReader:
    def load_period(self, period_end):
        return source(period_end)


class FakePipeline:
    def __init__(self):
        self.calls = 0

    def refresh(self, *, as_of):
        self.calls += 1
        return PipelineRefreshResult(5, ({"status": "PASS"},))


class ScheduleTests(unittest.TestCase):
    def test_trading_day_1030_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("10:33")], ["10:30"])

    def test_1130_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("11:33")], ["10:30", "11:30"])

    def test_1400_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("14:03")], ["10:30", "11:30", "14:00"])

    def test_1500_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("15:03")], ["10:30", "11:30", "14:00", "15:00"])

    def test_lunch_has_no_extra_period(self):
        self.assertEqual([p.period_end[11:16] for p in periods("13:30")], ["10:30", "11:30"])

    def test_catch_up_marks_missed_periods(self):
        result = periods("14:50")
        self.assertEqual([p.execution_mode for p in result], ["CATCH_UP", "CATCH_UP", "CATCH_UP"])

    def test_china_timezone_not_host_timezone(self):
        tokyo = datetime(2026, 8, 28, 11, 33, tzinfo=ZoneInfo("Asia/Tokyo"))
        shanghai = tokyo.astimezone(SHANGHAI)
        self.assertEqual(shanghai.strftime("%H:%M"), "10:33")


class LockRetryTests(unittest.TestCase):
    def test_lock_blocks_concurrent_process(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.lock"
            first = ProcessLock(path, stale_seconds=60)
            second = ProcessLock(path, stale_seconds=60)
            now = datetime.now(SHANGHAI)
            self.assertTrue(first.acquire(run_id="one", now=now))
            self.assertFalse(second.acquire(run_id="two", now=now))
            first.release()
            second.release()

    def test_stale_metadata_is_recovered(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.lock"
            path.write_text(json.dumps({"pid": 999999, "created_at": (datetime.now(SHANGHAI) - timedelta(days=1)).isoformat()}), encoding="utf-8")
            lock = ProcessLock(path, stale_seconds=60)
            self.assertTrue(lock.acquire(run_id="new", now=datetime.now(SHANGHAI)))
            self.assertTrue(lock.previous_stale)
            lock.release()

    def test_transient_failure_retries(self):
        calls = []
        def action():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeStageError("NETWORK_ERROR", "TEST", "temporary")
            return "ok"
        result, attempts = retry_action(action, max_attempts=3, backoff_seconds=[0, 0], retryable_categories={"NETWORK_ERROR"}, sleeper=lambda _: None)
        self.assertEqual((result, attempts), ("ok", 3))

    def test_deterministic_failure_does_not_retry(self):
        calls = []
        def action():
            calls.append(1)
            raise RuntimeStageError("UNSUPPORTED", "TEST", "deterministic")
        with self.assertRaises(RuntimeStageError):
            retry_action(action, max_attempts=3, backoff_seconds=[0, 0], retryable_categories={"NETWORK_ERROR"}, sleeper=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_failure_classifier(self):
        self.assertEqual(classify_stage_failure("UNSUPPORTED period"), "UNSUPPORTED")
        self.assertEqual(classify_stage_failure("NETWORK_ERROR"), "NETWORK_ERROR")


class StoreSecurityTests(unittest.TestCase):
    def record(self, run_id="run"):
        return RuntimeRunRecord(
            run_id=run_id, scheduled_period=None, started_at="2026-08-28T10:33:00+08:00",
            completed_at="2026-08-28T10:33:01+08:00", duration_seconds=1, trading_date="2026-08-28",
            period_end=None, status="SUCCESS", network_attempts=0, market_result_id=None,
            market_15m_result_id=None, stock_result_ids={}, error_summary=None,
            rules_versions=CONFIG.rules_versions, execution_mode=None, notification_eligibility=None,
        )

    def test_append_only_manifest(self):
        with TemporaryDirectory() as tmp:
            store = RuntimeStore(tmp)
            store.append(self.record("one"), idempotency_key="one")
            store.append(self.record("two"), idempotency_key="two")
            self.assertEqual(len(store.entries()), 2)

    def test_duplicate_result_reuses_same_report(self):
        with TemporaryDirectory() as tmp:
            store = RuntimeStore(tmp)
            payload = {"period_end": "2026-08-28T10:30:00+08:00", "risk": 1, "generated_at": "one"}
            machine, human, digest = store.save_report(payload, "report", idempotency_key="key")
            record = self.record("one")
            record = RuntimeRunRecord(**{**record.to_dict(), "combined_result_id": machine})
            store.append(record, idempotency_key="key", result_sha256=digest, human_report_id=human)
            second = dict(payload, generated_at="two", execution_mode="CATCH_UP")
            self.assertEqual(store.save_report(second, "report", idempotency_key="key")[0], machine)

    def test_no_secret_in_rotating_log(self):
        with TemporaryDirectory() as tmp:
            secret = "super-secret-token"
            path = Path(tmp) / "runtime.log"
            logger = runtime_logger(path, secrets=(secret,), max_bytes=1000, backups=1)
            logger.error("access_token=%s", secret)
            for handler in logger.handlers:
                handler.flush()
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_env_permission_fails_closed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "LONGBRIDGE_APP_KEY=a\nLONGBRIDGE_APP_SECRET=b\nLONGBRIDGE_ACCESS_TOKEN=c\nHITHINK_FINANCE_API_KEY=d\n",
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            result = audit_dotenv(path, CONFIG.raw["secret_keys"])
            self.assertEqual((result["status"], result["reason"]), ("FAIL", "ENV_PERMISSION_MUST_BE_0600"))


class SnapshotAndRunnerTests(unittest.TestCase):
    def test_real_replay_reader_and_lookahead(self):
        result = RuntimeSnapshotReader(ROOT).load_period("2026-08-28T15:00:00+08:00")
        period = periods("15:03", historical=True)[-1]
        combined = build_combined_result(result, scheduled_period=period, generated_at=datetime.now(SHANGHAI))
        self.assertTrue(combined["data"]["lookahead_safe"])
        self.assertEqual(combined["data"]["industry_context"], "DEFERRED")

    def _runner(self, tmp, *, trading=True, pipeline=None):
        logger = logging.getLogger(f"runtime-test-{tmp}")
        logger.handlers.clear(); logger.addHandler(logging.NullHandler())
        return RuntimeRunner(
            project_root=ROOT, config=CONFIG, calendar=StaticCalendar(trading),
            store=RuntimeStore(Path(tmp) / "runtime"), reader=FakeReader(),
            pipeline=pipeline, logger=logger, lock_path=Path(tmp) / "runner.lock",
        )

    def test_non_trading_day_skips_without_pipeline(self):
        with TemporaryDirectory() as tmp:
            pipeline = FakePipeline()
            result = self._runner(tmp, trading=False, pipeline=pipeline).run(
                as_of=datetime(2026, 8, 29, 10, 33, tzinfo=SHANGHAI)
            )
            self.assertEqual(result["status"], "SKIPPED_NON_TRADING_DAY")
            self.assertEqual(pipeline.calls, 0)

    def test_current_runner_and_duplicate_are_idempotent(self):
        with TemporaryDirectory() as tmp:
            pipeline = FakePipeline()
            runner = self._runner(tmp, pipeline=pipeline)
            now = datetime.now(SHANGHAI).replace(hour=10, minute=33, second=0, microsecond=0)
            first = runner.run(as_of=now)
            second = runner.run(as_of=now)
            self.assertEqual(first["results"][0]["status"], "SUCCESS_WITH_DEGRADATION")
            self.assertEqual(second["status"], "SKIPPED_ALREADY_COMPLETED")
            self.assertIsNotNone(second["combined_result_id"])
            self.assertEqual(second["market_result_id"], "market")
            self.assertEqual(pipeline.calls, 1)

    def test_historical_no_network_runner_catches_up(self):
        with TemporaryDirectory() as tmp:
            runner = self._runner(tmp, pipeline=None)
            result = runner.run(as_of=datetime(2026, 8, 28, 14, 50, tzinfo=SHANGHAI), no_network=True)
            self.assertEqual(len(result["results"]), 3)
            self.assertTrue(all(item["execution_mode"] == "CATCH_UP" for item in result["results"]))
            self.assertTrue(all(item["network_attempts"] == 0 for item in result["results"]))

    def test_frozen_rules_are_not_mutated(self):
        before = CONFIG.verify_frozen_rules()
        with TemporaryDirectory() as tmp:
            self._runner(tmp, pipeline=None).run(
                as_of=datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI), no_network=True
            )
        self.assertEqual(CONFIG.verify_frozen_rules(), before)

    def test_runtime_failure_record_is_persisted(self):
        class BrokenReader:
            def load_period(self, period_end):
                raise ValueError("missing replay")
        with TemporaryDirectory() as tmp:
            logger = logging.getLogger(f"broken-{tmp}"); logger.handlers.clear(); logger.addHandler(logging.NullHandler())
            store = RuntimeStore(Path(tmp) / "runtime")
            runner = RuntimeRunner(
                project_root=ROOT, config=CONFIG, calendar=StaticCalendar(True), store=store,
                reader=BrokenReader(), pipeline=None, logger=logger, lock_path=Path(tmp) / "runner.lock",
            )
            result = runner.run(
                as_of=datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI), no_network=True
            )
            self.assertEqual(result["results"][0]["status"], "FAILED")
            self.assertEqual(store.entries()[0]["error_summary"]["stage"], "COMBINED_RUNTIME_REPORT")


if __name__ == "__main__":
    unittest.main()
