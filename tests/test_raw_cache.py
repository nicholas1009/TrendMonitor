from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trend_monitor.cache import CacheStatus, RawCache
from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import DataType


RAW = {
    "code": 0,
    "data": {
        "timestamp": 1720000000000,
        "item": [{"thscode": "600487.SH", "last_price": 14.5}],
    },
}


class RawCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.cache = RawCache(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, *, fetched_at=None):
        return self.cache.save(
            instrument_id="stock.hengtong_optic",
            provider="hithink",
            provider_symbol="600487.SH",
            data_type=DataType.QUOTE,
            raw=RAW,
            fetched_at=fetched_at,
        )

    def test_save_load_manifest_and_no_overwrite(self):
        first = self.save()
        second = self.save()
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(self.cache.load(first), RAW)
        self.assertEqual(len(self.cache.entries()), 2)
        self.assertEqual(self.cache.entries()[0].provider_symbol, "600487.SH")
        self.assertEqual(self.cache.entries()[0].source_timestamp, 1720000000000)

    def test_cache_status_fresh_stale_and_missing(self):
        fetched = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.save(fetched_at=fetched)
        self.assertEqual(
            self.cache.status(
                "stock.hengtong_optic",
                "hithink",
                DataType.QUOTE,
                max_age=timedelta(hours=1),
                now=fetched + timedelta(minutes=30),
            ),
            CacheStatus.FRESH,
        )
        self.assertEqual(
            self.cache.status(
                "stock.hengtong_optic",
                "hithink",
                DataType.QUOTE,
                max_age=timedelta(hours=1),
                now=fetched + timedelta(hours=2),
            ),
            CacheStatus.STALE,
        )
        self.assertEqual(
            self.cache.status("index.csi500", "hithink", DataType.QUOTE),
            CacheStatus.MISSING,
        )

    def test_invalid_cache_is_detected(self):
        entry = self.save()
        Path(entry.path).write_text("not-json", encoding="utf-8")
        self.assertEqual(
            self.cache.status("stock.hengtong_optic", "hithink", DataType.QUOTE),
            CacheStatus.INVALID,
        )
        with self.assertRaises(TrendMonitorError) as raised:
            self.cache.load(entry)
        self.assertEqual(raised.exception.category, ErrorCategory.CACHE_INVALID)

    def test_sensitive_fields_are_rejected(self):
        for key in ("api_key", "app_key", "app_secret", "access_token"):
            with self.subTest(key=key), self.assertRaises(TrendMonitorError) as raised:
                self.cache.save(
                    instrument_id="stock.hengtong_optic",
                    provider="longbridge",
                    provider_symbol="600487.SH",
                    data_type=DataType.QUOTE,
                    raw={key: "must-not-be-written"},
                )
            self.assertEqual(raised.exception.category, ErrorCategory.CACHE_INVALID)
        self.assertFalse(self.cache.manifest_path.exists())

    def test_daily_manifest_distinguishes_request_and_actual_data_range(self):
        actual_start = 1719907200000
        actual_end = 1720080000000
        entry = self.cache.save(
            instrument_id="index.csi500",
            provider="hithink",
            provider_symbol="000905.SH",
            data_type=DataType.DAILY,
            raw={
                "code": 0,
                "data": {
                    "item": [
                        {"date_ms": actual_start},
                        {"date_ms": actual_end},
                    ]
                },
            },
            request_start=1719000000000,
            request_end=1721000000000,
        )
        self.assertEqual(entry.data_start, actual_start)
        self.assertEqual(entry.data_end, actual_end)
        self.assertEqual(entry.request_start, 1719000000000)
        self.assertEqual(entry.request_end, 1721000000000)

    def test_status_event_marks_latest_entry_invalid_without_overwriting_raw(self):
        entry = self.save()
        updated = self.cache.record_status(entry, CacheStatus.INVALID)
        self.assertEqual(updated.path, entry.path)
        self.assertEqual(self.cache.load(updated), RAW)
        self.assertEqual(
            self.cache.status("stock.hengtong_optic", "hithink", DataType.QUOTE),
            CacheStatus.INVALID,
        )


if __name__ == "__main__":
    unittest.main()
