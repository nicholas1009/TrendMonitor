from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.normalization.longbridge import (
    normalize_longbridge_candlesticks,
    normalize_longbridge_quote,
)
from trend_monitor.schemas import AssetType, SourceTrace
from trend_monitor.validation import validate_common_records


TRACE = SourceTrace(
    provider="longbridge",
    provider_symbol="600487.SH",
    raw_path="data/raw/longbridge/example.json",
    fetched_at="2026-08-29T00:00:00+00:00",
    source_timestamp=1787890800000,
)


class LongbridgeNormalizationTests(unittest.TestCase):
    def test_quote_normalization_has_internal_identity_and_trace(self):
        raw = {
            "data": {"item": [{
                "symbol": "600487.SH",
                "last_done": "15.20",
                "prev_close": "15.00",
                "open": "15.01",
                "high": "15.30",
                "low": "14.98",
                "timestamp": 1787890800,
                "volume": 123400,
                "turnover": "1875680.00",
            }]}
        }
        record = normalize_longbridge_quote(
            raw,
            instrument_id="stock.hengtong_optic",
            name="亨通光电",
            asset_type=AssetType.STOCK,
            source_trace=TRACE,
        )[0]
        validate_common_records([record])
        self.assertEqual(record.instrument_id, "stock.hengtong_optic")
        self.assertEqual(record.previous_close, 15.0)
        self.assertEqual(record.source_trace, TRACE)
        self.assertEqual(record.timestamp, 1787890800000)

    def test_minute_normalization_sorts_and_validates_session(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        first = int(datetime(2026, 8, 28, 9, 30, tzinfo=shanghai).timestamp())
        second = int(datetime(2026, 8, 28, 9, 45, tzinfo=shanghai).timestamp())
        raw = {"data": {"item": [
            {"timestamp": second, "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": 2, "turnover": "20", "trade_session": "Intraday"},
            {"timestamp": first, "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": 1, "turnover": "10", "trade_session": "Intraday"},
        ]}}
        records = normalize_longbridge_candlesticks(
            raw,
            instrument_id="stock.hengtong_optic",
            symbol="600487.SH",
            name="亨通光电",
            asset_type=AssetType.STOCK,
            period="15m",
            source_trace=TRACE,
        )
        validate_common_records(
            records,
            require_strict_time_order=True,
            validate_a_share_session=True,
            require_trade_session=True,
        )
        self.assertEqual([item.timestamp for item in records], [first * 1000, second * 1000])
        self.assertEqual(records[0].trade_session, "Intraday")

    def test_minute_quality_rejects_bad_ohlc_and_lunch_timestamp(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        lunch = int(datetime(2026, 8, 28, 12, 0, tzinfo=shanghai).timestamp())
        raw = {"data": {"item": [{
            "timestamp": lunch,
            "open": "12",
            "high": "11",
            "low": "9",
            "close": "10",
            "volume": 1,
            "turnover": "10",
        }]}}
        records = normalize_longbridge_candlesticks(
            raw,
            instrument_id="stock.hengtong_optic",
            symbol="600487.SH",
            name="亨通光电",
            asset_type=AssetType.STOCK,
            period="15m",
            source_trace=TRACE,
        )
        with self.assertRaises(TrendMonitorError) as raised:
            validate_common_records(records, validate_a_share_session=True)
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)


if __name__ == "__main__":
    unittest.main()
