from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
STUDY_PATH = ROOT / "research" / "livermore" / "002463" / "study.py"
SPEC = importlib.util.spec_from_file_location("task017_study", STUDY_PATH)
assert SPEC is not None and SPEC.loader is not None
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)

HENGTONG_STUDY_PATH = ROOT / "research" / "livermore" / "600487" / "study.py"
HENGTONG_SPEC = importlib.util.spec_from_file_location("task020_hengtong_study", HENGTONG_STUDY_PATH)
assert HENGTONG_SPEC is not None and HENGTONG_SPEC.loader is not None
hengtong = importlib.util.module_from_spec(HENGTONG_SPEC)
sys.modules[HENGTONG_SPEC.name] = hengtong
HENGTONG_SPEC.loader.exec_module(hengtong)


FROZEN_HASHES = {
    "config/market_60m_risk_rules.json": "0c001733e3986e73bbbe484e40cb483e7705cd2233959a5796146002e89fce82",
    "config/market_15m_internal_rules.json": "1ffd30195c6541b42dd92f9133cd5be422f5c4b38ec3bcd92a690fa9a4619d0d",
    "config/stock_intraday_risk_rules.json": "7fd2ec0b7670ce225ffe6df038967fcf2130bc46e7f517a8d2f84ad47097dd50",
    "config/risk_feature_contract.json": "0871e472babc57c1dc7085710933cbd2dfbf09b06d5308cebff1d1061c2f8528",
    "config/notification_policy.json": "e2aef111ecc0dfc21420ca98a189f64b30886cbd746542f262098cf9481405bc",
}
LEGACY_REGION_SHA256 = "51701d0528d4d19d2777f9933cd68dc75a1552e8b326dd2c666361b86c522ce6"
LOCAL_DAILY_EVIDENCE = study.DAILY_CSV.is_file() and hengtong.DAILY_CSV.is_file()
LOCAL_WORKBOOK_EVIDENCE = (ROOT / "legacy" / "A股价格趋势记录.xlsx").is_file()


def bar(index: int, close: str, *, high: str | None = None, low: str | None = None):
    value = Decimal(close)
    return study.DailyBar(
        trading_date=date(2024, 1, 1) + timedelta(days=index),
        open=value,
        high=Decimal(high) if high else value + Decimal("1"),
        low=Decimal(low) if low else value - Decimal("1"),
        close=value,
        volume=100,
        turnover=Decimal("1000"),
        provider_timestamp=1_700_000_000 + index * 86400,
        atr14_sma=Decimal("2"),
    )


