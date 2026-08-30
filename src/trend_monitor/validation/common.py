"""Provider-neutral market-record validation."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import MarketRecord


SHANGHAI = ZoneInfo("Asia/Shanghai")


def record_timestamp(record: MarketRecord) -> datetime:
    assert record.timestamp is not None
    return datetime.fromtimestamp(record.timestamp / 1000, tz=timezone.utc).astimezone(SHANGHAI)


def validate_common_records(
    records: Iterable[MarketRecord],
    *,
    require_strict_time_order: bool = False,
    validate_a_share_session: bool = False,
    require_trade_session: bool = False,
) -> None:
    materialized = list(records)
    if not materialized:
        raise TrendMonitorError(ErrorCategory.EMPTY_DATA, "record array is empty")

    timestamps: list[int] = []
    for record in materialized:
        missing = [
            field
            for field in ("symbol", "timestamp", "open", "high", "low", "close", "volume")
            if getattr(record, field) is None or getattr(record, field) == ""
        ]
        if missing:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"missing required market fields: {', '.join(missing)}",
            )
        assert record.timestamp is not None
        assert record.open is not None and record.high is not None
        assert record.low is not None and record.close is not None
        assert record.volume is not None
        if record.timestamp <= 0:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "timestamp must be positive")
        if record.high < max(record.low, record.open, record.close):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "high is below low/open/close")
        if record.low > min(record.high, record.open, record.close):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "low is above high/open/close")
        if record.volume < 0:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "volume is negative")
        timestamps.append(record.timestamp)

        if require_trade_session and not record.trade_session:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                "minute bar trade_session is missing",
            )

        if validate_a_share_session:
            local_time = record_timestamp(record).time()
            morning = time(9, 30) <= local_time <= time(11, 30)
            afternoon = time(13, 0) <= local_time <= time(15, 0)
            if not (morning or afternoon):
                raise TrendMonitorError(
                    ErrorCategory.INVALID_DATA,
                    f"minute bar timestamp outside A-share sessions: {local_time}",
                )

    if require_strict_time_order:
        if timestamps != sorted(timestamps):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "timestamps are not increasing")
        if len(timestamps) != len(set(timestamps)):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "duplicate timestamps detected")
