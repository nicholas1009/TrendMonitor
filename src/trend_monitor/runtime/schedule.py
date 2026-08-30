"""Timezone-safe A-share 60m period resolution."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from trend_monitor.schemas.runtime import ScheduledPeriod


def due_periods(
    as_of: datetime,
    *,
    trading_day: date,
    periods: Iterable[dict[str, str]],
    buffer_minutes: int,
    live_grace_minutes: int,
    historical_execution: bool = False,
) -> tuple[ScheduledPeriod, ...]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    output = []
    for definition in periods:
        start = datetime.combine(trading_day, time.fromisoformat(definition["start"]), tzinfo=as_of.tzinfo)
        end = datetime.combine(trading_day, time.fromisoformat(definition["end"]), tzinfo=as_of.tzinfo)
        scheduled = end + timedelta(minutes=buffer_minutes)
        if scheduled > as_of:
            continue
        live = not historical_execution and as_of <= scheduled + timedelta(minutes=live_grace_minutes)
        mode = "LIVE_SCHEDULED" if live else "CATCH_UP"
        eligibility = "ELIGIBLE" if live else "CATCH_UP_STALE_FUTURE_POLICY"
        output.append(
            ScheduledPeriod(
                trading_date=trading_day.isoformat(),
                period_start=start.isoformat(),
                period_end=end.isoformat(),
                scheduled_at=scheduled.isoformat(),
                execution_mode=mode,
                notification_eligibility=eligibility,
            )
        )
    return tuple(output)


def period_identity(period: ScheduledPeriod, rules_versions: dict[str, str]) -> str:
    signature = ",".join(f"{key}={rules_versions[key]}" for key in sorted(rules_versions))
    return f"{period.trading_date}|{period.period_end}|{signature}"
