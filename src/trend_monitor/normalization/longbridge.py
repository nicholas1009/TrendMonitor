"""Normalize the JSON-safe representation of Longbridge SDK responses."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import AssetType, MarketRecord, SourceTrace


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data = raw.get("data")
    if not isinstance(data, dict):
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "Longbridge data is not an object")
    items = data.get("item")
    if not isinstance(items, list):
        raise TrendMonitorError(
            ErrorCategory.DATA_INCOMPLETE,
            "Longbridge data.item is not an array",
        )
    if not items:
        raise TrendMonitorError(ErrorCategory.EMPTY_DATA, "Longbridge item array is empty")
    if not all(isinstance(item, dict) for item in items):
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "Longbridge item is not an object")
    return items


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, f"invalid numeric value: {value!r}") from exc


def _epoch_ms(value: object) -> int | None:
    if value is None:
        return None
    try:
        epoch = int(value)
    except (TypeError, ValueError) as exc:
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "invalid Longbridge timestamp") from exc
    return epoch * 1000 if epoch < 10_000_000_000 else epoch


def _timestamp_ms(item: dict[str, Any]) -> int | None:
    epoch_ms = _epoch_ms(item.get("timestamp"))
    market_time = item.get("market_time")
    if epoch_ms is not None and market_time not in (None, ""):
        parsed = datetime.fromisoformat(str(market_time))
        if parsed.tzinfo is None:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                "Longbridge market_time must be timezone-aware",
            )
        expected = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        if parsed != expected:
            raise TrendMonitorError(
                ErrorCategory.DATA_CONFLICT,
                "Longbridge epoch and Asia/Shanghai market_time disagree",
            )
    return epoch_ms


def normalize_longbridge_quote(
    raw: dict[str, Any],
    *,
    instrument_id: str,
    name: str | None,
    asset_type: AssetType,
    source_trace: SourceTrace,
) -> list[MarketRecord]:
    result: list[MarketRecord] = []
    for item in _items(raw):
        result.append(
            MarketRecord(
                symbol=str(item.get("symbol") or ""),
                name=name,
                asset_type=asset_type,
                timestamp=_timestamp_ms(item),
                open=_number(item.get("open")),
                high=_number(item.get("high")),
                low=_number(item.get("low")),
                close=_number(item.get("last_done")),
                volume=_number(item.get("volume")),
                turnover=_number(item.get("turnover")),
                source="longbridge",
                period="realtime",
                source_trace=source_trace,
                instrument_id=instrument_id,
                previous_close=_number(item.get("prev_close")),
            )
        )
    return result


def normalize_longbridge_candlesticks(
    raw: dict[str, Any],
    *,
    instrument_id: str,
    symbol: str,
    name: str | None,
    asset_type: AssetType,
    period: str,
    source_trace: SourceTrace,
) -> list[MarketRecord]:
    result: list[MarketRecord] = []
    for item in _items(raw):
        result.append(
            MarketRecord(
                symbol=symbol,
                name=name,
                asset_type=asset_type,
                timestamp=_timestamp_ms(item),
                open=_number(item.get("open")),
                high=_number(item.get("high")),
                low=_number(item.get("low")),
                close=_number(item.get("close")),
                volume=_number(item.get("volume")),
                turnover=_number(item.get("turnover")),
                source="longbridge",
                period=period,
                source_trace=source_trace,
                instrument_id=instrument_id,
                trade_session=(
                    str(item["trade_session"])
                    if item.get("trade_session") not in (None, "")
                    else None
                ),
            )
        )
    return sorted(result, key=lambda record: record.timestamp or 0)
