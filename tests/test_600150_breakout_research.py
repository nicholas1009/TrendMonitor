from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
STUDY_PATH = ROOT / "research" / "600150" / "study.py"
STUDY_SPEC = importlib.util.spec_from_file_location("task026b_study", STUDY_PATH)
assert STUDY_SPEC is not None and STUDY_SPEC.loader is not None
study = importlib.util.module_from_spec(STUDY_SPEC)
sys.modules[STUDY_SPEC.name] = study
STUDY_SPEC.loader.exec_module(study)

CLI_PATH = ROOT / "scripts" / "research_600150_opening_add.py"
CLI_SPEC = importlib.util.spec_from_file_location("task026b_opening", CLI_PATH)
assert CLI_SPEC is not None and CLI_SPEC.loader is not None
opening = importlib.util.module_from_spec(CLI_SPEC)
sys.modules[CLI_SPEC.name] = opening
CLI_SPEC.loader.exec_module(opening)

LOCAL_EVIDENCE = study.DAILY_CSV.is_file() and study.STUDY_JSON.is_file()
SHANGHAI = ZoneInfo("Asia/Shanghai")
FROZEN_HASHES = {
    "config/market_60m_risk_rules.json": "0c001733e3986e73bbbe484e40cb483e7705cd2233959a5796146002e89fce82",
    "config/market_15m_internal_rules.json": "1ffd30195c6541b42dd92f9133cd5be422f5c4b38ec3bcd92a690fa9a4619d0d",
    "config/stock_intraday_risk_rules.json": "7fd2ec0b7670ce225ffe6df038967fcf2130bc46e7f517a8d2f84ad47097dd50",
    "config/risk_feature_contract.json": "0871e472babc57c1dc7085710933cbd2dfbf09b06d5308cebff1d1061c2f8528",
    "config/notification_policy.json": "e2aef111ecc0dfc21420ca98a189f64b30886cbd746542f262098cf9481405bc",
}


def bars(count: int = 300, *, start: date = date(2024, 1, 1)):
    result = []
    for index in range(count):
        close = 20 + index * 0.01
        result.append(study.DailyBar(
            trading_date=start + timedelta(days=index),
            open=close - 0.1,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000 + index,
            turnover=(1000 + index) * close,
            provider_timestamp=1_700_000_000 + index * 86_400,
        ))
    return result


def similarity_row(day: str, segment: str, value: float = 0.0):
    row = {
        "date": day,
        "segment": segment,
        "breakout_signature": "100",
        "t1_open_gap": value,
        "t1_close_return": 0.01,
        "t3_close_return": 0.02,
        "t5_close_return": 0.03,
        "t10_close_return": 0.04,
        "t1_mae": -0.01,
        "t1_mfe": 0.02,
        "t3_mae": -0.01,
        "t3_mfe": 0.03,
        "t5_mae": -0.01,
        "t5_mfe": 0.04,
        "t10_mae": -0.02,
        "t10_mfe": 0.05,
        "false_break_price_1d": 0,
        "false_break_close_1d": 0,
        "false_break_price_3d": 0,
        "false_break_close_3d": 0,
        "false_break_price_5d": 0,
        "false_break_close_5d": 0,
    }
    for index, field in enumerate(study.SIMILARITY_FEATURES):
        row[field] = value + index * 0.01
    return row


