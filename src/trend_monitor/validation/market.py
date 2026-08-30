from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from trend_monitor.providers.hithink.errors import ErrorCategory, HithinkProviderError
from trend_monitor.schemas import MarketRecord


def validate_raw_items(raw: dict[str, Any]) -> None:
    data = raw.get("data")
    if not isinstance(data, dict) or "item" not in data:
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "response is missing data.item"
        )
    items = data["item"]
    if not isinstance(items, list):
        raise HithinkProviderError(
            ErrorCategory.INVALID_DATA, "response data.item is not an array"
        )
    if not items:
        raise HithinkProviderError(ErrorCategory.EMPTY_DATA, "response data.item is empty")


def validate_market_record(record: MarketRecord) -> None:
    missing: list[str] = []
    if not record.symbol:
        missing.append("symbol")
    if record.timestamp is None:
        missing.append("timestamp")
    for field in ("open", "high", "low", "close", "volume"):
        if getattr(record, field) is None:
            missing.append(field)
    if missing:
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE,
            f"missing required market fields: {', '.join(missing)}",
        )

    assert record.timestamp is not None
    try:
        datetime.fromtimestamp(record.timestamp / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise HithinkProviderError(
            ErrorCategory.INVALID_DATA, "timestamp is outside the supported range"
        ) from exc
    if record.timestamp <= 0:
        raise HithinkProviderError(ErrorCategory.INVALID_DATA, "timestamp must be positive")
    assert record.high is not None and record.low is not None
    if record.high < record.low:
        raise HithinkProviderError(ErrorCategory.INVALID_DATA, "high is lower than low")
    assert record.volume is not None
    if record.volume < 0:
        raise HithinkProviderError(ErrorCategory.INVALID_DATA, "volume is negative")


def validate_records(records: Iterable[MarketRecord]) -> None:
    materialized = list(records)
    if not materialized:
        raise HithinkProviderError(ErrorCategory.EMPTY_DATA, "record array is empty")
    for record in materialized:
        validate_market_record(record)
