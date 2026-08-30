from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.cache import RawCache
from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.providers.longbridge.adapter import LongbridgeMarketDataAdapter
from trend_monitor.providers.longbridge.provider import LongbridgeProvider
from trend_monitor.registry import InstrumentRegistry
from trend_monitor.schemas import DataType
from trend_monitor.services import MarketDataService


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "instruments.json"


class FailedHithink:
    name = "hithink"

    def get_bars(self, provider_symbol, asset_type, *, period, count):
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"no direct {period}")


class FakeLongbridgeContext:
    def candlesticks(self, symbol, period, count, adjust):
        shanghai = ZoneInfo("Asia/Shanghai")
        return [
            SimpleNamespace(
                close=Decimal("10.5"),
                open=Decimal("10"),
                low=Decimal("9.8"),
                high=Decimal("10.6"),
                volume=100,
                turnover=Decimal("1030"),
                timestamp=datetime(2026, 8, 28, 9, 30, tzinfo=shanghai),
                trade_session="Normal",
            ),
            SimpleNamespace(
                close=Decimal("10.6"),
                open=Decimal("10.5"),
                low=Decimal("10.4"),
                high=Decimal("10.7"),
                volume=120,
                turnover=Decimal("1270"),
                timestamp=datetime(2026, 8, 28, 9, 45, tzinfo=shanghai),
                trade_session="Normal",
            ),
        ]


class BoundaryQuirkContext:
    def __init__(self, hour=9, minute=30):
        self.hour = hour
        self.minute = minute

    def candlesticks(self, symbol, period, count, adjust):
        shanghai = ZoneInfo("Asia/Shanghai")
        return [SimpleNamespace(
            close=Decimal("10.5"),
            open=Decimal("9.7"),
            low=Decimal("9.8"),
            high=Decimal("10.6"),
            volume=100,
            turnover=Decimal("1030"),
            timestamp=datetime(2026, 8, 28, self.hour, self.minute, tzinfo=shanghai),
            trade_session="Intraday",
        )]


class MinuteFallbackTests(unittest.TestCase):
    def test_market_data_service_preserves_confirmed_0930_source_quirk(self):
        with TemporaryDirectory() as directory:
            service = MarketDataService(
                InstrumentRegistry.load(REGISTRY_PATH),
                [LongbridgeMarketDataAdapter(LongbridgeProvider(context=BoundaryQuirkContext()))],
                RawCache(directory),
            )
            result = service.get_bars(
                "stock.hengtong_optic", "longbridge", period="60m", count=1
            )
            self.assertEqual(result.normalized[0].open, 9.7)
            self.assertEqual(result.normalized[0].low, 9.8)

    def test_market_data_service_rejects_same_quirk_outside_0930(self):
        with TemporaryDirectory() as directory:
            service = MarketDataService(
                InstrumentRegistry.load(REGISTRY_PATH),
                [LongbridgeMarketDataAdapter(
                    LongbridgeProvider(context=BoundaryQuirkContext(hour=10, minute=30))
                )],
                RawCache(directory),
            )
            with self.assertRaises(TrendMonitorError) as raised:
                service.get_bars(
                    "stock.hengtong_optic", "longbridge", period="60m", count=1
                )
            self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)
            self.assertEqual(
                raised.exception.details["failure_details"][0]["category"],
                ErrorCategory.INVALID_DATA.value,
            )

    def test_period_is_preserved_and_fallback_raw_uses_actual_provider(self):
        with TemporaryDirectory() as directory:
            service = MarketDataService(
                InstrumentRegistry.load(REGISTRY_PATH),
                [
                    FailedHithink(),
                    LongbridgeMarketDataAdapter(
                        LongbridgeProvider(context=FakeLongbridgeContext())
                    ),
                ],
                RawCache(directory),
            )
            result = service.get_bars(
                "stock.hengtong_optic",
                "hithink",
                period="15m",
                count=2,
                fallback_providers=["longbridge"],
            )
            self.assertEqual(result.metadata.data_type, DataType.KLINE_15M)
            self.assertEqual(result.metadata.requested_provider, "hithink")
            self.assertEqual(result.metadata.actual_provider, "longbridge")
            self.assertTrue(result.metadata.fallback_used)
            self.assertEqual(result.metadata.fallback_reason, "hithink:UNSUPPORTED")
            self.assertIn("/longbridge/15m/", result.metadata.raw_path)
            self.assertEqual(result.normalized[0].period, "15m")
            self.assertEqual(result.normalized[0].instrument_id, "stock.hengtong_optic")
            self.assertEqual(
                result.normalized[0].source_trace.source_timestamp,
                result.metadata.source_timestamp,
            )

    def test_period_never_downgrades_to_daily(self):
        with TemporaryDirectory() as directory:
            service = MarketDataService(
                InstrumentRegistry.load(REGISTRY_PATH),
                [FailedHithink()],
                RawCache(directory),
            )
            with self.assertRaises(TrendMonitorError) as raised:
                service.get_bars(
                    "stock.hengtong_optic",
                    "hithink",
                    period="60m",
                )
            self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)
            self.assertEqual(raised.exception.details["failures"], ("hithink:UNSUPPORTED",))


if __name__ == "__main__":
    unittest.main()
