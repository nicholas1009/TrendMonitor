from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import AssetType, MarketRecord
from trend_monitor.validation import analyze_close_bar_structure


SHANGHAI = ZoneInfo("Asia/Shanghai")


def record(time_text: str, *, period: str, volume: float, turnover: float, close: float = 10.0):
    timestamp = int(datetime.fromisoformat(f"2026-08-28T{time_text}:00").replace(tzinfo=SHANGHAI).timestamp() * 1000)
    return MarketRecord(
        symbol="600487.SH",
        name="亨通光电",
        asset_type=AssetType.STOCK,
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        turnover=turnover,
        source="longbridge",
        period=period,
        trade_session="Intraday",
    )


class MinuteStructureTests(unittest.TestCase):
    def test_60m_close_bucket_is_compared_with_daily(self):
        times = ("09:30", "10:30", "13:00", "14:00", "15:00")
        minute = [
            record(value, period="60m", volume=100, turnover=1000)
            for value in times
        ]
        daily = [record("00:00", period="1d", volume=500, turnover=5000)]
        report = analyze_close_bar_structure(minute, daily, period="60m", minimum_days=1)
        self.assertTrue(report["all_schedules_match"])
        self.assertTrue(report["all_1500_closes_match_daily"])
        self.assertTrue(report["including_1500_always_closer_by_volume"])
        self.assertEqual(report["trade_sessions"], ["Intraday"])

    def test_incomplete_schedule_is_explicit(self):
        minute = [record("09:30", period="60m", volume=100, turnover=1000)]
        daily = [record("00:00", period="1d", volume=100, turnover=1000)]
        with self.assertRaises(TrendMonitorError) as raised:
            analyze_close_bar_structure(minute, daily, period="60m", minimum_days=1)
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
