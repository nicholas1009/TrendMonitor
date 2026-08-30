"""Stable schemas for the Close-only 15m internal structure auxiliary layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


MARKET_15M_INTERNAL_SCHEMA_VERSION = 1


class InternalClassification(StrEnum):
    HEALTHY_UP = "HEALTHY_UP"
    HEALTHY_DOWN = "HEALTHY_DOWN"
    LATE_REPAIR = "LATE_REPAIR"
    FAILED_REPAIR = "FAILED_REPAIR"
    LATE_WEAKENING = "LATE_WEAKENING"
    MIXED = "MIXED"
    EARLY_STRENGTH = "EARLY_STRENGTH"
    EARLY_WEAKNESS = "EARLY_WEAKNESS"
    EARLY_MIXED = "EARLY_MIXED"
    UNAVAILABLE = "UNAVAILABLE"


class MarketInternalState(StrEnum):
    REPAIR_BROADENING = "REPAIR_BROADENING"
    WEAKNESS_BROADENING = "WEAKNESS_BROADENING"
    INTERNAL_MIXED = "INTERNAL_MIXED"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class InternalPeriodStatus(StrEnum):
    COMPLETED = "COMPLETED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class Index15mInternalState:
    instrument_id: str
    name: str
    classification: InternalClassification
    completed_15m_count: int
    direction_sequence: tuple[str, ...]
    closes: tuple[float, ...]
    close_changes_pct: tuple[float, ...]
    repair_strength: float | None
    finish_position: float | None
    close_quality: tuple[str, ...]
    source_risk_input_id: str | None
    source_bar_ids: tuple[str, ...]
    source_raw_paths: tuple[str, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["classification"] = self.classification.value
        for key in (
            "direction_sequence",
            "closes",
            "close_changes_pct",
            "close_quality",
            "source_bar_ids",
            "source_raw_paths",
        ):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class Group15mInternalState:
    group: str
    state: str
    valid_count: int
    classification_counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Market15mInternalResult:
    trading_date: str
    period_60m_start: str
    period_60m_end: str
    period_status: InternalPeriodStatus
    completed_15m_count: int
    market_internal_state: MarketInternalState
    classification_counts: dict[str, int]
    index_internal_states: tuple[Index15mInternalState, ...]
    group_states: tuple[Group15mInternalState, ...]
    source_risk_input_ids: tuple[str, ...]
    source_60m_risk_result_id: str
    linked_60m_risk: dict[str, Any]
    data_quality: dict[str, Any]
    rules_version: str
    status: str = "READY"
    schema_version: int = MARKET_15M_INTERNAL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "rules_version": self.rules_version,
            "status": self.status,
            "trading_date": self.trading_date,
            "60m_period_start": self.period_60m_start,
            "60m_period_end": self.period_60m_end,
            "period_status": self.period_status.value,
            "completed_15m_count": self.completed_15m_count,
            "market_internal_state": self.market_internal_state.value,
            "classification_counts": dict(self.classification_counts),
            "index_internal_states": [item.to_dict() for item in self.index_internal_states],
            "group_states": [item.to_dict() for item in self.group_states],
            "source_risk_input_ids": list(self.source_risk_input_ids),
            "source_60m_risk_result_id": self.source_60m_risk_result_id,
            "linked_60m_risk": dict(self.linked_60m_risk),
            "data_quality": dict(self.data_quality),
        }
