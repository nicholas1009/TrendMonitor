from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_data_source_contract", ROOT / "scripts" / "audit_data_source_contract.py"
)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@unittest.skipUnless(
    (ROOT / "data" / "runtime" / "reports").exists()
    and (
        ROOT
        / "audit"
        / "data_source_contract"
        / "local_evidence"
        / "volume_samples.json"
    ).exists(),
    "TASK_027 integration audit requires ignored local production evidence",
)
class DataSourceContractAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.first = audit.build_audit(write=False)
        cls.second = audit.build_audit(write=False)

    def test_source_trace_completeness(self) -> None:
        summary = self.first["summary"]
        self.assertEqual(summary["all"]["total"], summary["all"]["traceable"])
        self.assertEqual(summary["all"]["percent"], 100.0)
        self.assertEqual(len(self.first["period_traces"]), 8)

    def test_volume_unit_contract_remains_data_conflict(self) -> None:
        contract = self.first["volume"]
        self.assertEqual(contract["status"], "DATA_CONFLICT")
        self.assertEqual(contract["conversion_location"], "NONE")
        self.assertFalse(contract["auto_normalization_allowed"])

    def test_unknown_unit_must_not_auto_normalize(self) -> None:
        self.assertIsNone(
            audit.normalize_volume_if_documented(123.0, documented_unit="UNKNOWN")
        )
        self.assertIsNone(
            audit.normalize_volume_if_documented(123.0, documented_unit=None)
        )

    def test_provider_field_missing_remains_unknown(self) -> None:
        trace = audit.feature_trace(
            feature_name="volume",
            feature_value=123,
            instrument="stock.test",
            provider="",
            raw_references=(),
            risk_input_id=None,
            analysis_as_of="2026-09-04T10:30:00+08:00",
            market_period_end="2026-09-04T10:30:00+08:00",
        )
        self.assertEqual(trace["source_trace_status"], "UNKNOWN")

    def test_disabled_feature_lineage_semantics(self) -> None:
        disabled = audit.audit_feature_state(
            {
                "feature_name": "previous_period_close",
                "value": None,
                "eligibility": "DISABLED",
                "reason": "INSUFFICIENT_COMPLETED_BARS",
                "lineage": [],
            }
        )
        self.assertFalse(disabled["lineage_required"])
        self.assertEqual(disabled["status"], "PASS")

    def test_snapshot_identity_is_present_and_stable(self) -> None:
        first = [item["snapshot_identity"] for item in self.first["period_traces"]]
        second = [item["snapshot_identity"] for item in self.second["period_traces"]]
        self.assertEqual(first, second)
        self.assertTrue(all(len(value) == 64 for value in first))
        self.assertEqual(self.first["summary"]["snapshot_contract"], "FAIL")
        failing_periods = [
            item["period"]
            for item in self.first["period_traces"]
            if item["snapshot_contract"] == "FAIL"
        ]
        self.assertEqual(failing_periods, ["2026-09-03T15:00:00+08:00"])
        self.assertTrue(
            all(
                item["snapshot_contract"] == "PASS"
                for item in self.first["period_traces"]
                if item["period"].startswith("2026-09-04")
            )
        )

    def test_analysis_as_of_semantics(self) -> None:
        for trace in self.first["period_traces"]:
            self.assertEqual(trace["analysis_as_of"], trace["market_period_end"])
            self.assertEqual(trace["analysis_as_of_contract"], "PASS")

    def test_fallback_policy_audit(self) -> None:
        fallback = self.first["fallback"]
        self.assertEqual(fallback["status"], "PARTIAL")
        self.assertFalse(fallback["silent_fallback_found"])
        self.assertEqual(fallback["formal_cross_provider_fallbacks_allowed"], [])

    def test_audit_output_is_deterministic(self) -> None:
        self.assertEqual(
            audit.canonical_json(self.first), audit.canonical_json(self.second)
        )

    def test_no_lookahead(self) -> None:
        self.assertEqual(self.first["summary"]["lookahead"], "PASS")
        self.assertTrue(
            all(item["lookahead"] == "PASS" for item in self.first["period_traces"])
        )


if __name__ == "__main__":
    unittest.main()
