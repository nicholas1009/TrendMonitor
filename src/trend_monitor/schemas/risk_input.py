"""Stable JSON-safe schemas at the risk-engine preflight boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from trend_monitor.schemas.market import AssetType


RISK_INPUT_SCHEMA_VERSION = 1


class AnalysisPeriod(StrEnum):
    DAILY = "DAILY"
    MIN_60 = "60M"
    MIN_15 = "15M"


class FeatureEligibility(StrEnum):
    ENABLED = "ENABLED"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class PreflightStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_DEGRADATION = "PASS_WITH_DEGRADATION"
    BLOCKED = "BLOCKED"


class RiskInputDataStatus(StrEnum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class RiskSourceTrace:
    requested_provider: str
    actual_provider: str
    provider_symbol: str | None
    fallback_used: bool
    fallback_reason: str | None
    raw_path: str | None
    fetched_at: str | None
    source_timestamp: int | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RiskBar:
    instrument_id: str
    period: str
    start: int
    end: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float
    source_provider: str
    provider_symbol: str
    source_bar_ids: tuple[str, ...]
    source_raw_paths: tuple[str, ...]
    fetched_at: str
    source_timestamp: int | None
    transformation: str
    quality_status: str
    field_quality: dict[str, str]
    completion_status: str = "COMPLETED"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_bar_ids"] = list(self.source_bar_ids)
        result["source_raw_paths"] = list(self.source_raw_paths)
        return result


@dataclass(frozen=True, slots=True)
class FeatureLineage:
    period: str
    source_provider: str
    provider_symbol: str
    source_bar_ids: tuple[str, ...]
    source_raw_paths: tuple[str, ...]
    transformation: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_bar_ids"] = list(self.source_bar_ids)
        result["source_raw_paths"] = list(self.source_raw_paths)
        return result


@dataclass(frozen=True, slots=True)
class FeatureInput:
    feature_name: str
    value: Any
    field_source: tuple[str, ...]
    quality: dict[str, str]
    eligibility: FeatureEligibility
    reason: str
    lineage: tuple[FeatureLineage, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_name": self.feature_name,
            "value": self.value,
            "field_source": list(self.field_source),
            "quality": dict(self.quality),
            "eligibility": self.eligibility.value,
            "reason": self.reason,
            "lineage": [item.to_dict() for item in self.lineage],
        }


@dataclass(frozen=True, slots=True)
class RiskInput:
    instrument_id: str
    asset_type: AssetType
    analysis_period: AnalysisPeriod
    as_of: str
    trading_date: str | None
    source_provider: str | None
    source_trace: RiskSourceTrace
    system_bars: tuple[RiskBar, ...]
    feature_inputs: tuple[FeatureInput, ...]
    disabled_features: tuple[FeatureInput, ...]
    degraded_features: tuple[FeatureInput, ...]
    data_status: RiskInputDataStatus
    preflight_status: PreflightStatus
    last_completed_bar_end: str | None
    data_fetched_at: str | None
    layer_role: str
    in_progress_source_bars: tuple[int, ...] = ()
    preflight_reasons: tuple[str, ...] = ()
    schema_version: int = RISK_INPUT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "asset_type": self.asset_type.value,
            "analysis_period": self.analysis_period.value,
            "as_of": self.as_of,
            "trading_date": self.trading_date,
            "source_provider": self.source_provider,
            "source_trace": self.source_trace.to_dict(),
            "system_bars": [item.to_dict() for item in self.system_bars],
            "feature_inputs": [item.to_dict() for item in self.feature_inputs],
            "disabled_features": [item.to_dict() for item in self.disabled_features],
            "degraded_features": [item.to_dict() for item in self.degraded_features],
            "data_status": self.data_status.value,
            "preflight_status": self.preflight_status.value,
            "last_completed_bar_end": self.last_completed_bar_end,
            "data_fetched_at": self.data_fetched_at,
            "layer_role": self.layer_role,
            "in_progress_source_bars": list(self.in_progress_source_bars),
            "preflight_reasons": list(self.preflight_reasons),
        }


@dataclass(frozen=True, slots=True)
class InstrumentRiskInputBundle:
    instrument_id: str
    asset_type: AssetType
    as_of: str
    daily: RiskInput
    risk_60m: RiskInput
    support_15m: RiskInput
    data_status: RiskInputDataStatus
    preflight_status: PreflightStatus
    reasons: tuple[str, ...]
    schema_version: int = RISK_INPUT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "asset_type": self.asset_type.value,
            "as_of": self.as_of,
            "daily": self.daily.to_dict(),
            "risk_60m": self.risk_60m.to_dict(),
            "support_15m": self.support_15m.to_dict(),
            "data_status": self.data_status.value,
            "preflight_status": self.preflight_status.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class GroupEntry:
    instrument_id: str
    status: str
    reason: str | None
    snapshot_path: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RiskInputGroup:
    group_name: str
    as_of: str
    entries: tuple[GroupEntry, ...]
    schema_version: int = RISK_INPUT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "group_name": self.group_name,
            "as_of": self.as_of,
            "entries": [item.to_dict() for item in self.entries],
        }
