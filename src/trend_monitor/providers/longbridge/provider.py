"""Thin wrapper around the official Longbridge Python SDK."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory
from trend_monitor.providers.longbridge.config import (
    LongbridgeCredentials,
    load_credentials,
)
from trend_monitor.providers.longbridge.errors import (
    LongbridgeProviderError,
    convert_sdk_exception,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
RawResponse = dict[str, Any]


def _epoch_seconds(value: datetime) -> int:
    # longbridge==4.5.0 returns SDK datetimes without tzinfo in the host's
    # local timezone. datetime.timestamp() intentionally applies that local
    # timezone for naive values. Relabelling them as UTC shifts A-share bars
    # and makes otherwise-valid minute data fail the session validator.
    return int(value.timestamp())


def _enum_text(value: object) -> str:
    name = getattr(value, "name", None)
    text = str(name if name is not None else value)
    # The Rust-backed longbridge==4.5.0 enums do not expose ``name`` to
    # Python and stringify as ``Market.CN`` / ``TradeSession.Intraday``.
    return text.rsplit(".", 1)[-1]


def _decimal_text(value: Decimal | object) -> str:
    return str(value)


class LongbridgeProvider:
    """Official SDK call layer; it never creates a trading context."""

    def __init__(
        self,
        credentials: LongbridgeCredentials | None = None,
        *,
        context: object | None = None,
        dotenv_path: str | Path = ".env",
    ) -> None:
        self._credentials = credentials or load_credentials(dotenv_path)
        self._context = context
        self._context_supplied = context is not None

    @property
    def configured(self) -> bool:
        return self._context is not None or self._credentials is not None

    def _quote_context(self, *, isolated: bool = False):
        if self._context is not None and (not isolated or self._context_supplied):
            return self._context
        if self._credentials is None:
            raise LongbridgeProviderError(
                ErrorCategory.AUTH_ERROR,
                "BLOCKED_BY_LONGBRIDGE_CREDENTIALS",
            )
        try:
            from longbridge.openapi import Config, QuoteContext

            config = Config.from_apikey(
                self._credentials.app_key,
                self._credentials.app_secret,
                self._credentials.access_token,
                enable_print_quote_packages=False,
                log_path=None,
            )
            context = QuoteContext(config)
            if not isolated:
                self._context = context
            return context
        except Exception as exc:
            raise convert_sdk_exception(exc, secrets=self._secret_values()) from exc

    def _secret_values(self) -> tuple[str, ...]:
        if self._credentials is None:
            return ()
        return (
            self._credentials.app_key,
            self._credentials.app_secret,
            self._credentials.access_token,
        )

    def _call(
        self,
        operation: Callable[..., object],
        *args: object,
        error_context: dict[str, object] | None = None,
    ) -> object:
        try:
            return operation(*args)
        except LongbridgeProviderError:
            raise
        except Exception as exc:
            mapped = convert_sdk_exception(exc, secrets=self._secret_values())
            if error_context:
                mapped.details.update(error_context)
            raise mapped from exc

    @staticmethod
    def _quote_item(item: object) -> dict[str, Any]:
        return {
            "symbol": str(getattr(item, "symbol")),
            "last_done": _decimal_text(getattr(item, "last_done")),
            "prev_close": _decimal_text(getattr(item, "prev_close")),
            "open": _decimal_text(getattr(item, "open")),
            "high": _decimal_text(getattr(item, "high")),
            "low": _decimal_text(getattr(item, "low")),
            "timestamp": _epoch_seconds(getattr(item, "timestamp")),
            "volume": int(getattr(item, "volume")),
            "turnover": _decimal_text(getattr(item, "turnover")),
            "trade_status": _enum_text(getattr(item, "trade_status")),
        }

    @staticmethod
    def _candlestick_item(item: object) -> dict[str, Any]:
        return {
            "close": _decimal_text(getattr(item, "close")),
            "open": _decimal_text(getattr(item, "open")),
            "low": _decimal_text(getattr(item, "low")),
            "high": _decimal_text(getattr(item, "high")),
            "volume": int(getattr(item, "volume")),
            "turnover": _decimal_text(getattr(item, "turnover")),
            "timestamp": _epoch_seconds(getattr(item, "timestamp")),
            "trade_session": _enum_text(getattr(item, "trade_session")),
        }

    @staticmethod
    def _envelope(
        *,
        data_type: str,
        symbol: str,
        items: list[dict[str, Any]],
        period: str | None = None,
        adjust_type: str | None = None,
    ) -> RawResponse:
        timestamps = [item["timestamp"] for item in items if isinstance(item.get("timestamp"), int)]
        return {
            "provider": "longbridge",
            "sdk": "longbridge-python",
            "sdk_version": version("longbridge"),
            "request": {
                "symbol": symbol,
                "data_type": data_type,
                "period": period,
                "adjust_type": adjust_type,
            },
            "data": {
                "timestamp": max(timestamps) if timestamps else None,
                "item": items,
            },
        }

    def get_quote(self, symbol: str) -> RawResponse:
        context = self._quote_context()
        response = self._call(
            getattr(context, "quote"),
            [symbol],
            error_context={"symbol": symbol, "data_type": "quote"},
        )
        items = [self._quote_item(item) for item in list(response)]
        if not items:
            raise LongbridgeProviderError(ErrorCategory.EMPTY_DATA, "quote response is empty")
        return self._envelope(data_type="quote", symbol=symbol, items=items)

    def get_static_info(self, symbols: list[str] | tuple[str, ...]) -> RawResponse:
        """Return JSON-safe official ``static_info`` identity evidence."""
        requested = list(symbols)
        if not requested:
            raise LongbridgeProviderError(
                ErrorCategory.INVALID_DATA,
                "static_info symbols are empty",
            )
        context = self._quote_context()
        response = self._call(
            getattr(context, "static_info"),
            requested,
            error_context={"symbols": tuple(requested), "data_type": "static_info"},
        )
        items = []
        for item in list(response):
            items.append(
                {
                    "symbol": str(getattr(item, "symbol")),
                    "name_cn": str(getattr(item, "name_cn")),
                    "name_en": str(getattr(item, "name_en")),
                    "name_hk": str(getattr(item, "name_hk")),
                    "exchange": str(getattr(item, "exchange")),
                    "currency": str(getattr(item, "currency")),
                    "lot_size": int(getattr(item, "lot_size")),
                    "total_shares": _decimal_text(getattr(item, "total_shares")),
                    "circulating_shares": _decimal_text(getattr(item, "circulating_shares")),
                    "hk_shares": _decimal_text(getattr(item, "hk_shares")),
                    "eps": _decimal_text(getattr(item, "eps")),
                    "eps_ttm": _decimal_text(getattr(item, "eps_ttm")),
                    "bps": _decimal_text(getattr(item, "bps")),
                    "dividend_yield": _decimal_text(getattr(item, "dividend_yield")),
                    "stock_derivatives": [
                        _enum_text(value) for value in list(getattr(item, "stock_derivatives"))
                    ],
                    "board": _enum_text(getattr(item, "board")),
                }
            )
        if not items:
            raise LongbridgeProviderError(
                ErrorCategory.EMPTY_DATA,
                "static_info response is empty",
            )
        return {
            "provider": "longbridge",
            "sdk": "longbridge-python",
            "sdk_version": version("longbridge"),
            "request": {"symbols": requested, "data_type": "static_info"},
            "data": {"timestamp": None, "item": items},
        }

    def get_daily(self, symbol: str, *, start: int, end: int) -> RawResponse:
        from longbridge.openapi import AdjustType, Period

        context = self._quote_context()
        start_date = datetime.fromtimestamp(start / 1000, tz=timezone.utc).astimezone(SHANGHAI).date()
        end_date = datetime.fromtimestamp(end / 1000, tz=timezone.utc).astimezone(SHANGHAI).date()
        response = self._call(
            getattr(context, "history_candlesticks_by_date"),
            symbol,
            Period.Day,
            AdjustType.NoAdjust,
            start_date,
            end_date,
            error_context={"symbol": symbol, "period": "1d", "data_type": "daily"},
        )
        items = [self._candlestick_item(item) for item in list(response)]
        if not items:
            raise LongbridgeProviderError(ErrorCategory.EMPTY_DATA, "daily response is empty")
        return self._envelope(
            data_type="daily",
            symbol=symbol,
            items=items,
            period="1d",
            adjust_type="none",
        )

    def get_candlesticks(self, symbol: str, *, period: str, count: int) -> RawResponse:
        from longbridge.openapi import AdjustType, Period

        periods = {"1m": Period.Min_1, "15m": Period.Min_15, "60m": Period.Min_60}
        if period not in periods:
            raise LongbridgeProviderError(
                ErrorCategory.UNSUPPORTED,
                f"unsupported Longbridge period: {period}",
            )
        context = self._quote_context()
        response = self._call(
            getattr(context, "candlesticks"),
            symbol,
            periods[period],
            count,
            AdjustType.NoAdjust,
            error_context={"symbol": symbol, "period": period, "data_type": period},
        )
        items = [self._candlestick_item(item) for item in list(response)]
        if not items:
            raise LongbridgeProviderError(
                ErrorCategory.EMPTY_DATA,
                f"Longbridge {period} response is empty",
            )
        return self._envelope(
            data_type=period,
            symbol=symbol,
            items=items,
            period=period,
            adjust_type="none",
        )

    def get_history_candlesticks(
        self,
        symbol: str,
        *,
        period: str,
        start: date,
        end: date,
    ) -> RawResponse:
        """Get one bounded historical minute window through the official SDK.

        The official endpoint returns at most 1000 bars. Callers that need a
        longer span must split it into bounded windows so no truncation is
        mistaken for a complete history.
        """
        from longbridge.openapi import AdjustType, Period

        periods = {"1m": Period.Min_1, "15m": Period.Min_15, "60m": Period.Min_60}
        if period not in periods:
            raise LongbridgeProviderError(
                ErrorCategory.UNSUPPORTED,
                f"unsupported Longbridge history period: {period}",
            )
        if start > end:
            raise LongbridgeProviderError(
                ErrorCategory.INVALID_DATA,
                "history start date is after end date",
            )
        # longbridge==4.5.0 can fail refreshing its socket token after a
        # sequence of bounded history calls on one long-lived context. Eight
        # isolated official contexts were verified successfully in the same
        # process, so date-window history uses request-scoped contexts while
        # injected test contexts keep their original deterministic behavior.
        context = self._quote_context(isolated=not self._context_supplied)
        error_context = {
            "symbol": symbol,
            "period": period,
            "data_type": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        try:
            response = self._call(
                getattr(context, "history_candlesticks_by_date"),
                symbol,
                periods[period],
                AdjustType.NoAdjust,
                start,
                end,
                error_context=error_context,
            )
        except LongbridgeProviderError as exc:
            # A stale SDK QuoteContext can fail while refreshing its socket
            # token. Recreate it once for a classified network error; the
            # second failure is preserved and propagated without looping.
            if exc.category is not ErrorCategory.NETWORK_ERROR or self._credentials is None:
                raise
            if not self._context_supplied:
                self._context = None
            context = self._quote_context(isolated=not self._context_supplied)
            response = self._call(
                getattr(context, "history_candlesticks_by_date"),
                symbol,
                periods[period],
                AdjustType.NoAdjust,
                start,
                end,
                error_context=error_context,
            )
        items = [self._candlestick_item(item) for item in list(response)]
        if not items:
            raise LongbridgeProviderError(
                ErrorCategory.EMPTY_DATA,
                f"Longbridge historical {period} response is empty",
            )
        raw = self._envelope(
            data_type=period,
            symbol=symbol,
            items=items,
            period=period,
            adjust_type="none",
        )
        raw["request"]["start_date"] = start.isoformat()
        raw["request"]["end_date"] = end.isoformat()
        raw["request"]["endpoint"] = "history_candlesticks_by_date"
        return raw

    def get_trading_sessions(self) -> RawResponse:
        """Return the official SDK trading-session response as JSON-safe Raw."""
        context = self._quote_context()
        response = self._call(
            getattr(context, "trading_session"),
            error_context={"data_type": "trading_session"},
        )
        items: list[dict[str, Any]] = []
        for market_session in list(response):
            sessions = []
            for session in list(getattr(market_session, "trade_sessions")):
                sessions.append(
                    {
                        "begin_time": getattr(session, "begin_time").isoformat(),
                        "end_time": getattr(session, "end_time").isoformat(),
                        "trade_session": _enum_text(getattr(session, "trade_session")),
                    }
                )
            items.append(
                {
                    "market": _enum_text(getattr(market_session, "market")),
                    "trade_sessions": sessions,
                }
            )
        if not items:
            raise LongbridgeProviderError(
                ErrorCategory.EMPTY_DATA,
                "Longbridge trading-session response is empty",
            )
        return {
            "provider": "longbridge",
            "sdk": "longbridge-python",
            "sdk_version": version("longbridge"),
            "request": {"data_type": "trading_session"},
            "data": {"item": items},
        }
