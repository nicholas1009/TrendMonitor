"""Stable TASK_011 stock/industry auxiliary context schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


STOCK_INDUSTRY_CONTEXT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class StockIndustryContextResult:
    rules_version: str
    instrument_id: str
    industry_id: str
    industry_name: str
    industry_provider: str
    industry_provider_symbol: str
    industry_mapping_type: str
    industry_confidence: str
    period_end: str | None
    stock_risk_score: int | None
    stock_risk_light: str | None
    stock_return: float | None
    industry_return: float | None
    market_return: float | None
    stock_industry_relative_return: float | None
    historical_stock_industry_relative_p10: float | None
    relative_reference_status: str
    industry_persistent_weakness: bool | None
    stock_industry_weak_resonance: bool | None
    triple_weak_resonance: bool | None
    stock_weak_vs_industry: bool | None
    stock_strong_against_industry: bool | None
    industry_relative_strength: bool | None
    context_classification: str
    independent_weakness_decomposition: str
    industry_15m_internal: dict[str, Any] | None
    joint_15m_flags: tuple[str, ...]
    data_quality: dict[str, Any]
    source_ids: dict[str, str | None]
    status: str
    unavailable_reason: str | None
    auxiliary_only: bool = True
    stock_score_immutable: bool = True
    schema_version: int = STOCK_INDUSTRY_CONTEXT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["joint_15m_flags"] = list(self.joint_15m_flags)
        return result