class DailyContractTests(unittest.TestCase):
    def test_direct_daily_requires_noadjust(self):
        raw = {
            "provider": "longbridge",
            "request": {
                "symbol": "600150.SH",
                "data_type": "daily",
                "period": "1d",
                "adjust_type": "none",
            },
        }
        study.validate_longbridge_daily_raw(raw)
        raw["request"]["adjust_type"] = "forward"
        with self.assertRaises(ValueError):
            study.validate_longbridge_daily_raw(raw)

    def test_duplicate_daily_date_is_rejected(self):
        sample = bars(2)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            study.validate_bars([sample[0], sample[0]])

    def test_unordered_daily_dates_are_rejected(self):
        sample = bars(2)
        with self.assertRaisesRegex(ValueError, "ordered"):
            study.validate_bars(list(reversed(sample)))

    def test_input_after_target_event_is_rejected(self):
        sample = bars(2, start=date(2026, 9, 4))
        with self.assertRaisesRegex(ValueError, "lookahead"):
            study.validate_bars(sample)

    def test_atr14_is_causal_sma_true_range(self):
        sample = [
            study.DailyBar(
                trading_date=date(2024, 1, 1) + timedelta(days=index),
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
                turnover=1000,
                provider_timestamp=index,
            )
            for index in range(15)
        ]
        rows = study.build_daily_features(sample)
        self.assertIsNone(rows[12]["atr14_sma"])
        self.assertEqual(rows[13]["atr14_sma"], 2)
        self.assertEqual(rows[14]["atr14_sma"], 2)

    def test_moving_average_and_features_have_no_future_dependency(self):
        sample = bars(280)
        short = study.build_daily_features(sample[:-1])
        full = study.build_daily_features(sample)
        self.assertEqual(short, full[:-1])

    def test_prior_high_excludes_current_day(self):
        sample = bars(21)
        final = sample[-1]
        sample[-1] = study.DailyBar(
            trading_date=final.trading_date,
            open=30,
            high=100,
            low=29,
            close=31,
            volume=final.volume,
            turnover=final.turnover,
            provider_timestamp=final.provider_timestamp,
        )
        rows = study.build_daily_features(sample)
        self.assertEqual(rows[-1]["prior20_high"], max(item.high for item in sample[:-1]))
        self.assertEqual(rows[-1]["breakout_20"], 1)

    def test_target_event_cannot_be_used_as_historical_outcome(self):
        sample = [
            study.DailyBar(date(2026, 9, 2), 10, 11, 9, 10, 100, 1000, 1),
            study.DailyBar(date(2026, 9, 3), 10, 12, 10, 11, 100, 1100, 2),
            study.DailyBar(date(2026, 9, 4), 11, 14, 11, 13, 100, 1300, 3),
        ]
        source = {
            "date": "2026-09-03",
            "breakout_20": 1,
            "breakout_40": 0,
            "breakout_60": 0,
            "prior20_high": 10.5,
            "prior40_high": 12,
            "prior60_high": 12,
        }
        outcome = study.add_outcomes([source], sample)[0]
        self.assertIsNone(outcome["t1_close_return"])
        self.assertIsNone(outcome["t1_open_gap"])


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            similarity_row("2024-01-01", "CALIBRATION", 0.0),
            similarity_row("2024-01-02", "CALIBRATION", 0.1),
            similarity_row("2026-01-01", "VALIDATION", 0.2),
        ]
        self.target = similarity_row("2026-09-04", "TARGET", 0.15)

    def test_outcomes_do_not_participate_in_similarity(self):
        first, _ = study.rank_similar_events(self.events, self.target)
        mutated = copy.deepcopy(self.events)
        for item in mutated:
            item["t5_close_return"] = -999
            item["t5_mae"] = -999
        second, _ = study.rank_similar_events(mutated, self.target)
        self.assertEqual(
            [(item["date"], item["similarity_distance"]) for item in first],
            [(item["date"], item["similarity_distance"]) for item in second],
        )

    def test_scaling_uses_calibration_only(self):
        _, first = study.rank_similar_events(self.events, self.target)
        mutated = copy.deepcopy(self.events)
        for field in study.SIMILARITY_FEATURES:
            mutated[-1][field] = 9999
        _, second = study.rank_similar_events(mutated, self.target)
        self.assertEqual(first["scaling"], second["scaling"])

    def test_opening_boundaries_ignore_validation_values(self):
        calibration = [
            similarity_row(f"2024-01-{index + 1:02d}", "CALIBRATION", index / 100)
            for index in range(6)
        ]
        validation = [
            similarity_row(f"2026-01-{index + 1:02d}", "VALIDATION", index / 100)
            for index in range(6)
        ]
        first = study.opening_study(calibration + validation, self.target)
        for item in validation:
            item["t1_open_gap"] = 50
        second = study.opening_study(calibration + validation, self.target)
        self.assertEqual(first["low_boundary"], second["low_boundary"])
        self.assertEqual(first["high_boundary"], second["high_boundary"])


