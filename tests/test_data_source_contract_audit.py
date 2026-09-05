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

    def test_volume_unit_contract_is_confirmed_by_dimensional_invariant(self) -> None:
        contract = self.first["volume"]
        self.assertEqual(contract["status"], "CONFIRMED_EMPIRICALLY")
        self.assertEqual(
            contract["evidence_type"],
            "EMPIRICALLY_CONFIRMED_BY_DIMENSIONAL_INVARIANT",
        )
        self.assertEqual(
            contract["longbridge_cn_volume_scale"], "100_SHARES_PER_RAW_UNIT"
        )
        self.assertTrue(contract["auto_normalization_allowed"])
        self.assertFalse(contract["unknown_unit_auto_normalization_allowed"])
        self.assertTrue(
            all(
                not item["longbridge_invariant"]["factor_1_valid"]
                and item["longbridge_invariant"]["factor_100_valid"]
                for item in contract["samples"]
            )
        )
        self.assertEqual(
            contract["post_audit_resolution_600150"]["status"],
            "PASS_AFTER_UNIT_NORMALIZATION",
        )

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

    def test_legacy_snapshot_identity_is_preserved_and_new_contract_passes(self) -> None:
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
        self.assertEqual(self.first["snapshot"]["status"], "PASS")
        self.assertEqual(
            self.first["snapshot"]["legacy_2026_09_03_1500"],
            "LEGACY_SNAPSHOT_IDENTITY_MISMATCH",
        )

    def test_analysis_as_of_semantics(self) -> None:
        for trace in self.first["period_traces"]:
            self.assertEqual(trace["analysis_as_of"], trace["market_period_end"])
            self.assertEqual(trace["analysis_as_of_contract"], "PASS")

    def test_fallback_policy_audit(self) -> None:
        fallback = self.first["fallback"]
        self.assertEqual(fallback["status"], "CONFIRMED_BLOCKED")
        self.assertFalse(fallback["silent_fallback_found"])
        self.assertEqual(fallback["formal_cross_provider_fallbacks_allowed"], [])
        self.assertEqual(
            fallback["entries"][0]["status"],
            "BLOCKED_PENDING_CONTRACT_VALIDATION",
        )
        self.assertEqual(fallback["research_hithink_daily"], "ALLOWED_EXPLICITLY")

    def test_timezone_contract_is_confirmed_by_controlled_epoch_invariant(self) -> None:
        timezone_contract = self.first["timezone"]
        self.assertEqual(timezone_contract["status"], "PASS")
        self.assertEqual(
            timezone_contract["longbridge_naive_datetime_semantic"], "CONFIRMED"
        )
        self.assertTrue(
            all(item["status"] == "PASS" for item in timezone_contract["pair_checks"])
        )

    def test_task_025_and_risk_results_do_not_regress(self) -> None:
        regression = self.first["regression"]
        self.assertEqual(regression["status"], "PASS")
        self.assertEqual(regression["current_replay_match"], "PASS")
        self.assertEqual(regression["determinism"], "PASS")
        self.assertEqual(regression["lookahead"], "PASS")
        self.assertEqual(regression["disabled_previous_period_provenance"], "PASS")
        self.assertEqual(regression["period_end_1500"], "PASS")

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
