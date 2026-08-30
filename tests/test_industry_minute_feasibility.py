from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.industry_feasibility import (
    IndustryMinuteFeasibilityRules,
    build_feasibility_result,
    classify_tushare_error,
    credential_available,
    redact_sensitive,
)
from trend_monitor.schemas import BoundarySnapshotClose


ROOT = Path(__file__).resolve().parents[1]
RULES = IndustryMinuteFeasibilityRules.load(ROOT / "config" / "industry_minute_feasibility.json")
NOW = datetime(2026, 8, 30, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class IdentityTests(unittest.TestCase):
    def test_canonical_and_proxy_identity_are_separate(self):
        for instrument_id, canonical in RULES.raw["canonical_benchmarks"].items():
            proxy = RULES.raw["minute_proxy_candidates"][instrument_id]
            self.assertEqual((canonical["provider"], canonical["taxonomy"]), ("hithink", "THS"))
            self.assertEqual((proxy["provider"], proxy["taxonomy"]), ("tushare", "SW2021"))
            self.assertNotEqual(canonical["provider_symbol"], proxy["provider_symbol"])

    def test_cross_taxonomy_cannot_masquerade_as_exact(self):
        raw = deepcopy(RULES.raw)
        raw["minute_proxy_candidates"]["stock.hengtong_optic"]["mapping_type"] = "EXACT"
        with self.assertRaises(ValueError):
            IndustryMinuteFeasibilityRules(raw)

    def test_proxy_mapping_is_candidate_not_activated(self):
        result = build_feasibility_result(
            RULES, project_root=ROOT, evaluated_at=NOW, credential_present=False
        )
        for candidate in result.minute_proxy_candidates.values():
            self.assertEqual(candidate["mapping_type"], "CANDIDATE_PROXY")
            self.assertEqual(candidate["activation"], "DISABLED")

    def test_synthetic_scheme_is_rejected(self):
        raw = deepcopy(RULES.raw)
        raw["synthetic_benchmark_allowed"] = True
        with self.assertRaises(ValueError):
            IndustryMinuteFeasibilityRules(raw)


class CredentialTests(unittest.TestCase):
    def test_credential_detection_never_returns_value(self):
        self.assertTrue(credential_available(environ={"TUSHARE_TOKEN": "secret"}))
        self.assertFalse(credential_available(environ={"TUSHARE_TOKEN": ""}))
        with TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("TUSHARE_TOKEN=hidden-value\n", encoding="utf-8")
            self.assertTrue(credential_available(environ={}, dotenv_path=dotenv))

    def test_credential_redaction(self):
        secret = "abc123-secret"
        output = redact_sensitive(
            {"message": f"failed token={secret}", "TUSHARE_TOKEN": secret}, secrets=(secret,)
        )
        self.assertNotIn(secret, str(output))
        self.assertEqual(output["TUSHARE_TOKEN"], "[REDACTED]")

    def test_permission_error_is_structured_and_redacted(self):
        secret = "token-secret"
        result = classify_tushare_error(
            f"权限不足 token={secret}",
            endpoint="sw_mins",
            ts_code="801102.SI",
            freq="15min",
            secrets=(secret,),
        )
        self.assertEqual(result["status"], "BLOCKED_BY_TUSHARE_PERMISSION")
        self.assertNotIn(secret, result["message"])


class BoundaryAndImmutabilityTests(unittest.TestCase):
    def test_boundary_snapshot_schema_and_timestamp(self):
        snapshot = BoundarySnapshotClose(
            requested_boundary="15:00",
            provider_trade_time="2026-08-28T15:00:00+08:00",
            fetched_at="2026-08-28T15:00:03+08:00",
            close=1234.5,
            source_provider="tushare",
            source_raw_path="data/raw/tushare/rt_sw_k.json",
            delay_seconds=3.0,
        )
        self.assertEqual(snapshot.to_dict()["source_type"], "BOUNDARY_SNAPSHOT_CLOSE")

    def test_direct_bar_label_is_rejected(self):
        snapshot = BoundarySnapshotClose(
            requested_boundary="15:00",
            provider_trade_time="2026-08-28T15:00:00+08:00",
            fetched_at="2026-08-28T15:00:03+08:00",
            close=1234.5,
            source_provider="tushare",
            source_raw_path="raw.json",
            delay_seconds=3.0,
            source_type="DIRECT_60M_BAR",
        )
        with self.assertRaises(ValueError):
            snapshot.validate()

    def test_no_stock_score_modification_and_deterministic(self):
        first = build_feasibility_result(
            RULES, project_root=ROOT, evaluated_at=NOW, credential_present=False
        )
        second = build_feasibility_result(
            RULES, project_root=ROOT, evaluated_at=NOW, credential_present=False
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertFalse(first.stock_score_modified)
        self.assertEqual(first.final_judgment, "BLOCKED_BY_PERMISSION")
        self.assertEqual(first.credential_status, "BLOCKED_BY_TUSHARE_CREDENTIALS")


if __name__ == "__main__":
    unittest.main()
