"""Evidence-oriented checks for Longbridge A-share close-bar structure."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import MarketRecord


SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPECTED_TIMES = {
    "15m": (
        "09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15",
        "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45",
        "15:00",
    ),
    "60m": ("09:30", "10:30", "13:00", "14:00", "15:00"),
}


def _local(record: MarketRecord) -> datetime:
    if record.timestamp is None:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "timestamp is missing")
    return datetime.fromtimestamp(record.timestamp / 1000, tz=timezone.utc).astimezone(SHANGHAI)


def _bar_fields(record: MarketRecord) -> dict[str, object]:
    return {
        "timestamp_shanghai": _local(record).isoformat(),
        "open": record.open,
        "high": record.high,
        "low": record.low,
        "close": record.close,
        "volume": record.volume,
        "turnover": record.turnover,
        "trade_session": record.trade_session,
    }


def _sum(records: list[MarketRecord], field: str) -> Decimal:
    return sum((Decimal(str(getattr(item, field))) for item in records), Decimal("0"))


def analyze_close_bar_structure(
    minute_records: list[MarketRecord],
    daily_records: list[MarketRecord],
    *,
    period: str,
    minimum_days: int = 5,
) -> dict[str, object]:
    if period not in EXPECTED_TIMES:
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"unsupported minute period: {period}")

    grouped: dict[str, list[MarketRecord]] = defaultdict(list)
    for record in minute_records:
        grouped[_local(record).date().isoformat()].append(record)
    daily = {_local(record).date().isoformat(): record for record in daily_records}
    candidates = sorted(set(grouped) & set(daily))
    complete = [
        day
        for day in candidates
        if tuple(_local(item).strftime("%H:%M") for item in sorted(grouped[day], key=_local))
        == EXPECTED_TIMES[period]
    ]
    selected = complete[-minimum_days:]
    if len(selected) < minimum_days:
        raise TrendMonitorError(
            ErrorCategory.DATA_INCOMPLETE,
            f"requires {minimum_days} complete {period} trading days; found {len(selected)}",
        )

    days: list[dict[str, object]] = []
    for day in selected:
        bars = sorted(grouped[day], key=_local)
        closing = [item for item in bars if _local(item).strftime("%H:%M") == "15:00"]
        if len(closing) != 1:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"expected one 15:00 bar on {day}; found {len(closing)}",
            )
        close_bar = closing[0]
        previous = bars[-2]
        without_close = bars[:-1]
        daily_record = daily[day]
        all_volume = _sum(bars, "volume")
        without_volume = _sum(without_close, "volume")
        all_turnover = _sum(bars, "turnover")
        without_turnover = _sum(without_close, "turnover")
        daily_volume = Decimal(str(daily_record.volume))
        daily_turnover = Decimal(str(daily_record.turnover))
        days.append(
            {
                "date": day,
                "times": [_local(item).strftime("%H:%M") for item in bars],
                "schedule_matches": True,
                "previous": _bar_fields(previous),
                "closing_1500": _bar_fields(close_bar),
                "daily": _bar_fields(daily_record),
                "sum_volume_all": str(all_volume),
                "sum_volume_without_1500": str(without_volume),
                "daily_volume": str(daily_volume),
                "absolute_volume_gap_all": str(abs(daily_volume - all_volume)),
                "absolute_volume_gap_without_1500": str(abs(daily_volume - without_volume)),
                "sum_turnover_all": str(all_turnover),
                "sum_turnover_without_1500": str(without_turnover),
                "daily_turnover": str(daily_turnover),
                "absolute_turnover_gap_all": str(abs(daily_turnover - all_turnover)),
                "absolute_turnover_gap_without_1500": str(abs(daily_turnover - without_turnover)),
                "close_matches_daily": close_bar.close == daily_record.close,
                "flat_ohlc": len({close_bar.open, close_bar.high, close_bar.low, close_bar.close}) == 1,
                "including_1500_is_closer_by_volume": (
                    abs(daily_volume - all_volume) <= abs(daily_volume - without_volume)
                ),
                "including_1500_is_closer_by_turnover": (
                    abs(daily_turnover - all_turnover) <= abs(daily_turnover - without_turnover)
                ),
            }
        )

    return {
        "period": period,
        "days": days,
        "all_schedules_match": all(item["schedule_matches"] for item in days),
        "all_have_positive_1500_volume": all(
            float(item["closing_1500"]["volume"]) > 0 for item in days
        ),
        "all_1500_closes_match_daily": all(item["close_matches_daily"] for item in days),
        "all_1500_flat_ohlc": all(item["flat_ohlc"] for item in days),
        "including_1500_always_closer_by_volume": all(
            item["including_1500_is_closer_by_volume"] for item in days
        ),
        "including_1500_always_closer_by_turnover": all(
            item["including_1500_is_closer_by_turnover"] for item in days
        ),
        "trade_sessions": sorted({
            str(item["closing_1500"]["trade_session"])
            for item in days
        }),
    }
