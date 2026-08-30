import unittest

from trend_monitor.normalization import normalize_historical, normalize_snapshot
from trend_monitor.providers.hithink import ErrorCategory, HithinkProviderError
from trend_monitor.schemas import AssetType, MarketRecord
from trend_monitor.validation import validate_market_record, validate_records, validate_raw_items


HISTORICAL_RAW = {
    "code": 0,
    "message": "ok",
    "request_id": "test",
    "data": {
        "timestamp": 1720000000000,
        "item": [
            {
                "date_ms": 1719907200000,
                "open_price": 10.0,
                "high_price": 11.0,
                "low_price": 9.5,
                "close_price": 10.5,
                "volume": 1000,
                "turnover": 10500,
            }
        ],
    },
}


class NormalizationValidationTests(unittest.TestCase):
    def test_historical_normalization_for_each_asset_class(self):
        cases = [
            (AssetType.STOCK, "600487.SH"),
            (AssetType.INDEX, "000905.SH"),
            (AssetType.SECTOR, "example.TI"),
            (AssetType.ETF, "510300.SH"),
        ]
        for asset_type, symbol in cases:
            with self.subTest(asset_type=asset_type):
                records = normalize_historical(
                    HISTORICAL_RAW,
                    symbol=symbol,
                    name="test",
                    asset_type=asset_type,
                )
                validate_records(records)
                self.assertEqual(records[0].symbol, symbol)
                self.assertEqual(records[0].asset_type, asset_type)

    def test_snapshot_normalization_preserves_missing_source_timestamp(self):
        raw = {
            "code": 0,
            "data": {
                "timestamp": None,
                "item": [{
                    "thscode": "600487.SH", "last_price": 15.0,
                    "open_price": 14.0, "high_price": 15.2, "low_price": 13.9,
                    "volume": 100, "turnover": 1500,
                }],
            },
        }
        record = normalize_snapshot(raw, asset_type=AssetType.STOCK)[0]
        self.assertIsNone(record.timestamp)
        with self.assertRaises(HithinkProviderError) as raised:
            validate_market_record(record)
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)

    def test_empty_raw_array_is_explicit(self):
        with self.assertRaises(HithinkProviderError) as raised:
            validate_raw_items({"data": {"item": []}})
        self.assertEqual(raised.exception.category, ErrorCategory.EMPTY_DATA)

    def test_high_below_low_is_invalid(self):
        record = MarketRecord(
            symbol="600487.SH", name="test", asset_type=AssetType.STOCK,
            timestamp=1719907200000, open=10, high=9, low=10, close=9.5,
            volume=1, turnover=10, source="hithink", period="1d",
        )
        with self.assertRaises(HithinkProviderError) as raised:
            validate_market_record(record)
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_missing_ohlcv_is_incomplete(self):
        record = MarketRecord(
            symbol="600487.SH", name="test", asset_type=AssetType.STOCK,
            timestamp=1719907200000, open=None, high=10, low=9, close=9.5,
            volume=None, turnover=None, source="hithink", period="1d",
        )
        with self.assertRaises(HithinkProviderError) as raised:
            validate_market_record(record)
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
