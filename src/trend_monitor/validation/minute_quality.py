"""Strict Source Bar validation with a narrow, evidence-based 09:30 quirk."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Iterable

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import MarketRecord, SourceQualityStatus
from trend_monitor.validation.common import record_timestamp


class OhlcAnomalyType(StrEnum):
    HIGH_BELOW_LOW = "HIGH_BELOW_LOW"
    HIGH_BELOW_OPEN = "HIGH_BELOW_OPEN"
    HIGH_BELOW_CLOSE = "HIGH_BELOW_CLOSE"
    LOW_ABOVE_OPEN = "LOW_ABOVE_OPEN"
    LOW_ABOVE_CLOSE = "LOW_ABOVE_CLOSE"


@dataclass(frozen=True, slots=True)
class SourceBarAssessment:
    source_bar_id: str
    quality_status: SourceQualityStatus
    anomaly_types: tuple[OhlcAnomalyType, ...]


def source_bar_id(record: MarketRecord) -> str:
    if record.timestamp is None:
        return f"{record.source}:{record.symbol}:{record.period}:missing-timestamp"
    return f"{record.source}:{record.symbol}:{record.period}:{record.timestamp}"


def ohlc_anomalies(record: MarketRecord) -> tuple[OhlcAnomalyType, ...]:
    if any(
        getattr(record, field) is None
        for field in ("open", "high", "low", "close")
    ):
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "Source Bar OHLC is missing")
    assert record.open is not None and record.high is not None
    assert record.low is not None and record.close is not None
    anomalies: list[OhlcAnomalyType] = []
    if record.high < record.low:
        anomalies.append(OhlcAnomalyType.HIGH_BELOW_LOW)
    if record.high < record.open:
        anomalies.append(OhlcAnomalyType.HIGH_BELOW_OPEN)
    if record.high < record.close:
        anomalies.append(OhlcAnomalyType.HIGH_BELOW_CLOSE)
    if record.low > record.open:
        anomalies.append(OhlcAnomalyType.LOW_ABOVE_OPEN)
    if record.low > record.close:
        anomalies.append(OhlcAnomalyType.LOW_ABOVE_CLOSE)
    return tuple(anomalies)


def classify_source_bar(record: MarketRecord) -> SourceBarAssessment:
    anomalies = ohlc_anomalies(record)
    if not anomalies:
        status = SourceQualityStatus.VALID
    else:
        local_time = record_timestamp(record).time().replace(tzinfo=None)
        opening_only = set(anomalies) <= {
            OhlcAnomalyType.HIGH_BELOW_OPEN,
            OhlcAnomalyType.LOW_ABOVE_OPEN,
        }
        status = (
            SourceQualityStatus.SOURCE_BOUNDARY_QUIRK
            if local_time == time(9, 30) and opening_only
            else SourceQualityStatus.INVALID
        )
    return SourceBarAssessment(source_bar_id(record), status, anomalies)


def validate_source_minute_records(
    records: Iterable[MarketRecord],
    *,
    allowed_negative_fields: frozenset[str] = frozenset(),
) -> tuple[SourceBarAssessment, ...]:
    """Validate minute Source Bars without changing Raw values.

    The default remains strict. A caller may explicitly admit a known
    non-core negative field so the field-quality layer can BLOCK that field
    while retaining trusted Close. Only ``volume`` and ``turnover`` are
    eligible for this narrow degradation path.
    """
    if not allowed_negative_fields <= {"volume", "turnover"}:
        raise TrendMonitorError(
            ErrorCategory.INVALID_DATA,
            "invalid allowed_negative_fields",
        )
    materialized = list(records)
    if not materialized:
        raise TrendMonitorError(ErrorCategory.EMPTY_DATA, "Source Bar array is empty")
    timestamps: list[int] = []
    assessments: list[SourceBarAssessment] = []
    for record in materialized:
        missing = [
            field
            for field in (
                "symbol", "instrument_id", "timestamp", "open", "high", "low",
                "close", "volume", "turnover", "trade_session",
            )
            if getattr(record, field) is None or getattr(record, field) == ""
        ]
        if missing:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"missing required Source Bar fields: {', '.join(missing)}",
            )
        assert record.timestamp is not None
        assert record.volume is not None and record.turnover is not None
        invalid_timestamp = record.timestamp <= 0
        invalid_volume = record.volume < 0 and "volume" not in allowed_negative_fields
        invalid_turnover = record.turnover < 0 and "turnover" not in allowed_negative_fields
        if invalid_timestamp or invalid_volume or invalid_turnover:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"invalid timestamp/volume/turnover in {source_bar_id(record)}",
            )
        local_time = record_timestamp(record).time().replace(tzinfo=None)
        in_session = (
            time(9, 30) <= local_time <= time(11, 30)
            or time(13, 0) <= local_time <= time(15, 0)
        )
        if not in_session:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"Source Bar outside A-share session: {source_bar_id(record)}",
            )
        assessment = classify_source_bar(record)
        if assessment.quality_status is SourceQualityStatus.INVALID:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"strict OHLC anomaly in {assessment.source_bar_id}: "
                f"{','.join(item.value for item in assessment.anomaly_types)}",
            )
        timestamps.append(record.timestamp)
        assessments.append(assessment)
    if timestamps != sorted(timestamps):
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "Source Bar timestamps are not increasing")
    if len(timestamps) != len(set(timestamps)):
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "duplicate Source Bar timestamp")
    return tuple(assessments)
