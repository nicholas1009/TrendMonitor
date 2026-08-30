from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from trend_monitor.errors import ErrorCategory
from trend_monitor.providers.longbridge.config import load_credentials
from trend_monitor.providers.longbridge.errors import (
    LongbridgeProviderError,
    category_for_longbridge_error,
    convert_sdk_exception,
)
from trend_monitor.providers.longbridge.provider import LongbridgeProvider


def quote_object(symbol="600487.SH"):
    return SimpleNamespace(
        symbol=symbol,
        last_done=Decimal("15.20"),
        prev_close=Decimal("15.00"),
        open=Decimal("15.01"),
        high=Decimal("15.30"),
        low=Decimal("14.98"),
        timestamp=datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc),
        volume=123400,
        turnover=Decimal("1875680.00"),
        trade_status="Normal",
    )


def candle_object(timestamp=None):
    return SimpleNamespace(
        close=Decimal("15.20"),
        open=Decimal("15.01"),
        low=Decimal("14.98"),
        high=Decimal("15.30"),
        volume=123400,
        turnover=Decimal("1875680.00"),
        timestamp=timestamp or datetime(2026, 8, 28, tzinfo=timezone.utc),
        trade_session="Normal",
    )


def static_info_object(symbol="000001.SH", name_cn="上证指数"):
    return SimpleNamespace(
        symbol=symbol,
        name_cn=name_cn,
        name_en="SSE Index",
        name_hk="上證指數",
        exchange="SSE",
        currency="CNY",
        lot_size=0,
        total_shares=Decimal("0"),
        circulating_shares=Decimal("0"),
        hk_shares=Decimal("0"),
        eps=Decimal("0"),
        eps_ttm=Decimal("0"),
        bps=Decimal("0"),
        dividend_yield=Decimal("0"),
        stock_derivatives=[],
        board="CNIX",
    )


class FakeContext:
    def static_info(self, symbols):
        return [static_info_object(symbols[0])]

    def quote(self, symbols):
        return [quote_object(symbols[0])]

    def history_candlesticks_by_date(self, symbol, period, adjust, start, end):
        return [candle_object()]

    def candlesticks(self, symbol, period, count, adjust):
        return [candle_object(datetime(2026, 8, 28, 1, 30, tzinfo=timezone.utc))]

    def trading_session(self):
        return [SimpleNamespace(
            market="CN",
            trade_sessions=[SimpleNamespace(
                begin_time=time(9, 30),
                end_time=time(11, 30),
                trade_session="Intraday",
            )],
        )]


class FakeSdkError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class FailedContext:
    def candlesticks(self, symbol, period, count, adjust):
        raise FakeSdkError(301604, "no quote permission")


