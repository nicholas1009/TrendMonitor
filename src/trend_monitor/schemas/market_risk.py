"""Stable machine schema for Market 60m Risk Engine results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


MARKET_RISK_SCHEMA_VERSION = 1


class RiskLight(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


class SignalConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskChangeDirection(StrEnum):
    RISING = "RISING"
    FLAT = "FLAT"
    FALLING = "FALLING"
    NOT_AVAILABLE = "N/A"


class CloseRepairState(StrEnum):
    NONE = "NONE"
    REPAIR_ATTEMPT = "REPAIR_ATTEMPT"
    FULL_CLOSE_REPAIR = "FULL_CLOSE_REPAIR"


@dataclass(frozen=True, slots=True)
class IndexRiskState:
    instrument_id: str
    name: str
    close: float
    close_change_pct: float
    one_period_direction: str
    two_period_direction: str
    three_period_close_direction: str
    persistent_weak: bool
    repair_state: CloseRepairState
    downside_shock: bool
    shock_feature_status: str
    shock_reference_p95: float | None
    recent_close_high: float
    recent_close_low: float
    close_drawdown_from_recent_close_high: float
    quality: str
    source_snapshot_id: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["repair_state"] = self.repair_state.value
        return result


@dataclass(frozen=True, slots=True)
class GroupRiskState:
    group: str
    group_advancers: int
    group_decliners: int
    group_unchanged: int
    median_close_change_pct: float
    group_direction: str
    valid_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Market60mRiskResult:
    trading_date: str | None
    as_of: str
    last_completed_bar_end: str | None
    risk_score: int | None
    risk_light: RiskLight | None
    risk_light_symbol: str | None
    risk_direction: RiskChangeDirection
    signal_confidence: SignalConfidence
    breadth: dict[str, int]
    persistent_weakness: dict[str, int]
    downside_shocks: dict[str, Any]
    weighted_support_distortion: bool
    small_cap_stress: bool
    style_divergence_strong: bool
    broad_selloff_resonance: bool
    strong_broad_weakness: bool
    broad_repair: bool
    repair_count: int
    group_states: tuple[GroupRiskState, ...]
    index_states: tuple[IndexRiskState, ...]
    style_spreads: dict[str, float | str | None]
    score_components: dict[str, int]
    data_quality: dict[str, Any]
    source_snapshot_ids: tuple[str, ...]
    rules_version: str
    status: str = "READY"
    schema_version: int = MARKET_RISK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "rules_version": self.rules_version,
            "status": self.status,
            "trading_date": self.trading_date,
            "as_of": self.as_of,
            "last_completed_bar_end": self.last_completed_bar_end,
            "risk_score": self.risk_score,
            "risk_light": self.risk_light.value if self.risk_light else None,
            "risk_light_symbol": self.risk_light_symbol,
            "risk_direction": self.risk_direction.value,
            "signal_confidence": self.signal_confidence.value,
            "breadth": dict(self.breadth),
            "persistent_weakness": dict(self.persistent_weakness),
            "downside_shocks": dict(self.downside_shocks),
            "weighted_support_distortion": self.weighted_support_distortion,
            "small_cap_stress": self.small_cap_stress,
            "style_divergence_strong": self.style_divergence_strong,
            "broad_selloff_resonance": self.broad_selloff_resonance,
            "strong_broad_weakness": self.strong_broad_weakness,
            "broad_repair": self.broad_repair,
            "repair_count": self.repair_count,
            "group_states": [item.to_dict() for item in self.group_states],
            "index_states": [item.to_dict() for item in self.index_states],
            "style_spreads": dict(self.style_spreads),
            "score_components": dict(self.score_components),
            "data_quality": dict(self.data_quality),
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }
