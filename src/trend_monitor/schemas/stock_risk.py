"""Stable schemas for two-stock intraday risk monitoring v0.1."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .market_internal import InternalClassification, InternalPeriodStatus
from .market_risk import CloseRepairState, RiskChangeDirection, RiskLight, SignalConfidence


STOCK_60M_RISK_SCHEMA_VERSION = 1
STOCK_15M_INTERNAL_SCHEMA_VERSION = 1
STOCK_MONITOR_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Stock60mRiskResult:
    rules_version: str
    instrument_id: str
    symbol: str
    name: str
    trading_date: str | None
    period_end: str | None
    risk_score: int | None
    risk_light: RiskLight | None
    risk_light_symbol: str | None
    risk_direction: RiskChangeDirection
    confidence: SignalConfidence
    current_close: float | None
    previous_close: float | None
    current_return: float | None
    previous_return: float | None
    two_period_return: float | None
    consecutive_close_direction: str
    persistent_weakness: bool
    downside_shock: bool
    shock_feature_status: str
    historical_abs_return_p95: float | None
    market_median_return: float | None
    relative_return: float | None
    relative_weakness: bool
    relative_weakness_status: str
    historical_relative_return_p10: float | None
    market_resonance: bool
    repair_state: CloseRepairState
    market_relationship: str
    divergence_flags: tuple[str, ...]
    market_context: dict[str, Any]
    score_components: dict[str, int]
    data_quality: dict[str, Any]
    source_risk_input_id: str | None
    source_market_60m_result_id: str | None
    status: str = "READY"
    schema_version: int = STOCK_60M_RISK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["risk_light"] = self.risk_light.value if self.risk_light else None
        result["risk_direction"] = self.risk_direction.value
        result["confidence"] = self.confidence.value
        result["repair_state"] = self.repair_state.value
        result["divergence_flags"] = list(self.divergence_flags)
        return result


@dataclass(frozen=True, slots=True)
class Stock15mInternalResult:
    rules_version: str
    instrument_id: str
    symbol: str
    name: str
    period_60m_start: str
    period_60m_end: str
    period_status: InternalPeriodStatus
    completed_15m_count: int
    classification: InternalClassification
    direction_sequence: tuple[str, ...]
    closes: tuple[float, ...]
    close_changes_pct: tuple[float, ...]
    repair_strength: float | None
    finish_position: float | None
    joint_market_flags: tuple[str, ...]
    source_stock_risk_input_id: str | None
    source_market_15m_result_id: str | None
    data_quality: dict[str, Any]
    status: str = "READY"
    schema_version: int = STOCK_15M_INTERNAL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["period_status"] = self.period_status.value
        result["classification"] = self.classification.value
        for key in ("direction_sequence", "closes", "close_changes_pct", "joint_market_flags"):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class StockIntradayMonitorResult:
    instrument_id: str
    symbol: str
    stock_60m_risk: Stock60mRiskResult | None
    stock_15m_internal: Stock15mInternalResult
    market_60m_context: dict[str, Any]
    market_15m_context: dict[str, Any]
    monitoring_only: bool = True
    schema_version: int = STOCK_MONITOR_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "monitoring_only": self.monitoring_only,
            "stock_60m_risk": self.stock_60m_risk.to_dict() if self.stock_60m_risk else None,
            "stock_15m_internal": self.stock_15m_internal.to_dict(),
            "market_60m_context": dict(self.market_60m_context),
            "market_15m_context": dict(self.market_15m_context),
        }