@unittest.skipUnless(LOCAL_EVIDENCE, "local-only TASK_026B research evidence")
class PersistedEvidenceTests(unittest.TestCase):
    def test_target_event_is_verified_and_excluded_from_fitting(self):
        target = json.loads(study.TARGET_JSON.read_text(encoding="utf-8"))
        report = json.loads(study.STUDY_JSON.read_text(encoding="utf-8"))
        self.assertEqual(target["date"], "2026-09-04")
        self.assertEqual(target["breakout_signature"], "100")
        self.assertEqual(target["close"], 37.47)
        self.assertTrue(report["history"]["target_excluded_from_fitting"])
        self.assertEqual(report["lookahead"], "PASS")

    def test_same_input_produces_identical_outputs(self):
        study.analyze()
        paths = (study.TARGET_JSON, study.EVENTS_CSV, study.SIMILAR_CSV, study.STUDY_JSON, study.PLAYBOOK_JSON)
        first = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        study.analyze()
        second = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(first, second)

    def test_daily_cross_validation_reports_volume_conflict_explicitly(self):
        report = json.loads(study.DAILY_VALIDATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(report["price_and_date_status"], "PASS")
        self.assertEqual(report["volume_status"], "UNIT_SEMANTICS_UNRESOLVED")
        self.assertEqual(report["status"], "DATA_CONFLICT")
        self.assertEqual(report["sample_size"], 15)

    def test_historical_open_and_auction_fields_remain_distinct(self):
        report = json.loads(study.BRIDGE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PROVISIONAL_CONFIRMED")
        self.assertEqual(report["hithink_field_semantics"]["auction_price"], "竞价价格")
        self.assertEqual(report["longbridge_field"], "daily_open")

    def test_minute_fetch_is_bounded_to_similar_events(self):
        report = json.loads(study.MINUTE_STUDY_JSON.read_text(encoding="utf-8"))
        self.assertLessEqual(report["events_requested"], 30)
        self.assertEqual(report["scope"], "Top Similar Events only; T0 through T+2")
        self.assertEqual(report["feature_timing"], "POST_OPEN_PATH_STUDY_NOT_AVAILABLE_AT_09:25")


class OpeningCliTests(unittest.TestCase):
    def setUp(self):
        self.playbook = json.loads(study.PLAYBOOK_JSON.read_text(encoding="utf-8"))
        self.thesis = {
            "symbol": "600150.SH",
            "position": {
                "entry_date": "2026-09-04",
                "entry_price": 37.47,
                "shares": 100,
            },
        }
        self.observed = datetime(2026, 9, 7, 9, 32, tzinfo=SHANGHAI)
        self.calendar = {"data": {"item": [{"date": "20260907"}]}}
        self.auction = {
            "data": {
                "auction_phase": "closed",
                "data_status": "final",
                "item": [{"thscode": "600150.SH", "auction_price": 37.47}],
            }
        }

    def test_shadow_cli_never_writes_production_or_notifies(self):
        result = opening.evaluate(
            auction_raw=self.auction,
            calendar_raw=self.calendar,
            playbook=self.playbook,
            thesis=self.thesis,
            observed_at=self.observed,
        )
        self.assertEqual(result["mode"], "READ_ONLY_SHADOW")
        self.assertFalse(result["production_state_written"])
        self.assertFalse(result["notification_sent"])
        self.assertEqual(result["action"], "NO_SIGNAL")

    def test_not_ready_preserves_unknown_and_returns_no_signal(self):
        self.auction["data"]["data_status"] = "not_ready"
        result = opening.evaluate(
            auction_raw=self.auction,
            calendar_raw=self.calendar,
            playbook=self.playbook,
            thesis=self.thesis,
            observed_at=self.observed,
        )
        self.assertEqual(result["auction_status"], "DATA_NOT_READY")
        self.assertEqual(result["add_qualification"], "UNKNOWN")
        self.assertEqual(result["execute_at_auction"], "UNKNOWN")

    def test_absent_authoritative_calendar_date_fails_closed(self):
        result = opening.evaluate(
            auction_raw=self.auction,
            calendar_raw={"data": {"item": []}},
            playbook=self.playbook,
            thesis=self.thesis,
            observed_at=self.observed,
        )
        self.assertEqual(result["action"], "NO_SIGNAL")
        self.assertIn("CALENDAR", result["auction_status"])

    def test_target_day_cannot_be_reused_as_next_day_experiment(self):
        result = opening.evaluate(
            auction_raw=self.auction,
            calendar_raw={"data": {"item": [{"date": "20260904"}]}},
            playbook=self.playbook,
            thesis=self.thesis,
            observed_at=datetime(2026, 9, 4, 9, 32, tzinfo=SHANGHAI),
        )
        self.assertEqual(result["auction_status"], "TARGET_EVENT_NOT_YET_PRIOR_SESSION")


class ProductionBoundaryTests(unittest.TestCase):
    def test_frozen_rules_are_unchanged(self):
        for relative, expected in FROZEN_HASHES.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)

    def test_research_cli_has_no_runtime_or_notification_write_dependency(self):
        source = CLI_PATH.read_text(encoding="utf-8")
        self.assertNotIn("RuntimeStore", source)
        self.assertNotIn("NotificationService", source)
        self.assertNotIn("Bark", source)
        self.assertNotIn("LaunchAgent", source)


if __name__ == "__main__":
    unittest.main()
