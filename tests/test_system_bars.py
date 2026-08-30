from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import (
    AssetType,
    MarketRecord,
    SourceQualityStatus,
    SourceTrace,
    SystemBarQualityStatus,
    SystemBarTransformation,
)
from trend_monitor.transformation import build_system_bars
from trend_monitor.validation import (
    classify_source_bar,
    reconcile_system_bars,
    validate_common_records,
    validate_source_minute_records,
)
from trend_monitor.validation.minute_structure import EXPECTED_TIMES


SHANGHAI = ZoneInfo("Asia/Shanghai")
TRACE = SourceTrace(
    provider="longbridge",
    provider_symbol="600487.SH",
    raw_path="data/raw/longbridge/15m/window.json",
    fetched_at="2026-08-29T00:00:00+00:00",
)


def minute_record(
    time_text: str,
    *,
    period: str,
    day: str = "2026-08-28",
    open_: float = 10.0,
    high: float = 11.0,
    low: float = 9.0,
    close: float = 10.5,
    volume: float = 100.0,
    turnover: float = 1000.0,
    trace: SourceTrace = TRACE,
) -> MarketRecord:
    timestamp = int(datetime.fromisoformat(f"{day}T{time_text}:00").replace(tzinfo=SHANGHAI).timestamp() * 1000)
    return MarketRecord(
        symbol="600487.SH",
        name="亨通光电",
        asset_type=AssetType.STOCK,
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        turnover=turnover,
        source="longbridge",
        period=period,
        source_trace=trace,
        instrument_id="stock.hengtong_optic",
        trade_session="Intraday",
    )


def complete_source(period: str) -> list[MarketRecord]:
    result = [minute_record(value, period=period) for value in EXPECTED_TIMES[period]]
    closing = result[-1]
    result[-1] = minute_record(
        "15:00",
        period=period,
        open_=12.0,
        high=12.0,
        low=12.0,
        close=12.0,
        volume=50.0,
        turnover=600.0,
    )
    return result


class SourceMinuteQualityTests(unittest.TestCase):
    def test_common_validator_remains_strict_for_0930_quirk(self):
        bar = minute_record("09:30", period="60m", open_=8.9, high=11, low=9)
        with self.assertRaises(TrendMonitorError) as raised:
            validate_common_records([bar])
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_0930_open_only_anomaly_is_source_boundary_quirk(self):
        bar = minute_record("09:30", period="60m", open_=8.9, high=11, low=9)
        assessment = classify_source_bar(bar)
        self.assertEqual(assessment.quality_status, SourceQualityStatus.SOURCE_BOUNDARY_QUIRK)
        self.assertEqual(validate_source_minute_records([bar])[0], assessment)

    def test_non_0930_ohlc_error_remains_invalid(self):
        bar = minute_record("10:30", period="60m", open_=8.9, high=11, low=9)
        self.assertEqual(classify_source_bar(bar).quality_status, SourceQualityStatus.INVALID)
        with self.assertRaises(TrendMonitorError) as raised:
            validate_source_minute_records([bar])
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_close_outside_range_at_0930_is_not_whitelisted(self):
        bar = minute_record("09:30", period="60m", high=10, low=9, close=11)
        self.assertEqual(classify_source_bar(bar).quality_status, SourceQualityStatus.INVALID)

    def test_lunch_boundary_is_invalid(self):
        with self.assertRaises(TrendMonitorError) as raised:
            validate_source_minute_records([minute_record("12:00", period="15m")])
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)


class SystemBarTests(unittest.TestCase):
    def test_60m_closing_bucket_merge_yields_four_bars_and_lineage(self):
        source = complete_source("60m")
        bars = build_system_bars(source, period="60m")
        self.assertEqual(len(bars), 4)
        final = bars[-1]
        self.assertEqual(final.open, 10.0)
        self.assertEqual(final.high, 12.0)
        self.assertEqual(final.low, 9.0)
        self.assertEqual(final.close, 12.0)
        self.assertEqual(final.volume, 150.0)
        self.assertEqual(final.turnover, 1600.0)
        self.assertEqual(final.transformation, SystemBarTransformation.MERGE_CLOSING_BUCKET)
        self.assertEqual(final.quality_status, SystemBarQualityStatus.MERGED_CLOSING_BUCKET)
        self.assertEqual(len(final.source_bar_ids), 2)
        self.assertEqual(final.source_raw_paths, (TRACE.raw_path,))

    def test_15m_closing_bucket_merge_yields_sixteen_bars(self):
        bars = build_system_bars(complete_source("15m"), period="15m")
        self.assertEqual(len(bars), 16)
        final_start = datetime.fromtimestamp(bars[-1].system_start / 1000, tz=SHANGHAI)
        final_end = datetime.fromtimestamp(bars[-1].system_end / 1000, tz=SHANGHAI)
        self.assertEqual(final_start.strftime("%H:%M"), "14:45")
        self.assertEqual(final_end.strftime("%H:%M"), "15:00")

    def test_boundary_quirk_is_propagated_without_mutating_source_ohlc(self):
        source = complete_source("60m")
        source[0] = minute_record("09:30", period="60m", open_=8.9, high=11, low=9)
        bars = build_system_bars(source, period="60m")
        self.assertEqual(bars[0].quality_status, SystemBarQualityStatus.SOURCE_BOUNDARY_QUIRK)
        self.assertEqual(
            bars[0].transformation,
            SystemBarTransformation.SOURCE_BOUNDARY_ENVELOPE,
        )
        self.assertEqual(bars[0].open, 8.9)
        self.assertEqual(bars[0].low, 8.9)

    def test_missing_closing_bucket_is_data_incomplete(self):
        with self.assertRaises(TrendMonitorError) as raised:
            build_system_bars(complete_source("60m")[:-1], period="60m")
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)

    def test_duplicate_bar_is_rejected(self):
        source = complete_source("60m")
        source.insert(1, source[0])
        with self.assertRaises(TrendMonitorError) as raised:
            build_system_bars(source, period="60m")
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_daily_reconciliation(self):
        source = complete_source("60m")
        bars = build_system_bars(source, period="60m")
        daily = minute_record(
            "00:00",
            period="1d",
            open_=10.0,
            high=12.0,
            low=9.0,
            close=12.0,
            volume=450.0,
            turnover=4600.0,
        )
        report = reconcile_system_bars(bars, [daily], period="60m")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["volume_relative_tolerance"], str(Decimal("0.001")))


if __name__ == "__main__":
    unittest.main()
