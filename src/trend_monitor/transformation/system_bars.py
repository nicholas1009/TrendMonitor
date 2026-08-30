"""Build fixed A-share TrendMonitor System Bars from Longbridge Source Bars."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Iterable

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import (
    MarketRecord,
    SourceQualityStatus,
    SystemBar,
    SystemBarQualityStatus,
    SystemBarTransformation,
)
from trend_monitor.validation.common import record_timestamp
from trend_monitor.validation.minute_quality import (
    SourceBarAssessment,
    source_bar_id,
    validate_source_minute_records,
)
from trend_monitor.validation.minute_structure import EXPECTED_TIMES
from zoneinfo import ZoneInfo


SYSTEM_COUNTS = {"15m": 16, "60m": 4}
SHANGHAI = ZoneInfo("Asia/Shanghai")
SYSTEM_END_TIMES = {
    "15m": (
        "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
        "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00",
    ),
    "60m": ("10:30", "11:30", "14:00", "15:00"),
}


def _lineage(records: list[MarketRecord]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    providers = {item.source for item in records}
    if len(providers) != 1:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "System Bar has mixed providers")
    raw_paths: list[str] = []
    for record in records:
        if record.source_trace is None or not record.source_trace.raw_path:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "System Bar source trace is missing")
        if record.source_trace.raw_path not in raw_paths:
            raw_paths.append(record.source_trace.raw_path)
    return providers.pop(), tuple(source_bar_id(item) for item in records), tuple(raw_paths)


def _make_bar(
    records: list[MarketRecord],
    assessments: dict[str, SourceBarAssessment],
    *,
    period: str,
    merge_closing: bool,
) -> SystemBar:
    first, last = records[0], records[-1]
    if first.instrument_id is None:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "instrument_id is missing")
    values = [
        value
        for record in records
        for value in (record.open, record.high, record.low, record.close, record.volume, record.turnover)
    ]
    if any(value is None for value in values):
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "System Bar input value is missing")
    assert first.open is not None and last.close is not None
    assert all(item.high is not None and item.low is not None for item in records)
    assert all(item.volume is not None and item.turnover is not None for item in records)
    provider, bar_ids, raw_paths = _lineage(records)
    boundary_quirk = any(
        assessments[source_bar_id(item)].quality_status
        is SourceQualityStatus.SOURCE_BOUNDARY_QUIRK
        for item in records
    )
    transformation = (
        SystemBarTransformation.MERGE_CLOSING_BUCKET
        if merge_closing
        else SystemBarTransformation.SOURCE_BOUNDARY_ENVELOPE
        if boundary_quirk
        else SystemBarTransformation.DIRECT_NORMALIZED
    )
    quality = (
        SystemBarQualityStatus.SOURCE_BOUNDARY_QUIRK
        if boundary_quirk
        else SystemBarQualityStatus.MERGED_CLOSING_BUCKET
        if merge_closing
        else SystemBarQualityStatus.DIRECT_NORMALIZED
    )
    start = record_timestamp(first)
    if merge_closing:
        end = record_timestamp(last)
    else:
        end = start + timedelta(minutes=15 if period == "15m" else 60)
    return SystemBar(
        instrument_id=first.instrument_id,
        period=period,
        system_start=int(start.timestamp() * 1000),
        system_end=int(end.timestamp() * 1000),
        open=first.open,
        # A Source Bar classified as SOURCE_BOUNDARY_QUIRK keeps its original
        # fields untouched. The derived System Bar uses the OHLC envelope so
        # its high/low include the Provider's own opening price. Lineage and
        # transformation make this deterministic adjustment explicit.
        high=max(float(value) for item in records for value in (item.open, item.high, item.low, item.close)),
        low=min(float(value) for item in records for value in (item.open, item.high, item.low, item.close)),
        close=last.close,
        volume=sum(float(item.volume) for item in records),
        turnover=sum(float(item.turnover) for item in records),
        source_provider=provider,
        source_bar_ids=bar_ids,
        source_raw_paths=raw_paths,
        transformation=transformation,
        quality_status=quality,
    )


def build_system_bars(
    records: Iterable[MarketRecord],
    *,
    period: str,
    allowed_negative_fields: frozenset[str] = frozenset(),
) -> tuple[SystemBar, ...]:
    """Create 16 x 15m or 4 x 60m System Bars per complete trading day."""
    if period not in SYSTEM_COUNTS:
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"unsupported System Bar period: {period}")
    ordered = sorted(records, key=lambda item: item.timestamp or 0)
    assessments = {
        item.source_bar_id: item
        for item in validate_source_minute_records(
            ordered, allowed_negative_fields=allowed_negative_fields
        )
    }
    grouped: dict[str, list[MarketRecord]] = defaultdict(list)
    for record in ordered:
        if record.period != period:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "mixed Source Bar periods")
        grouped[record_timestamp(record).date().isoformat()].append(record)

    result: list[SystemBar] = []
    expected = EXPECTED_TIMES[period]
    for day, day_records in sorted(grouped.items()):
        times = tuple(record_timestamp(item).strftime("%H:%M") for item in day_records)
        if times != expected:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"incomplete {period} Source schedule on {day}: {times}",
            )
        for record in day_records[:-2]:
            result.append(
                _make_bar([record], assessments, period=period, merge_closing=False)
            )
        result.append(
            _make_bar(day_records[-2:], assessments, period=period, merge_closing=True)
        )
        if len(result) % SYSTEM_COUNTS[period] != 0:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "unexpected System Bar count")
    return tuple(result)


def expected_completed_system_bar_count(
    *, period: str, trading_day: date, as_of: datetime
) -> int:
    """Return how many fixed System periods should be complete at ``as_of``."""
    if period not in SYSTEM_END_TIMES:
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"unsupported System Bar period: {period}")
    if as_of.tzinfo is None:
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of must be timezone-aware")
    local = as_of.astimezone(SHANGHAI)
    if trading_day < local.date():
        return SYSTEM_COUNTS[period]
    if trading_day > local.date():
        return 0
    return sum(
        datetime.combine(trading_day, time.fromisoformat(value), tzinfo=SHANGHAI) <= local
        for value in SYSTEM_END_TIMES[period]
    )


def build_completed_system_bars(
    records: Iterable[MarketRecord],
    *,
    period: str,
    as_of: datetime,
    allowed_negative_fields: frozenset[str] = frozenset(),
) -> tuple[SystemBar, ...]:
    """Build only completed periods from a valid Source-Bar prefix.

    This applies the already-verified 1:1/Closing-Bucket transformation. It is
    not quote sampling or LOCAL_AGGREGATION.
    """
    if period not in SYSTEM_COUNTS:
        raise TrendMonitorError(ErrorCategory.UNSUPPORTED, f"unsupported System Bar period: {period}")
    if as_of.tzinfo is None:
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of must be timezone-aware")
    ordered = sorted(records, key=lambda item: item.timestamp or 0)
    assessments = {
        item.source_bar_id: item
        for item in validate_source_minute_records(
            ordered, allowed_negative_fields=allowed_negative_fields
        )
    }
    grouped: dict[str, list[MarketRecord]] = defaultdict(list)
    for record in ordered:
        if record.period != period:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "mixed Source Bar periods")
        grouped[record_timestamp(record).date().isoformat()].append(record)

    result: list[SystemBar] = []
    width = 15 if period == "15m" else 60
    closing_start = "14:45" if period == "15m" else "14:00"
    for day, day_records in sorted(grouped.items()):
        times = tuple(record_timestamp(item).strftime("%H:%M") for item in day_records)
        expected = EXPECTED_TIMES[period]
        if times != expected[: len(times)]:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"non-prefix {period} Source schedule on {day}: {times}",
            )
        by_time = {
            record_timestamp(item).strftime("%H:%M"): item for item in day_records
        }
        for record in day_records:
            local = record_timestamp(record)
            label = local.strftime("%H:%M")
            if label == "15:00":
                continue
            if label == closing_start:
                closing = by_time.get("15:00")
                if closing is not None and record_timestamp(closing) <= as_of.astimezone(SHANGHAI):
                    result.append(
                        _make_bar(
                            [record, closing], assessments, period=period, merge_closing=True
                        )
                    )
                continue
            if local + timedelta(minutes=width) <= as_of.astimezone(SHANGHAI):
                result.append(
                    _make_bar([record], assessments, period=period, merge_closing=False)
                )
    return tuple(result)
