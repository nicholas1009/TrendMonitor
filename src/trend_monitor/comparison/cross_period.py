"""Deterministic diagnostic aggregation and field-level error distributions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import MarketRecord, SystemBar
from trend_monitor.validation import record_timestamp, source_bar_id


SHANGHAI = ZoneInfo("Asia/Shanghai")
FIELDS = ("open", "high", "low", "close", "volume", "turnover")


def _minute_times(start: time, end: time) -> tuple[str, ...]:
    current = datetime.combine(datetime.min.date(), start)
    stop = datetime.combine(datetime.min.date(), end)
    values = []
    while current <= stop:
        values.append(current.strftime("%H:%M"))
        current += timedelta(minutes=1)
    return tuple(values)


EXPECTED_1M_TIMES = _minute_times(time(9, 30), time(11, 29)) + _minute_times(
    time(13, 0), time(15, 0)
)


@dataclass(frozen=True, slots=True)
class DiagnosticBar:
    instrument_id: str
    period: str
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal
    source_bar_ids: tuple[str, ...]
    source_raw_paths: tuple[str, ...]

    @property
    def day(self) -> str:
        return datetime.fromtimestamp(self.timestamp / 1000, tz=timezone.utc).astimezone(
            SHANGHAI
        ).date().isoformat()

    def values(self) -> dict[str, Decimal]:
        return {field: getattr(self, field) for field in FIELDS}

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "period": self.period,
            "timestamp": self.timestamp,
            **{field: str(getattr(self, field)) for field in FIELDS},
            "source_bar_ids": list(self.source_bar_ids),
            "source_raw_paths": list(self.source_raw_paths),
        }


def _decimal(value: float | None, field: str) -> Decimal:
    if value is None:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, f"{field} is missing")
    return Decimal(str(value))


def _aggregate(records: list[MarketRecord], *, period: str, timestamp: int) -> DiagnosticBar:
    if not records:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "diagnostic group is empty")
    instrument_ids = {item.instrument_id for item in records}
    if len(instrument_ids) != 1 or None in instrument_ids:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "diagnostic identity is missing")
    raw_paths = []
    for record in records:
        if record.source_trace is None or not record.source_trace.raw_path:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "diagnostic lineage is missing")
        if record.source_trace.raw_path not in raw_paths:
            raw_paths.append(record.source_trace.raw_path)
    return DiagnosticBar(
        instrument_id=str(records[0].instrument_id),
        period=period,
        timestamp=timestamp,
        open=_decimal(records[0].open, "open"),
        high=max(
            _decimal(value, "high")
            for item in records
            for value in (item.open, item.high, item.low, item.close)
        ),
        low=min(
            _decimal(value, "low")
            for item in records
            for value in (item.open, item.high, item.low, item.close)
        ),
        close=_decimal(records[-1].close, "close"),
        volume=sum((_decimal(item.volume, "volume") for item in records), Decimal("0")),
        turnover=sum((_decimal(item.turnover, "turnover") for item in records), Decimal("0")),
        source_bar_ids=tuple(source_bar_id(item) for item in records),
        source_raw_paths=tuple(raw_paths),
    )


def aggregate_one_minute(
    records: Iterable[MarketRecord], *, target_period: str, allow_missing_minutes: bool = False
) -> tuple[DiagnosticBar, ...]:
    """Diagnostic-only 1m aggregation; never used as a Provider fallback."""
    if target_period not in {"15m", "60m", "1d"}:
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"unsupported target: {target_period}")
    ordered = sorted(records, key=lambda item: item.timestamp or 0)
    grouped_days: dict[str, list[MarketRecord]] = defaultdict(list)
    for record in ordered:
        if record.period != "1m":
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "diagnostic source must be 1m")
        grouped_days[record_timestamp(record).date().isoformat()].append(record)
    result: list[DiagnosticBar] = []
    width = 15 if target_period == "15m" else 60
    for day, day_records in sorted(grouped_days.items()):
        times = tuple(record_timestamp(item).strftime("%H:%M") for item in day_records)
        valid_flexible_schedule = (
            allow_missing_minutes
            and len(times) == len(set(times))
            and set(times) <= set(EXPECTED_1M_TIMES)
        )
        if times != EXPECTED_1M_TIMES and not valid_flexible_schedule:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"incomplete 1m schedule on {day}: rows={len(times)}",
            )
        if target_period == "1d":
            result.append(_aggregate(day_records, period="1d", timestamp=day_records[0].timestamp or 0))
            continue
        buckets: dict[int, list[MarketRecord]] = defaultdict(list)
        for record in day_records:
            local = record_timestamp(record)
            if local.time().replace(tzinfo=None) == time(15, 0):
                bucket = local.replace(second=0, microsecond=0)
            else:
                session_start = local.replace(
                    hour=9 if local.hour < 12 else 13,
                    minute=30 if local.hour < 12 else 0,
                    second=0,
                    microsecond=0,
                )
                offset = int((local - session_start).total_seconds() // 60)
                bucket = session_start + timedelta(minutes=(offset // width) * width)
            buckets[int(bucket.timestamp() * 1000)].append(record)
        for timestamp, bucket_records in sorted(buckets.items()):
            result.append(
                _aggregate(bucket_records, period=target_period, timestamp=timestamp)
            )
        expected_bucket_count = 17 if target_period == "15m" else 5
        actual_bucket_count = len(buckets)
        if actual_bucket_count != expected_bucket_count:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"1m diagnostic buckets on {day}: {actual_bucket_count}/{expected_bucket_count}",
            )
    return tuple(result)


def direct_records_as_diagnostic(records: Iterable[MarketRecord]) -> tuple[DiagnosticBar, ...]:
    result = []
    for record in records:
        if record.instrument_id is None or record.timestamp is None:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "direct identity is missing")
        if record.source_trace is None or not record.source_trace.raw_path:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "direct lineage is missing")
        result.append(
            DiagnosticBar(
                instrument_id=record.instrument_id,
                period=record.period,
                timestamp=record.timestamp,
                open=_decimal(record.open, "open"),
                high=_decimal(record.high, "high"),
                low=_decimal(record.low, "low"),
                close=_decimal(record.close, "close"),
                volume=_decimal(record.volume, "volume"),
                turnover=_decimal(record.turnover, "turnover"),
                source_bar_ids=(source_bar_id(record),),
                source_raw_paths=(record.source_trace.raw_path,),
            )
        )
    return tuple(result)


def aggregate_system_daily(bars: Iterable[SystemBar]) -> tuple[DiagnosticBar, ...]:
    grouped: dict[str, list[SystemBar]] = defaultdict(list)
    for bar in bars:
        day = datetime.fromtimestamp(bar.system_start / 1000, tz=timezone.utc).astimezone(
            SHANGHAI
        ).date().isoformat()
        grouped[day].append(bar)
    result = []
    for _, day_bars in sorted(grouped.items()):
        ordered = sorted(day_bars, key=lambda item: item.system_start)
        raw_paths = []
        source_ids = []
        for bar in ordered:
            for value in bar.source_raw_paths:
                if value not in raw_paths:
                    raw_paths.append(value)
            source_ids.extend(bar.source_bar_ids)
        result.append(
            DiagnosticBar(
                instrument_id=ordered[0].instrument_id,
                period="1d",
                timestamp=ordered[0].system_start,
                open=Decimal(str(ordered[0].open)),
                high=max(Decimal(str(item.high)) for item in ordered),
                low=min(Decimal(str(item.low)) for item in ordered),
                close=Decimal(str(ordered[-1].close)),
                volume=sum((Decimal(str(item.volume)) for item in ordered), Decimal("0")),
                turnover=sum((Decimal(str(item.turnover)) for item in ordered), Decimal("0")),
                source_bar_ids=tuple(source_ids),
                source_raw_paths=tuple(raw_paths),
            )
        )
    return tuple(result)


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    position = (Decimal(len(ordered) - 1) * percentile) / Decimal("100")
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _distribution(values: list[Decimal]) -> dict[str, str | int]:
    if not values:
        return {key: "0" for key in ("mean", "median", "p90", "p95", "p99", "max")} | {"count": 0}
    return {
        "count": len(values),
        "mean": str(sum(values, Decimal("0")) / Decimal(len(values))),
        "median": str(_percentile(values, Decimal("50"))),
        "p90": str(_percentile(values, Decimal("90"))),
        "p95": str(_percentile(values, Decimal("95"))),
        "p99": str(_percentile(values, Decimal("99"))),
        "max": str(max(values)),
    }


def compare_diagnostic_bars(
    left: Iterable[DiagnosticBar],
    right: Iterable[DiagnosticBar],
    *,
    key: Callable[[DiagnosticBar], object] | None = None,
) -> dict[str, object]:
    identity = key or (lambda item: item.timestamp)
    left_map = {identity(item): item for item in left}
    right_map = {identity(item): item for item in right}
    common = sorted(set(left_map) & set(right_map))
    if not common:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "no common diagnostic bars")
    fields: dict[str, object] = {}
    mismatches: list[dict[str, object]] = []
    for field in FIELDS:
        absolute: list[Decimal] = []
        relative: list[Decimal] = []
        mismatch_count = 0
        for item_key in common:
            left_value = getattr(left_map[item_key], field)
            right_value = getattr(right_map[item_key], field)
            difference = abs(left_value - right_value)
            absolute.append(difference)
            if right_value == 0:
                if difference == 0:
                    relative.append(Decimal("0"))
            else:
                relative.append(difference / abs(right_value))
            if difference != 0:
                mismatch_count += 1
                if len(mismatches) < 200:
                    mismatches.append(
                        {
                            "key": str(item_key),
                            "field": field,
                            "left": str(left_value),
                            "right": str(right_value),
                            "absolute_difference": str(difference),
                            "left_raw_paths": list(left_map[item_key].source_raw_paths),
                            "right_raw_paths": list(right_map[item_key].source_raw_paths),
                        }
                    )
        fields[field] = {
            "count": len(common),
            "exact_match_count": len(common) - mismatch_count,
            "mismatch_count": mismatch_count,
            "mismatch_frequency": str(Decimal(mismatch_count) / Decimal(len(common))),
            "absolute_difference": _distribution(absolute),
            "relative_difference": _distribution(relative),
        }
    return {
        "common_count": len(common),
        "fields": fields,
        "mismatches": mismatches,
    }
