"""Minimal, explainable daily OHLC cross-provider comparison."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import MarketRecord


SHANGHAI = ZoneInfo("Asia/Shanghai")
PRICE_FIELDS = ("open", "high", "low", "close")


class ComparisonStatus(StrEnum):
    MATCH = "MATCH"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PRICE_CONFLICT = "PRICE_CONFLICT"
    ADJUSTMENT_MISMATCH = "ADJUSTMENT_MISMATCH"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class VolumeComparison(StrEnum):
    COMPARABLE = "COMPARABLE"
    UNIT_UNKNOWN = "UNIT_UNKNOWN"


@dataclass(frozen=True, slots=True)
class ComparisonConfig:
    minimum_common_days: int
    price_relative_tolerance: float | None
    mode: str
    rationale: str


@dataclass(frozen=True, slots=True)
class PriceDifference:
    trade_date: str
    field: str
    left: float
    right: float
    absolute_difference: float
    relative_difference: float


@dataclass(frozen=True, slots=True)
class DailyComparisonReport:
    instrument_id: str
    left_provider: str
    right_provider: str
    common_days: int
    date_start: str | None
    date_end: str | None
    adjustment: str | None
    status: ComparisonStatus
    volume_comparison: VolumeComparison
    maximum_relative_difference: dict[str, float]
    mean_relative_difference: dict[str, float]
    anomalies: tuple[PriceDifference, ...]
    notes: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["status"] = self.status.value
        result["volume_comparison"] = self.volume_comparison.value
        return result


def load_comparison_config(path: str | Path) -> ComparisonConfig:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        threshold = payload.get("price_relative_tolerance")
        return ComparisonConfig(
            minimum_common_days=int(payload["minimum_common_days"]),
            price_relative_tolerance=float(threshold) if threshold is not None else None,
            mode=str(payload["mode"]),
            rationale=str(payload["rationale"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TrendMonitorError(
            ErrorCategory.INVALID_DATA,
            f"Invalid comparison config: {path}",
        ) from exc


def _trade_date(record: MarketRecord) -> str:
    if record.timestamp is None:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "daily timestamp is missing")
    return (
        datetime.fromtimestamp(record.timestamp / 1000, tz=timezone.utc)
        .astimezone(SHANGHAI)
        .date()
        .isoformat()
    )


def _by_date(records: list[MarketRecord]) -> dict[str, MarketRecord]:
    result: dict[str, MarketRecord] = {}
    for record in records:
        trade_date = _trade_date(record)
        if trade_date in result:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"duplicate daily record: {trade_date}",
            )
        result[trade_date] = record
    return result


def compare_daily_records(
    left: list[MarketRecord],
    right: list[MarketRecord],
    *,
    instrument_id: str,
    left_provider: str,
    right_provider: str,
    left_adjustment: str,
    right_adjustment: str,
    config: ComparisonConfig,
    left_volume_unit: str | None = None,
    right_volume_unit: str | None = None,
) -> DailyComparisonReport:
    volume_status = (
        VolumeComparison.COMPARABLE
        if left_volume_unit is not None and left_volume_unit == right_volume_unit
        else VolumeComparison.UNIT_UNKNOWN
    )
    if left_adjustment != right_adjustment:
        return DailyComparisonReport(
            instrument_id=instrument_id,
            left_provider=left_provider,
            right_provider=right_provider,
            common_days=0,
            date_start=None,
            date_end=None,
            adjustment=None,
            status=ComparisonStatus.ADJUSTMENT_MISMATCH,
            volume_comparison=volume_status,
            maximum_relative_difference={},
            mean_relative_difference={},
            anomalies=(),
            notes=f"adjustment mismatch: {left_adjustment} vs {right_adjustment}",
        )

    left_dates = _by_date(left)
    right_dates = _by_date(right)
    common_dates = sorted(set(left_dates) & set(right_dates))
    if len(common_dates) < config.minimum_common_days:
        return DailyComparisonReport(
            instrument_id=instrument_id,
            left_provider=left_provider,
            right_provider=right_provider,
            common_days=len(common_dates),
            date_start=common_dates[0] if common_dates else None,
            date_end=common_dates[-1] if common_dates else None,
            adjustment=left_adjustment,
            status=ComparisonStatus.DATA_INCOMPLETE,
            volume_comparison=volume_status,
            maximum_relative_difference={},
            mean_relative_difference={},
            anomalies=(),
            notes=f"requires at least {config.minimum_common_days} common days",
        )

    differences: list[PriceDifference] = []
    by_field: dict[str, list[float]] = {field: [] for field in PRICE_FIELDS}
    for trade_date in common_dates:
        for field in PRICE_FIELDS:
            left_value = getattr(left_dates[trade_date], field)
            right_value = getattr(right_dates[trade_date], field)
            if left_value is None or right_value is None:
                raise TrendMonitorError(
                    ErrorCategory.DATA_INCOMPLETE,
                    f"missing {field} on {trade_date}",
                )
            absolute = abs(left_value - right_value)
            relative = absolute / max(abs(left_value), abs(right_value), 1e-12)
            by_field[field].append(relative)
            if relative > 0:
                differences.append(
                    PriceDifference(
                        trade_date=trade_date,
                        field=field,
                        left=left_value,
                        right=right_value,
                        absolute_difference=absolute,
                        relative_difference=relative,
                    )
                )

    maximum = {field: max(values, default=0.0) for field, values in by_field.items()}
    averages = {field: mean(values) if values else 0.0 for field, values in by_field.items()}
    maximum_overall = max(maximum.values(), default=0.0)
    if maximum_overall == 0:
        status = ComparisonStatus.MATCH
        anomalies: tuple[PriceDifference, ...] = ()
    elif config.price_relative_tolerance is None:
        status = ComparisonStatus.REVIEW_REQUIRED
        anomalies = tuple(sorted(differences, key=lambda item: item.relative_difference, reverse=True)[:20])
    elif maximum_overall > config.price_relative_tolerance:
        status = ComparisonStatus.PRICE_CONFLICT
        anomalies = tuple(
            item
            for item in differences
            if item.relative_difference > config.price_relative_tolerance
        )
    else:
        status = ComparisonStatus.MATCH
        anomalies = ()

    return DailyComparisonReport(
        instrument_id=instrument_id,
        left_provider=left_provider,
        right_provider=right_provider,
        common_days=len(common_dates),
        date_start=common_dates[0],
        date_end=common_dates[-1],
        adjustment=left_adjustment,
        status=status,
        volume_comparison=volume_status,
        maximum_relative_difference=maximum,
        mean_relative_difference=averages,
        anomalies=anomalies,
        notes=config.rationale if status is ComparisonStatus.REVIEW_REQUIRED else "",
    )
