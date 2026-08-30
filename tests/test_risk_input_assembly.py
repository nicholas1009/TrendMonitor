from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.quality import RiskFeatureContract
from trend_monitor.registry import InstrumentRegistry
from trend_monitor.risk_input import RiskInputAssembler, RiskInputService, RiskInputSnapshotStore
from trend_monitor.schemas import (
    AssetType,
    DataType,
    FeatureEligibility,
    InstrumentRiskInputBundle,
    MarketRecord,
    PreflightStatus,
    ProviderDataResult,
    ProviderResultMetadata,
    RiskInputDataStatus,
    SourceTrace,
)
from trend_monitor.validation.minute_structure import EXPECTED_TIMES


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
CONTRACT = ROOT / "config" / "risk_feature_contract.json"


def symbol_for(instrument_id: str) -> str:
    return {
        "stock.hengtong_optic": "600487.SH",
        "stock.wus_printed_circuit": "002463.SZ",
        "index.csi500": "000905.SH",
        "index.star50": "000688.SH",
    }[instrument_id]


def source_records(
    period: str,
    *,
    instrument_id: str = "stock.hengtong_optic",
    asset_type: AssetType = AssetType.STOCK,
    day: str = "2026-08-28",
    boundary_quirk: bool = False,
) -> tuple[MarketRecord, ...]:
    symbol = symbol_for(instrument_id)
    trace = SourceTrace(
        provider="longbridge",
        provider_symbol=symbol,
        raw_path=f"data/raw/longbridge/{period}/{instrument_id}.json",
        fetched_at="2026-08-30T00:00:00+00:00",
    )
    result = []
    for index, label in enumerate(EXPECTED_TIMES[period]):
        local = datetime.fromisoformat(f"{day}T{label}:00").replace(tzinfo=SHANGHAI)
        close = 10.0 + index / 10
        open_ = close - 0.05
        high = close + 0.1
        low = close - 0.1
        if boundary_quirk and index == 0:
            open_, high, low, close = 8.9, 11.0, 9.0, 10.0
        if label == "15:00":
            open_ = high = low = close = 12.0
        result.append(
            MarketRecord(
                symbol=symbol,
                name="test",
                asset_type=asset_type,
                timestamp=int(local.timestamp() * 1000),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=100 + index,
                turnover=1000 + index,
                source="longbridge",
                period=period,
                source_trace=trace,
                instrument_id=instrument_id,
                trade_session="Intraday",
            )
        )
    return tuple(result)


def result_for(
    period: str,
    records: tuple[MarketRecord, ...],
    *,
    fallback: bool = False,
) -> ProviderDataResult:
    instrument_id = records[0].instrument_id or "unknown"
    symbol = records[0].symbol
    return ProviderDataResult(
        raw={"code": 0, "data": {"item": []}},
        normalized=records,
        metadata=ProviderResultMetadata(
            provider="longbridge",
            provider_symbol=symbol,
            instrument_id=instrument_id,
            fetched_at="2026-08-30T00:00:00+00:00",
            source_timestamp=records[-1].timestamp,
            data_type=DataType.DAILY if period == "1d" else DataType(period),
            mapping_type="EXACT",
            requested_provider="hithink" if fallback else "longbridge",
            actual_provider="longbridge",
            fallback_used=fallback,
            fallback_reason="hithink:UNSUPPORTED" if fallback else None,
            raw_path=records[0].source_trace.raw_path if records[0].source_trace else "",
        ),
    )


def daily_result() -> ProviderDataResult:
    trace = SourceTrace(
        provider="longbridge",
        provider_symbol="600487.SH",
        raw_path="data/raw/longbridge/daily/example.json",
        fetched_at="2026-08-30T00:00:00+00:00",
    )
    records = []
    for day, close in (("2026-08-27", 10.0), ("2026-08-28", 10.5)):
        local = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=SHANGHAI)
        records.append(
            MarketRecord(
                symbol="600487.SH",
                name="亨通光电",
                asset_type=AssetType.STOCK,
                timestamp=int(local.timestamp() * 1000),
                open=9.5,
                high=11,
                low=9,
                close=close,
                volume=1000,
                turnover=10000,
                source="longbridge",
                period="1d",
                source_trace=trace,
                instrument_id="stock.hengtong_optic",
            )
        )
    return result_for("1d", tuple(records))


class RiskInputAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.assembler = RiskInputAssembler(RiskFeatureContract.load(CONTRACT))
        self.as_of = datetime(2026, 8, 28, 16, 0, tzinfo=SHANGHAI)

    def assemble(self, period="60m", **kwargs):
        records = source_records(period, **kwargs)
        return self.assembler.assemble_minute(
            result_for(period, records),
            asset_type=kwargs.get("asset_type", AssetType.STOCK),
            period=period,
            as_of=self.as_of,
            trading_date=kwargs.get("day", "2026-08-28"),
        )

    def test_trusted_close_features_are_enabled(self):
        risk = self.assemble()
        enabled = {item.feature_name: item for item in risk.feature_inputs}
        for name in (
            "current_period_close", "previous_period_close", "close_change",
            "close_change_pct", "consecutive_close_direction", "close_repair",
        ):
            self.assertEqual(enabled[name].eligibility, FeatureEligibility.ENABLED)

    def test_approximate_high_low_cannot_be_exact_trigger(self):
        risk = self.assemble()
        disabled = {item.feature_name: item for item in risk.disabled_features}
        self.assertIn("precise_high_low_break", disabled)
        self.assertEqual(disabled["precise_high_low_break"].quality["high"], "APPROXIMATE")
        degraded = {item.feature_name for item in risk.degraded_features}
        self.assertIn("high_low_range_description", degraded)

    def test_index_volume_disabled_but_close_enabled(self):
        risk = self.assemble(
            instrument_id="index.csi500",
            asset_type=AssetType.INDEX,
        )
        self.assertIn("current_period_close", {item.feature_name for item in risk.feature_inputs})
        self.assertIn("index_volume_signal", {item.feature_name for item in risk.disabled_features})

    def test_stock_volume_and_turnover_are_degraded(self):
        risk = self.assemble()
        degraded = {item.feature_name for item in risk.degraded_features}
        self.assertIn("stock_volume_context", degraded)
        self.assertIn("turnover_context", degraded)

    def test_closing_bucket_close_is_transformed_and_traceable(self):
        risk = self.assemble()
        final = risk.system_bars[-1]
        self.assertEqual(final.transformation, "MERGE_CLOSING_BUCKET")
        self.assertEqual(final.field_quality["close"], "TRUSTED_WITH_TRANSFORMATION")
        close = next(item for item in risk.feature_inputs if item.feature_name == "current_period_close")
        self.assertEqual(close.lineage[0].transformation, "MERGE_CLOSING_BUCKET")
        self.assertEqual(len(close.lineage[0].source_bar_ids), 2)

    def test_source_boundary_quirk_degrades_only_high_low(self):
        records = source_records(
            "60m",
            instrument_id="index.csi500",
            asset_type=AssetType.INDEX,
            day="2026-08-05",
            boundary_quirk=True,
        )
        risk = self.assembler.assemble_minute(
            result_for("60m", records),
            asset_type=AssetType.INDEX,
            period="60m",
            as_of=datetime(2026, 8, 5, 16, 0, tzinfo=SHANGHAI),
            trading_date="2026-08-05",
        )
        self.assertEqual(risk.system_bars[0].transformation, "SOURCE_BOUNDARY_ENVELOPE")
        self.assertIn("current_period_close", {item.feature_name for item in risk.feature_inputs})
        self.assertIn("precise_high_low_break", {item.feature_name for item in risk.disabled_features})

    def test_preflight_pass_and_pass_with_degradation(self):
        daily = self.assembler.assemble_daily(
            daily_result(), asset_type=AssetType.STOCK, as_of=self.as_of
        )
        minute = self.assemble()
        self.assertEqual(daily.preflight_status, PreflightStatus.PASS)
        self.assertEqual(minute.preflight_status, PreflightStatus.PASS_WITH_DEGRADATION)

    def test_missing_closing_bucket_is_blocked_and_data_incomplete(self):
        records = source_records("60m")[:-1]
        risk = self.assembler.assemble_minute(
            result_for("60m", records),
            asset_type=AssetType.STOCK,
            period="60m",
            as_of=self.as_of,
            trading_date="2026-08-28",
        )
        self.assertEqual(risk.preflight_status, PreflightStatus.BLOCKED)
        self.assertEqual(risk.data_status, RiskInputDataStatus.DATA_INCOMPLETE)

    def test_missing_source_trace_and_lineage_are_blocked(self):
        records = tuple(replace(item, source_trace=None) for item in source_records("60m"))
        risk = self.assembler.assemble_minute(
            result_for("60m", records),
            asset_type=AssetType.STOCK,
            period="60m",
            as_of=self.as_of,
            trading_date="2026-08-28",
        )
        self.assertEqual(risk.preflight_status, PreflightStatus.BLOCKED)
        self.assertTrue(any("source trace" in item.lower() for item in risk.preflight_reasons))

    def test_in_progress_current_bar_is_excluded(self):
        records = source_records("60m")[:4]
        as_of = datetime(2026, 8, 28, 14, 20, tzinfo=SHANGHAI)
        risk = self.assembler.assemble_minute(
            result_for("60m", records),
            asset_type=AssetType.STOCK,
            period="60m",
            as_of=as_of,
            trading_date="2026-08-28",
        )
        self.assertEqual(len(risk.system_bars), 3)
        self.assertEqual(risk.preflight_status, PreflightStatus.PASS_WITH_DEGRADATION)
        self.assertEqual(len(risk.in_progress_source_bars), 1)
        self.assertLessEqual(risk.system_bars[-1].end, int(as_of.timestamp() * 1000))

    def test_daily_minute_derived_input_is_rejected(self):
        with self.assertRaises(TrendMonitorError) as raised:
            self.assembler.assemble_daily(
                daily_result(),
                asset_type=AssetType.STOCK,
                as_of=self.as_of,
                source_kind="minute_derived_daily",
            )
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_provider_fallback_trace_is_preserved(self):
        records = source_records("60m")
        risk = self.assembler.assemble_minute(
            result_for("60m", records, fallback=True),
            asset_type=AssetType.STOCK,
            period="60m",
            as_of=self.as_of,
        )
        self.assertTrue(risk.source_trace.fallback_used)
        self.assertEqual(risk.source_trace.requested_provider, "hithink")
        self.assertEqual(risk.source_trace.actual_provider, "longbridge")

    def test_all_provider_failure_blocks_entire_bundle(self):
        class FailingMarketData:
            registry = InstrumentRegistry.load(ROOT / "config" / "instruments.json")

            def get_daily(self, *args, **kwargs):
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "all daily providers failed")

            def get_bars(self, *args, **kwargs):
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "all minute providers failed")

        service = RiskInputService(FailingMarketData(), RiskFeatureContract.load(CONTRACT))
        bundle = service.build_bundle(
            "stock.hengtong_optic",
            as_of=self.as_of,
            requested_provider="longbridge",
            fallback_providers=("hithink",),
        )
        self.assertEqual(bundle.preflight_status, PreflightStatus.BLOCKED)
        self.assertEqual(bundle.data_status, RiskInputDataStatus.DATA_INCOMPLETE)
        self.assertEqual(bundle.daily.preflight_status, PreflightStatus.BLOCKED)

    def test_service_requests_only_bounded_latest_minute_windows(self):
        requested_counts = {}

        class RecordingFailureMarketData:
            registry = InstrumentRegistry.load(ROOT / "config" / "instruments.json")

            def get_daily(self, *args, **kwargs):
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "daily unavailable")

            def get_bars(self, instrument_id, provider, *, period, count, **kwargs):
                requested_counts[period] = count
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "minute unavailable")

        service = RiskInputService(
            RecordingFailureMarketData(), RiskFeatureContract.load(CONTRACT)
        )
        service.build_bundle(
            "stock.hengtong_optic",
            as_of=self.as_of,
            requested_provider="longbridge",
        )
        self.assertEqual(requested_counts, {"60m": 11, "15m": 35})

    def test_known_runtime_anomalies_disable_fields_not_close(self):
        cases = (
            ("stock.wus_printed_circuit", AssetType.STOCK, "2026-08-06", "stock_volume_context"),
            ("index.csi500", AssetType.INDEX, "2026-08-07", "index_volume_signal"),
            ("index.star50", AssetType.INDEX, "2026-08-21", "turnover_context"),
        )
        for instrument_id, asset_type, day, disabled_name in cases:
            with self.subTest(instrument_id=instrument_id, day=day):
                records = source_records("60m", instrument_id=instrument_id, asset_type=asset_type, day=day)
                risk = self.assembler.assemble_minute(
                    result_for("60m", records),
                    asset_type=asset_type,
                    period="60m",
                    as_of=datetime.fromisoformat(f"{day}T16:00:00").replace(tzinfo=SHANGHAI),
                    trading_date=day,
                )
                self.assertIn("current_period_close", {item.feature_name for item in risk.feature_inputs})
                self.assertIn(disabled_name, {item.feature_name for item in risk.disabled_features})

    def test_snapshot_round_trip_and_no_overwrite(self):
        daily = self.assembler.assemble_daily(daily_result(), asset_type=AssetType.STOCK, as_of=self.as_of)
        minute = self.assemble()
        bundle = InstrumentRiskInputBundle(
            instrument_id="stock.hengtong_optic",
            asset_type=AssetType.STOCK,
            as_of=self.as_of.isoformat(),
            daily=daily,
            risk_60m=minute,
            support_15m=self.assemble(period="15m"),
            data_status=RiskInputDataStatus.DEGRADED,
            preflight_status=PreflightStatus.PASS_WITH_DEGRADATION,
            reasons=("degraded:60M", "degraded:15M"),
        )
        with TemporaryDirectory() as directory:
            store = RiskInputSnapshotStore(directory)
            first = store.save_bundle(bundle)
            second = store.save_bundle(bundle)
            self.assertNotEqual(first, second)
            self.assertEqual(store.load(first), bundle.to_dict())
            self.assertTrue(Path(first).exists())


if __name__ == "__main__":
    unittest.main()
