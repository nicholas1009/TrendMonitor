from datetime import datetime, timedelta, timezone
import unittest

from trend_monitor.comparison import (
    ComparisonConfig,
    ComparisonStatus,
    VolumeComparison,
    compare_daily_records,
)
from trend_monitor.schemas import AssetType, MarketRecord


def daily(day: int, close: float, source: str) -> MarketRecord:
    timestamp = int((datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=day)).timestamp() * 1000)
    return MarketRecord(
        symbol="600487.SH",
        name="亨通光电",
        asset_type=AssetType.STOCK,
        timestamp=timestamp,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000,
        turnover=10000,
        source=source,
        period="1d",
        instrument_id="stock.hengtong_optic",
    )


class DailyComparisonTests(unittest.TestCase):
    def config(self, threshold=None, minimum=2):
        return ComparisonConfig(
            minimum_common_days=minimum,
            price_relative_tolerance=threshold,
            mode="REVIEW_REQUIRED",
            rationale="test rationale",
        )

    def test_exact_prices_match_but_volume_unit_stays_unknown(self):
        left = [daily(1, 10, "hithink"), daily(2, 11, "hithink")]
        right = [daily(1, 10, "longbridge"), daily(2, 11, "longbridge")]
        report = compare_daily_records(
            left,
            right,
            instrument_id="stock.hengtong_optic",
            left_provider="hithink",
            right_provider="longbridge",
            left_adjustment="none",
            right_adjustment="none",
            config=self.config(),
        )
        self.assertEqual(report.status, ComparisonStatus.MATCH)
        self.assertEqual(report.volume_comparison, VolumeComparison.UNIT_UNKNOWN)

    def test_no_configured_threshold_requires_review(self):
        report = compare_daily_records(
            [daily(1, 10, "hithink"), daily(2, 11, "hithink")],
            [daily(1, 10.1, "longbridge"), daily(2, 11, "longbridge")],
            instrument_id="stock.hengtong_optic",
            left_provider="hithink",
            right_provider="longbridge",
            left_adjustment="none",
            right_adjustment="none",
            config=self.config(),
        )
        self.assertEqual(report.status, ComparisonStatus.REVIEW_REQUIRED)
        self.assertTrue(report.anomalies)

    def test_configured_threshold_detects_price_conflict(self):
        report = compare_daily_records(
            [daily(1, 10, "hithink"), daily(2, 11, "hithink")],
            [daily(1, 12, "longbridge"), daily(2, 11, "longbridge")],
            instrument_id="stock.hengtong_optic",
            left_provider="hithink",
            right_provider="longbridge",
            left_adjustment="none",
            right_adjustment="none",
            config=self.config(threshold=0.01),
        )
        self.assertEqual(report.status, ComparisonStatus.PRICE_CONFLICT)

    def test_adjustment_mismatch_prevents_price_judgment(self):
        report = compare_daily_records(
            [daily(1, 10, "hithink")],
            [daily(1, 10, "longbridge")],
            instrument_id="stock.hengtong_optic",
            left_provider="hithink",
            right_provider="longbridge",
            left_adjustment="none",
            right_adjustment="forward",
            config=self.config(minimum=1),
        )
        self.assertEqual(report.status, ComparisonStatus.ADJUSTMENT_MISMATCH)


if __name__ == "__main__":
    unittest.main()
