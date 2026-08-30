"""Reconcile derived System Bars with Provider Daily bars."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import MarketRecord, SystemBar
from trend_monitor.validation.common import record_timestamp


class ReconciliationStatus(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


DEFAULT_VOLUME_RELATIVE_TOLERANCE = Decimal("0.001")
DEFAULT_TURNOVER_RELATIVE_TOLERANCE = Decimal("0.001")


def _relative_gap(actual: Decimal, expected: Decimal) -> Decimal:
    if expected == 0:
        return Decimal("0") if actual == 0 else Decimal("Infinity")
    return abs(actual - expected) / abs(expected)


def reconcile_system_bars(
    system_bars: Iterable[SystemBar],
    daily_records: Iterable[MarketRecord],
    *,
    period: str,
    volume_relative_tolerance: Decimal = DEFAULT_VOLUME_RELATIVE_TOLERANCE,
    turnover_relative_tolerance: Decimal = DEFAULT_TURNOVER_RELATIVE_TOLERANCE,
) -> dict[str, object]:
    expected_count = {"15m": 16, "60m": 4}.get(period)
    if expected_count is None:
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"unsupported period: {period}")
    grouped: dict[str, list[SystemBar]] = defaultdict(list)
    for bar in system_bars:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        day = datetime.fromtimestamp(bar.system_start / 1000, tz=timezone.utc).astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date().isoformat()
        grouped[day].append(bar)
    daily = {record_timestamp(item).date().isoformat(): item for item in daily_records}
    common = sorted(set(grouped) & set(daily))
    if not common:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "no common System/Daily dates")

    reports: list[dict[str, object]] = []
    for day in common:
        bars = sorted(grouped[day], key=lambda item: item.system_start)
        if len(bars) != expected_count:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"{day} has {len(bars)} System Bars; expected {expected_count}",
            )
        record = daily[day]
        if any(getattr(record, field) is None for field in ("open", "high", "low", "close", "volume", "turnover")):
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, f"Daily fields missing on {day}")
        system_values = {
            "open": Decimal(str(bars[0].open)),
            "high": max(Decimal(str(item.high)) for item in bars),
            "low": min(Decimal(str(item.low)) for item in bars),
            "close": Decimal(str(bars[-1].close)),
            "volume": sum((Decimal(str(item.volume)) for item in bars), Decimal("0")),
            "turnover": sum((Decimal(str(item.turnover)) for item in bars), Decimal("0")),
        }
        daily_values = {
            field: Decimal(str(getattr(record, field)))
            for field in ("open", "high", "low", "close", "volume", "turnover")
        }
        price_matches = {
            field: system_values[field] == daily_values[field]
            for field in ("open", "high", "low", "close")
        }
        volume_gap = _relative_gap(system_values["volume"], daily_values["volume"])
        turnover_gap = _relative_gap(system_values["turnover"], daily_values["turnover"])
        if all(price_matches.values()) and volume_gap <= volume_relative_tolerance and turnover_gap <= turnover_relative_tolerance:
            status = ReconciliationStatus.PASS
        elif price_matches["open"] and price_matches["close"] and volume_gap <= Decimal("0.01") and turnover_gap <= Decimal("0.01"):
            status = ReconciliationStatus.REVIEW
        else:
            status = ReconciliationStatus.FAIL
        reports.append(
            {
                "date": day,
                "status": status.value,
                "quality_status": (
                    "DATA_INCOMPLETE"
                    if status is ReconciliationStatus.FAIL
                    else "REVIEW_REQUIRED"
                    if status is ReconciliationStatus.REVIEW
                    else "VALIDATED"
                ),
                "system": {key: str(value) for key, value in system_values.items()},
                "daily": {key: str(value) for key, value in daily_values.items()},
                "price_matches": price_matches,
                "volume_relative_gap": str(volume_gap),
                "turnover_relative_gap": str(turnover_gap),
            }
        )
    overall = (
        ReconciliationStatus.FAIL
        if any(item["status"] == ReconciliationStatus.FAIL.value for item in reports)
        else ReconciliationStatus.REVIEW
        if any(item["status"] == ReconciliationStatus.REVIEW.value for item in reports)
        else ReconciliationStatus.PASS
    )
    status_counts = {
        status.value: sum(item["status"] == status.value for item in reports)
        for status in ReconciliationStatus
    }
    price_match_days = {
        field: sum(bool(item["price_matches"][field]) for item in reports)
        for field in ("open", "high", "low", "close")
    }
    max_price_absolute_gap = {
        field: str(max(
            abs(Decimal(item["system"][field]) - Decimal(item["daily"][field]))
            for item in reports
        ))
        for field in ("open", "high", "low", "close")
    }
    return {
        "period": period,
        "status": overall.value,
        "quality_status": (
            "DATA_INCOMPLETE"
            if overall is ReconciliationStatus.FAIL
            else "REVIEW_REQUIRED"
            if overall is ReconciliationStatus.REVIEW
            else "VALIDATED"
        ),
        "days": reports,
        "summary": {
            "days": len(reports),
            "status_counts": status_counts,
            "price_match_days": price_match_days,
            "max_price_absolute_gap": max_price_absolute_gap,
            "max_volume_relative_gap": str(max(Decimal(item["volume_relative_gap"]) for item in reports)),
            "max_turnover_relative_gap": str(max(Decimal(item["turnover_relative_gap"]) for item in reports)),
        },
        "volume_relative_tolerance": str(volume_relative_tolerance),
        "turnover_relative_tolerance": str(turnover_relative_tolerance),
    }
