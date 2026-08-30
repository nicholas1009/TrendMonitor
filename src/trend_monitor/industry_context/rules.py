"""Load and freeze TASK_011 auxiliary industry-context rules."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from trend_monitor.errors import ErrorCategory, TrendMonitorError


@dataclass(frozen=True, slots=True)
class IndustryBenchmark:
    instrument_id: str
    stock_symbol: str
    stock_name: str
    industry_id: str
    industry_name: str
    taxonomy: str
    provider: str
    provider_symbol: str
    mapping_type: str
    confidence: str
    constituent_verified: bool
    quote_capability: str
    daily_capability: str
    minute_15m_capability: str
    minute_60m_capability: str
    unavailable_reason: str | None
    longbridge_mapping_type: str
    longbridge_confidence: str


@dataclass(frozen=True, slots=True)
class StockIndustryContextRules:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "StockIndustryContextRules":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            cls._validate(raw)
            return cls(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA, f"invalid stock industry context rules: {path}"
            ) from exc

    @staticmethod
    def _validate(raw: dict[str, Any]) -> None:
        if raw["rules_version"] != "stock_industry_context_v0.1":
            raise ValueError("unexpected industry context rules version")
        frozen = {
            "source_stock_60m_rules_version": "stock_60m_risk_v0.1",
            "source_stock_15m_rules_version": "stock_15m_internal_v0.1",
            "source_market_60m_rules_version": "market_60m_risk_v0.1",
            "source_market_15m_rules_version": "market_15m_internal_v0.1",
        }
        for key, expected in frozen.items():
            if raw[key] != expected:
                raise ValueError(f"frozen linkage changed: {key}")
        if raw["scoring_effect"] != "NONE" or raw["formal_fields"] != ["close"]:
            raise ValueError("industry context must be Close-only and non-scoring")
        if set(raw["ignored_for_flags"]) != {"open", "high", "low", "volume", "turnover"}:
            raise ValueError("unsafe industry flag fields changed")
        if int(raw["relative_weakness"]["minimum_complete_trading_days"]) < 60:
            raise ValueError("industry-relative baseline must contain at least 60 complete days")
        if set(raw["benchmarks"]) != {"stock.hengtong_optic", "stock.wus_printed_circuit"}:
            raise ValueError("TASK_011 must contain exactly the two formal stocks")
        for item in raw["benchmarks"].values():
            if item["mapping_type"] not in {"EXACT", "PROXY", "CANDIDATE_PROXY", "UNMAPPED"}:
                raise ValueError("invalid mapping type")
            if item["confidence"] not in {"HIGH", "MEDIUM", "LOW"}:
                raise ValueError("invalid mapping confidence")

    @property
    def rules_version(self) -> str:
        return str(self.raw["rules_version"])

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(self.raw["benchmarks"])

    def benchmark(self, instrument_id: str) -> IndustryBenchmark:
        try:
            item = self.raw["benchmarks"][instrument_id]
        except KeyError as exc:
            raise TrendMonitorError(
                ErrorCategory.UNMAPPED, f"stock is outside TASK_011: {instrument_id}"
            ) from exc
        return IndustryBenchmark(instrument_id=instrument_id, **item)

    def validate(self) -> None:
        self._validate(self.raw)