class LongbridgeProviderTests(unittest.TestCase):
    def test_dotenv_credentials_are_loaded_without_environment_mutation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "LONGBRIDGE_APP_KEY=key\n"
                "LONGBRIDGE_APP_SECRET=secret\n"
                "LONGBRIDGE_ACCESS_TOKEN=token\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                credentials = load_credentials(path)
            self.assertIsNotNone(credentials)
            self.assertEqual(credentials.app_key, "key")

    def test_missing_credentials_are_explicit(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = LongbridgeProvider(dotenv_path="missing.env")
        with self.assertRaises(LongbridgeProviderError) as raised:
            provider.get_quote("600487.SH")
        self.assertEqual(raised.exception.category, ErrorCategory.AUTH_ERROR)
        self.assertEqual(raised.exception.message, "BLOCKED_BY_LONGBRIDGE_CREDENTIALS")

    def test_quote_and_candlestick_serialization_preserve_raw_fields(self):
        provider = LongbridgeProvider(context=FakeContext())
        quote = provider.get_quote("600487.SH")
        self.assertEqual(quote["data"]["item"][0]["last_done"], "15.20")
        self.assertEqual(quote["data"]["item"][0]["prev_close"], "15.00")
        bars = provider.get_candlesticks("600487.SH", period="15m", count=10)
        self.assertEqual(bars["request"]["period"], "15m")
        self.assertEqual(bars["request"]["adjust_type"], "none")
        self.assertEqual(bars["data"]["item"][0]["volume"], 123400)

    def test_static_info_serialization_preserves_identity_fields(self):
        provider = LongbridgeProvider(context=FakeContext())
        raw = provider.get_static_info(["000001.SH"])
        item = raw["data"]["item"][0]
        self.assertEqual(item["symbol"], "000001.SH")
        self.assertEqual(item["name_cn"], "上证指数")
        self.assertEqual(item["exchange"], "SSE")
        self.assertEqual(item["board"], "CNIX")

    def test_historical_minutes_and_trading_sessions_are_json_safe(self):
        provider = LongbridgeProvider(context=FakeContext())
        bars = provider.get_history_candlesticks(
            "600487.SH",
            period="1m",
            start=date(2026, 8, 1),
            end=date(2026, 8, 28),
        )
        self.assertEqual(bars["request"]["period"], "1m")
        self.assertEqual(bars["request"]["endpoint"], "history_candlesticks_by_date")
        self.assertEqual(bars["request"]["start_date"], "2026-08-01")
        sessions = provider.get_trading_sessions()
        self.assertEqual(sessions["data"]["item"][0]["market"], "CN")
        self.assertEqual(
            sessions["data"]["item"][0]["trade_sessions"][0]["begin_time"],
            "09:30:00",
        )

    def test_naive_sdk_datetime_uses_host_local_timezone(self):
        local_naive = datetime(2026, 8, 28, 16, 0)
        provider = LongbridgeProvider(context=FakeContext())
        serialized = provider._candlestick_item(candle_object(local_naive))
        self.assertEqual(serialized["timestamp"], int(local_naive.timestamp()))

    def test_official_error_codes_are_mapped(self):
        self.assertEqual(category_for_longbridge_error(401003), ErrorCategory.AUTH_ERROR)
        self.assertEqual(category_for_longbridge_error(301604), ErrorCategory.PERMISSION_ERROR)
        self.assertEqual(category_for_longbridge_error(301606), ErrorCategory.RATE_LIMIT)
        self.assertEqual(category_for_longbridge_error(301603), ErrorCategory.EMPTY_DATA)
        self.assertEqual(category_for_longbridge_error(301600), ErrorCategory.INVALID_DATA)
        self.assertEqual(category_for_longbridge_error(301602), ErrorCategory.NETWORK_ERROR)

    def test_sdk_error_preserves_safe_symbol_period_code_and_class(self):
        provider = LongbridgeProvider(context=FailedContext())
        with self.assertRaises(LongbridgeProviderError) as raised:
            provider.get_candlesticks("600487.SH", period="15m", count=10)
        self.assertEqual(raised.exception.category, ErrorCategory.PERMISSION_ERROR)
        self.assertEqual(raised.exception.provider_code, 301604)
        self.assertEqual(raised.exception.details["exception_class"], "FakeSdkError")
        self.assertEqual(raised.exception.details["symbol"], "600487.SH")
        self.assertEqual(raised.exception.details["period"], "15m")

    def test_unknown_sdk_error_and_secret_redaction(self):
        exc = SimpleNamespace(code=999999, message="failed secret-value")
        mapped = convert_sdk_exception(exc, secrets=("secret-value",))
        self.assertEqual(mapped.category, ErrorCategory.UNKNOWN_ERROR)
        self.assertNotIn("secret-value", mapped.message)
        self.assertEqual(mapped.details["exception_class"], "SimpleNamespace")
        self.assertEqual(mapped.details["provider_code"], 999999)

    def test_sdk_connect_message_without_code_maps_to_network(self):
        exc = FakeSdkError(None, "error sending request: client error (Connect)")
        mapped = convert_sdk_exception(exc)
        self.assertEqual(mapped.category, ErrorCategory.NETWORK_ERROR)


if __name__ == "__main__":
    unittest.main()
