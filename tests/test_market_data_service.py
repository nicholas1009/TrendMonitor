from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trend_monitor.cache import RawCache
from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.normalization import normalize_historical, normalize_snapshot
from trend_monitor.providers.hithink import HithinkProviderError
from trend_monitor.registry import InstrumentRegistry
from trend_monitor.schemas import AssetType, DataType
from trend_monitor.services import MarketDataService
from trend_monitor.validation import validate_records


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "instruments.json"


def quote_raw(symbol: str) -> dict:
    return {
        "code": 0,
        "data": {
            "timestamp": 1720000000000,
            "item": [{
                "thscode": symbol,
                "name": "test",
                "open_price": 10.0,
                "high_price": 11.0,
                "low_price": 9.0,
                "last_price": 10.5,
                "volume": 1000,
                "turnover": 10500,
            }],
        },
    }


class FakeProvider:
    name = "hithink"

    def __init__(self, error: HithinkProviderError | None = None):
        self.error = error

    def get_quote(self, provider_symbol, asset_type):
        if self.error:
            raise self.error
        return quote_raw(provider_symbol)

    def get_daily(self, provider_symbol, asset_type, *, start, end):
        if self.error:
            raise self.error
        return {
            "code": 0,
            "data": {"item": [{
                "date_ms": start,
                "open_price": 10.0,
                "high_price": 11.0,
                "low_price": 9.0,
                "close_price": 10.5,
                "volume": 1000,
                "turnover": 10500,
            }]},
        }

    def normalize_quote(self, raw, instrument, mapping, source_trace):
        records = normalize_snapshot(
            raw,
            asset_type=instrument.asset_type,
            source_trace=source_trace,
        )
        validate_records(records)
        return records

    def normalize_daily(self, raw, instrument, mapping, source_trace):
        records = normalize_historical(
            raw,
            symbol=mapping.provider_symbol,
            name=instrument.display_name,
            asset_type=instrument.asset_type,
            source_trace=source_trace,
        )
        validate_records(records)
        return records


class MarketDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.registry = InstrumentRegistry.load(REGISTRY_PATH)

    def tearDown(self):
        self.temporary.cleanup()

    def service(self, provider):
        return MarketDataService(
            self.registry,
            [provider],
            RawCache(self.temporary.name),
        )

    def test_result_has_source_trace_and_provider_metadata(self):
        result = self.service(FakeProvider()).get_quote(
            "stock.hengtong_optic", "hithink"
        )
        self.assertEqual(result.metadata.instrument_id, "stock.hengtong_optic")
        self.assertEqual(result.metadata.provider_symbol, "600487.SH")
        self.assertFalse(result.metadata.fallback_used)
        trace = result.normalized[0].source_trace
        self.assertIsNotNone(trace)
        self.assertEqual(trace.raw_path, result.metadata.raw_path)
        self.assertTrue(Path(result.metadata.raw_path).exists())

    def test_fallback_is_explicit(self):
        result = self.service(FakeProvider()).get_quote(
            "sector.communication_equipment",
            "eastmoney",
            fallback_providers=["hithink"],
        )
        self.assertTrue(result.metadata.fallback_used)
        self.assertEqual(result.metadata.requested_provider, "eastmoney")
        self.assertEqual(result.metadata.actual_provider, "hithink")
        self.assertEqual(result.metadata.fallback_reason, "eastmoney:NOT_CONFIGURED")

    def test_provider_error_is_retained_when_all_sources_fail(self):
        provider = FakeProvider(
            HithinkProviderError(ErrorCategory.AUTH_ERROR, "invalid credentials")
        )
        with self.assertRaises(TrendMonitorError) as raised:
            self.service(provider).get_quote("stock.hengtong_optic", "hithink")
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)
        self.assertEqual(
            raised.exception.details["failures"],
            ("hithink:AUTH_ERROR",),
        )
        self.assertEqual(
            raised.exception.details["failure_details"],
            ({
                "provider": "hithink",
                "category": "AUTH_ERROR",
                "message": "invalid credentials",
            },),
        )

    def test_conflict_state_is_expressible_without_selection_logic(self):
        self.assertEqual(ErrorCategory.DATA_CONFLICT.value, "DATA_CONFLICT")

    def test_cached_raw_is_revalidated_without_provider_request(self):
        provider = FakeProvider()
        service = self.service(provider)
        first = service.get_quote("stock.hengtong_optic", "hithink")
        entry = service.cache.latest("stock.hengtong_optic", "hithink", DataType.QUOTE)
        self.assertIsNotNone(entry)
        assert entry is not None
        provider.error = HithinkProviderError(ErrorCategory.NETWORK_ERROR, "must not request")
        replayed = service.load_cached(entry)
        self.assertEqual(replayed.normalized, first.normalized)
        self.assertEqual(replayed.metadata.raw_path, first.metadata.raw_path)


if __name__ == "__main__":
    unittest.main()
