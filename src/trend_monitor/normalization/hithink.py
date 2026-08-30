from __future__ import annotations

from typing import Any

from trend_monitor.providers.hithink.errors import ErrorCategory, HithinkProviderError
from trend_monitor.schemas import AssetType, MarketRecord, SourceTrace


def _response_data(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    if not isinstance(data, dict):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "raw response data is not an object"
        )
    return data


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("item")
    if not isinstance(items, list):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "raw response data.item is not an array"
        )
    if not items:
        raise HithinkProviderError(ErrorCategory.EMPTY_DATA, "raw response item array is empty")
    if not all(isinstance(item, dict) for item in items):
        raise HithinkProviderError(
            ErrorCategory.INVALID_DATA, "raw response item contains a non-object"
        )
    return items


def normalize_snapshot(
    raw: dict[str, Any],
    *,
    asset_type: AssetType,
    names: dict[str, str] | None = None,
    source_trace: SourceTrace | None = None,
    instrument_id: str | None = None,
) -> list[MarketRecord]:
    data = _response_data(raw)
    timestamp = data.get("timestamp")
    result: list[MarketRecord] = []
    for item in _items(data):
        symbol = item.get("thscode")
        result.append(
            MarketRecord(
                symbol=symbol,
                name=item.get("name") or (names or {}).get(symbol),
                asset_type=asset_type,
                timestamp=timestamp,
                open=item.get("open_price"),
                high=item.get("high_price"),
                low=item.get("low_price"),
                close=item.get("last_price"),
                volume=item.get("volume"),
                turnover=item.get("turnover"),
                source="hithink",
                period="realtime",
                source_trace=source_trace,
                instrument_id=instrument_id,
            )
        )
    return result


def normalize_historical(
    raw: dict[str, Any],
    *,
    symbol: str,
    name: str | None,
    asset_type: AssetType,
    period: str = "1d",
    source_trace: SourceTrace | None = None,
    instrument_id: str | None = None,
) -> list[MarketRecord]:
    data = _response_data(raw)
    result: list[MarketRecord] = []
    for item in _items(data):
        result.append(
            MarketRecord(
                symbol=symbol,
                name=name,
                asset_type=asset_type,
                timestamp=item.get("date_ms"),
                open=item.get("open_price"),
                high=item.get("high_price"),
                low=item.get("low_price"),
                close=item.get("close_price"),
                volume=item.get("volume"),
                turnover=item.get("turnover"),
                source="hithink",
                period=period,
                source_trace=source_trace,
                instrument_id=instrument_id,
            )
        )
    return result
