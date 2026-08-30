from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.comparison import (
    aggregate_one_minute,
    compare_diagnostic_bars,
    direct_records_as_diagnostic,
)
from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.quality import (
    HardBlockContext,
    RiskEngineReadiness,
    RiskFeatureContract,
    annotate_system_bar,
    evaluate_risk_input,
)
from trend_monitor.schemas import (
    AssetType,
    FieldQuality,
    MarketRecord,
    SourceTrace,
    SystemBar,
    SystemBarQualityStatus,
    SystemBarTransformation,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "risk_feature_contract.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")
TRACE = SourceTrace(
    provider="longbridge",
    provider_symbol="600487.SH",
    raw_path="data/raw/longbridge/1m/window.json",
    fetched_at="2026-08-29T00:00:00+00:00",
)


def one_minute_day() -> list[MarketRecord]:
    ranges = (
        (datetime(2026, 8, 28, 9, 30, tzinfo=SHANGHAI), 120),
        (datetime(2026, 8, 28, 13, 0, tzinfo=SHANGHAI), 121),
    )
    result = []
    index = 0
    for start, count in ranges:
        for offset in range(count):
            current = start + timedelta(minutes=offset)
            price = Decimal("10") + Decimal(index) / Decimal("100")
            result.append(
                MarketRecord(
                    symbol="600487.SH",
                    name="亨通光电",
                    asset_type=AssetType.STOCK,
                    timestamp=int(current.timestamp() * 1000),
                    open=float(price),
                    high=float(price + Decimal("0.02")),
                    low=float(price - Decimal("0.01")),
                    close=float(price + Decimal("0.01")),
                    volume=1.0,
                    turnover=10.0,
                    source="longbridge",
                    period="1m",
                    source_trace=TRACE,
                    instrument_id="stock.hengtong_optic",
                    trade_session="Intraday",
                )
            )
            index += 1
    return result


def system_bar(
    *,
    instrument_id="stock.hengtong_optic",
    period="60m",
    day="2026-08-28",
    transformation=SystemBarTransformation.DIRECT_NORMALIZED,
) -> SystemBar:
    start = int(datetime.fromisoformat(f"{day}T14:00:00").replace(tzinfo=SHANGHAI).timestamp() * 1000)
    return SystemBar(
        instrument_id=instrument_id,
        period=period,
        system_start=start,
        system_end=start + 3_600_000,
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100,
        turnover=1000,
        source_provider="longbridge",
        source_bar_ids=("longbridge:symbol:60m:1",),
        source_raw_paths=("data/raw/longbridge/60m/example.json",),
        transformation=transformation,
        quality_status=SystemBarQualityStatus.DIRECT_NORMALIZED,
    )


class CrossPeriodTests(unittest.TestCase):
    def test_one_minute_diagnostic_aggregation_has_expected_bar_counts(self):
        records = one_minute_day()
        derived_15 = aggregate_one_minute(records, target_period="15m")
        derived_60 = aggregate_one_minute(records, target_period="60m")
        derived_daily = aggregate_one_minute(records, target_period="1d")
        self.assertEqual(len(derived_15), 17)
        self.assertEqual(len(derived_60), 5)
        self.assertEqual(len(derived_daily), 1)
        self.assertEqual(derived_15[-1].source_bar_ids[-1].split(":")[-1], str(records[-1].timestamp))
        self.assertEqual(derived_daily[0].volume, Decimal("241.0"))

    def test_error_distribution_reports_frequency_and_percentiles(self):
        left = aggregate_one_minute(one_minute_day(), target_period="15m")
        right = direct_records_as_diagnostic([
            MarketRecord(
                symbol="600487.SH",
                name="亨通光电",
                asset_type=AssetType.STOCK,
                timestamp=item.timestamp,
                open=float(item.open),
                high=float(item.high),
                low=float(item.low),
                close=float(item.close),
                volume=float(item.volume),
                turnover=float(item.turnover),
                source="longbridge",
                period="15m",
                source_trace=TRACE,
                instrument_id="stock.hengtong_optic",
                trade_session="Intraday",
            )
            for item in left
        ])
        report = compare_diagnostic_bars(left, right)
        self.assertEqual(report["fields"]["close"]["mismatch_count"], 0)
        self.assertIn("p99", report["fields"]["volume"]["relative_difference"])


class RiskContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = RiskFeatureContract.load(CONTRACT_PATH)

    def test_field_quality_enum_and_daily_direct_protection(self):
        self.assertEqual(FieldQuality("TRUSTED"), FieldQuality.TRUSTED)
        self.assertEqual(self.contract.formal_daily["source_requirement"], "DIRECT")
        self.assertFalse(self.contract.formal_daily["minute_derived_daily_allowed"])

    def test_close_only_features_survive_high_low_degradation(self):
        annotated, _ = annotate_system_bar(
            system_bar(), asset_type=AssetType.STOCK, contract=self.contract
        )
        assessment = evaluate_risk_input(
            annotated,
            asset_type=AssetType.STOCK,
            contract=self.contract,
        )
        enabled = {item.feature for item in assessment.features if item.enabled}
        disabled = {item.feature for item in assessment.feature_disabled}
        self.assertIn("period_close_change", enabled)
        self.assertIn("stock_volume_context", enabled)
        self.assertIn("precise_high_low_break", disabled)
        self.assertEqual(assessment.readiness, RiskEngineReadiness.YES_WITH_LIMITS)

    def test_index_volume_is_disabled_but_close_price_is_enabled(self):
        annotated, _ = annotate_system_bar(
            system_bar(instrument_id="index.csi500"),
            asset_type=AssetType.INDEX,
            contract=self.contract,
        )
        assessment = evaluate_risk_input(
            annotated, asset_type=AssetType.INDEX, contract=self.contract
        )
        decisions = {item.feature: item for item in assessment.features}
        self.assertTrue(decisions["period_close_change"].enabled)
        self.assertFalse(decisions["index_volume_signal"].enabled)
        self.assertEqual(decisions["index_volume_signal"].affected_fields, ("volume",))

    def test_runtime_anomaly_blocks_volume_without_blocking_close(self):
        annotated, reasons = annotate_system_bar(
            system_bar(
                instrument_id="stock.wus_printed_circuit",
                day="2026-08-06",
            ),
            asset_type=AssetType.STOCK,
            contract=self.contract,
        )
        self.assertIn("closing_bucket_daily_reconciliation_anomaly", reasons)
        self.assertEqual(annotated.field_quality.volume, FieldQuality.BLOCKED)
        assessment = evaluate_risk_input(
            annotated,
            asset_type=AssetType.STOCK,
            contract=self.contract,
            quality_reasons=reasons,
        )
        decisions = {item.feature: item for item in assessment.features}
        self.assertTrue(decisions["period_close_change"].enabled)
        self.assertFalse(decisions["stock_volume_context"].enabled)
        self.assertIn(
            "closing_bucket_daily_reconciliation_anomaly",
            decisions["stock_volume_context"].reason,
        )
        self.assertIn(
            "closing_bucket_daily_reconciliation_anomaly",
            assessment.quality_reasons,
        )

    def test_known_index_anomaly_dates_apply_field_specific_degradation(self):
        csi, csi_reasons = annotate_system_bar(
            system_bar(instrument_id="index.csi500", day="2026-08-07"),
            asset_type=AssetType.INDEX,
            contract=self.contract,
        )
        star, star_reasons = annotate_system_bar(
            system_bar(instrument_id="index.star50", day="2026-08-21"),
            asset_type=AssetType.INDEX,
            contract=self.contract,
        )
        self.assertEqual(csi.field_quality.volume, FieldQuality.BLOCKED)
        self.assertEqual(star.field_quality.volume, FieldQuality.BLOCKED)
        self.assertEqual(star.field_quality.turnover, FieldQuality.BLOCKED)
        self.assertIn("minute_daily_volume_near_double", csi_reasons)
        self.assertIn("minute_daily_volume_turnover_anomaly", star_reasons)

    def test_opening_boundary_envelope_is_visible_and_high_low_stay_approximate(self):
        original = system_bar(
            instrument_id="index.csi500",
            day="2026-08-05",
            transformation=SystemBarTransformation.SOURCE_BOUNDARY_ENVELOPE,
        )
        annotated, reasons = annotate_system_bar(
            original, asset_type=AssetType.INDEX, contract=self.contract
        )
        self.assertEqual(annotated.field_quality.high, FieldQuality.APPROXIMATE)
        self.assertEqual(annotated.field_quality.low, FieldQuality.APPROXIMATE)
        self.assertIn("SOURCE_BOUNDARY_ENVELOPE", reasons)
        self.assertEqual(annotated.source_bar_ids, original.source_bar_ids)

    def test_closing_bucket_close_is_trusted_with_transformation_and_lineage_remains(self):
        original = system_bar(transformation=SystemBarTransformation.MERGE_CLOSING_BUCKET)
        annotated, reasons = annotate_system_bar(
            original, asset_type=AssetType.STOCK, contract=self.contract
        )
        self.assertEqual(
            annotated.field_quality.close,
            FieldQuality.TRUSTED_WITH_TRANSFORMATION,
        )
        self.assertEqual(annotated.source_raw_paths, original.source_raw_paths)
        self.assertIn("MERGE_CLOSING_BUCKET", reasons)

    def test_hard_block_stops_all_features(self):
        annotated, _ = annotate_system_bar(
            system_bar(), asset_type=AssetType.STOCK, contract=self.contract
        )
        assessment = evaluate_risk_input(
            annotated,
            asset_type=AssetType.STOCK,
            contract=self.contract,
            hard_blocks=HardBlockContext(bar_count_incomplete=True),
        )
        self.assertEqual(assessment.readiness, RiskEngineReadiness.NO)
        self.assertEqual(assessment.data_status, ErrorCategory.DATA_INCOMPLETE.value)
        self.assertTrue(all(not item.enabled for item in assessment.features))
        self.assertEqual(assessment.hard_block_reasons, ("bar_count_incomplete",))

    def test_contract_rejects_minute_derived_daily_substitution(self):
        value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        value["formal_daily"]["minute_derived_daily_allowed"] = True
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(TrendMonitorError) as raised:
                RiskFeatureContract.load(path)
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)


if __name__ == "__main__":
    unittest.main()