class LivermoreResearchTests(unittest.TestCase):
    def test_direct_daily_contract(self):
        raw = {
            "provider": "longbridge",
            "request": {
                "symbol": "002463.SZ", "data_type": "daily",
                "period": "1d", "adjust_type": "none",
            },
        }
        study.validate_direct_daily_contract(raw)
        raw["request"]["period"] = "60m"
        with self.assertRaises(ValueError):
            study.validate_direct_daily_contract(raw)

    def test_direct_daily_contract_accepts_explicit_research_symbol(self):
        raw = {
            "provider": "longbridge",
            "request": {
                "symbol": "600487.SH", "data_type": "daily",
                "period": "1d", "adjust_type": "none",
            },
        }
        study.validate_direct_daily_contract(raw, symbol="600487.SH")

    def test_atr14_is_sma_of_causal_true_ranges(self):
        bars = [bar(i, "10", high="11", low="9") for i in range(15)]
        result = study.with_atr14_sma(bars)
        self.assertIsNone(result[12].atr14_sma)
        self.assertEqual(result[13].atr14_sma, Decimal("2"))
        self.assertEqual(result[14].atr14_sma, Decimal("2"))

    def test_future_bar_cannot_change_prior_states(self):
        bars = [bar(i, value) for i, value in enumerate(("10", "9", "8", "7", "12", "13"))]
        first = study.replay_natural_moves(bars, Decimal("2"), initial_state=study.DOWNWARD_TREND)
        extended = study.replay_natural_moves(
            bars + [bar(6, "1", high="2", low="1")],
            Decimal("2"), initial_state=study.DOWNWARD_TREND,
        )
        self.assertEqual(first.states, extended.states[:len(first.states)])
        self.assertEqual(first.transitions, tuple(
            item for item in extended.transitions if item.trading_date <= bars[-1].trading_date
        ))

    def test_k_is_independent_and_changes_trigger_timing(self):
        bars = [bar(i, value) for i, value in enumerate(("10", "9", "8", "7", "10", "12", "14"))]
        fast = study.replay_natural_moves(bars, Decimal("1"), initial_state=study.DOWNWARD_TREND)
        slow = study.replay_natural_moves(bars, Decimal("3"), initial_state=study.DOWNWARD_TREND)
        self.assertNotEqual(fast.transitions[0].trading_date, slow.transitions[0].trading_date)

    @unittest.skipUnless(LOCAL_DAILY_EVIDENCE and LOCAL_WORKBOOK_EVIDENCE, "local-only Livermore evidence")
    def test_k2_replays_legacy_transition_without_lookahead(self):
        bars = study.with_atr14_sma(study.load_daily_input())
        legacy, parameters = study.load_legacy_records()
        result = study.legacy_comparison(bars, Decimal("2.0"), legacy, parameters)
        self.assertEqual(result["detected_transition_date"], "2026-08-05")
        self.assertEqual(result["anchor_low"], "94.73")
        self.assertEqual(result["atr14_sma_3dp"], "10.431")
        self.assertEqual(result["threshold_legacy_3dp_atr"], "115.592")
        self.assertEqual(result["state_matches"], result["state_records"])
        self.assertEqual(result["legacy_replay"], "PASS")

    @unittest.skipUnless(LOCAL_DAILY_EVIDENCE, "local-only Livermore evidence")
    def test_same_input_is_deterministic(self):
        bars = study.with_atr14_sma(study.load_daily_input())
        first = study.replay_natural_moves(bars, Decimal("2.0"))
        second = study.replay_natural_moves(bars, Decimal("2.0"))
        self.assertEqual(first, second)

    @unittest.skipUnless(LOCAL_DAILY_EVIDENCE and LOCAL_WORKBOOK_EVIDENCE, "local-only Livermore evidence")
    def test_hengtong_k2_replays_current_record_without_lookahead(self):
        bars = study.with_atr14_sma(hengtong.load_daily_input())
        replay = study.replay_natural_moves(bars, Decimal("2.0"))
        records = hengtong.load_current_records()
        result = hengtong.current_record_comparison(replay, records)
        self.assertEqual(result["state_matches"], 46)
        self.assertEqual(result["state_records"], 46)
        self.assertEqual(result["transition_offsets_trading_days"], "0;0;0;0")
        self.assertEqual(result["current_record_replay"], "PASS")

        cutoff = date(2026, 8, 19)
        partial = study.replay_natural_moves(
            [item for item in bars if item.trading_date <= cutoff],
            Decimal("2.0"),
        )
        self.assertEqual(
            tuple(item for item in replay.states if item.trading_date <= cutoff),
            partial.states,
        )

    @unittest.skipUnless(LOCAL_DAILY_EVIDENCE, "local-only Livermore evidence")
    def test_hengtong_sensitivity_evidence_is_persisted(self):
        with hengtong.SENSITIVITY_CSV.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        by_k = {row["k"]: row for row in rows}
        self.assertEqual(tuple(by_k), tuple(str(item) for item in study.K_VALUES))
        self.assertGreater(int(by_k["1.0"]["whipsaw_10d"]), int(by_k["2.0"]["whipsaw_10d"]))
        self.assertGreater(
            Decimal(by_k["3.0"]["validation_median_detection_delay"]),
            Decimal(by_k["2.0"]["validation_median_detection_delay"]),
        )
        self.assertEqual(by_k["2.0"]["current_record_replay"], "PASS")
        manifest = json.loads(hengtong.SOURCE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["symbol"], "600487.SH")
        self.assertEqual(manifest["daily_contract"], "DIRECT_DAILY")
        self.assertEqual(manifest["adjust_type"], "none")
        self.assertEqual(
            manifest["daily_input_sha256"],
            hashlib.sha256(hengtong.DAILY_CSV.read_bytes()).hexdigest(),
        )

    def test_only_natural_move_k_changes_replay(self):
        self.assertEqual(
            tuple(str(item) for item in study.K_VALUES),
            ("1.0", "1.25", "1.5", "1.75", "2.0", "2.25", "2.5", "2.75", "3.0"),
        )
        self.assertNotIn("exit_trailing_k", STUDY_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("pivot_confirm_k", STUDY_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("secondary_move_k", STUDY_PATH.read_text(encoding="utf-8"))

    @unittest.skipUnless(LOCAL_DAILY_EVIDENCE, "local-only Livermore evidence")
    def test_offline_input_is_direct_daily_no_adjust(self):
        bars = study.load_daily_input()
        self.assertGreaterEqual(len(bars), 500)
        self.assertEqual(bars[0].trading_date, date(2023, 8, 1))
        self.assertEqual(bars[-1].trading_date, date(2026, 9, 1))

    @unittest.skipUnless(LOCAL_DAILY_EVIDENCE and LOCAL_WORKBOOK_EVIDENCE, "local-only Livermore evidence")
    def test_hengtong_offline_input_and_workbook_presentation(self):
        bars = hengtong.load_daily_input()
        self.assertEqual(len(bars), 750)
        self.assertEqual(bars[0].trading_date, date(2023, 8, 1))
        self.assertEqual(bars[-1].trading_date, date(2026, 9, 2))

        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        forbidden = (
            "current state", "last update", "current extreme", "natural_move_k",
            "stable_region", "research_sample", "last_research_date", "exit_trailing_k",
            "reentry_buffer_k", "pivot_confirm_k", "secondary_move_k",
            "BASELINE_UNVALIDATED", "FORMAL_CURRENT_NOT_SEPARATELY_OPTIMIZED", "TBD",
        )
        with ZipFile(ROOT / "legacy" / "A股价格趋势记录.xlsx") as archive:
            sheet_paths = sorted(
                item for item in archive.namelist()
                if item.startswith("xl/worksheets/sheet") and item.endswith(".xml")
            )
            self.assertEqual(len(sheet_paths), 3)
            visible_text: list[str] = []
            for path in sheet_paths:
                root = ET.fromstring(archive.read(path))
                visible_text.extend(node.text or "" for node in root.iter(f"{{{main}}}t"))
                self.assertEqual(len(list(root.iter(f"{{{main}}}conditionalFormatting"))), 6)
        joined = "\n".join(visible_text)
        self.assertFalse([token for token in forbidden if token in joined])

    @unittest.skipUnless(LOCAL_WORKBOOK_EVIDENCE, "local-only Livermore evidence")
    def test_legacy_and_frozen_contracts_are_unchanged(self):
        legacy, _ = study.load_legacy_records()
        canonical = "\n".join(
            f"{item.trading_date.isoformat()}|{item.state}|{item.price.quantize(Decimal('0.00'))}"
            for item in legacy
        ) + "\n"
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), LEGACY_REGION_SHA256)
        for relative, expected in FROZEN_HASHES.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)


if __name__ == "__main__":
    unittest.main()
