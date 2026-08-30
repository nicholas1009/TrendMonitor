"""Deterministic Close-only Market→Industry→Stock context engine."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import AnalysisPeriod, PreflightStatus, RiskInput, StockIndustryContextResult
from trend_monitor.stock_risk.engine import percentile

from .rules import IndustryBenchmark, StockIndustryContextRules


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class IndustryReferenceObservation:
    instrument_id: str
    trading_date: str
    period_end: str
    industry_close: float
    stock_return: float
    industry_return: float
    source_industry_risk_input_id: str

    @property
    def stock_industry_relative_return(self) -> float:
        return self.stock_return - self.industry_return


def _iso_epoch(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI).isoformat()


class StockIndustryContextEngine:
    def __init__(self, rules: StockIndustryContextRules) -> None:
        rules.validate()
        self.rules = rules

    def evaluate(
        self,
        *,
        instrument_id: str,
        stock_60m_result: Mapping[str, Any],
        industry_risk_input: RiskInput | None,
        history: Sequence[IndustryReferenceObservation],
        source_stock_60m_result_id: str,
        source_market_60m_result_id: str | None,
        source_industry_risk_input_id: str | None,
        source_benchmark_evidence_id: str,
    ) -> StockIndustryContextResult:
        benchmark = self.rules.benchmark(instrument_id)
        self._validate_stock_result(instrument_id, stock_60m_result)
        if benchmark.minute_60m_capability != "DIRECT":
            return self._unavailable(
                benchmark,
                stock_60m_result,
                reason=benchmark.unavailable_reason or "NO_DIRECT_MINUTE_BENCHMARK",
                source_stock_60m_result_id=source_stock_60m_result_id,
                source_market_60m_result_id=source_market_60m_result_id,
                source_industry_risk_input_id=source_industry_risk_input_id,
                source_benchmark_evidence_id=source_benchmark_evidence_id,
            )
        error = self._input_error(industry_risk_input, benchmark, source_industry_risk_input_id)
        if error:
            return self._unavailable(
                benchmark,
                stock_60m_result,
                reason=error,
                source_stock_60m_result_id=source_stock_60m_result_id,
                source_market_60m_result_id=source_market_60m_result_id,
                source_industry_risk_input_id=source_industry_risk_input_id,
                source_benchmark_evidence_id=source_benchmark_evidence_id,
            )
        assert industry_risk_input is not None
        target_end = industry_risk_input.system_bars[-1].end
        period_end = _iso_epoch(target_end)
        if stock_60m_result.get("period_end") != period_end:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock and industry periods are misaligned")
        ordered = sorted(history, key=lambda item: item.period_end)
        if any(item.instrument_id != instrument_id for item in ordered):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "industry reference instrument mismatch")
        if any(datetime.fromisoformat(item.period_end).timestamp() * 1000 >= target_end for item in ordered):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "industry reference contains current/future period")
        if len(ordered) < 2:
            return self._unavailable(
                benchmark,
                stock_60m_result,
                reason="INSUFFICIENT_INDUSTRY_CLOSE_HISTORY",
                source_stock_60m_result_id=source_stock_60m_result_id,
                source_market_60m_result_id=source_market_60m_result_id,
                source_industry_risk_input_id=source_industry_risk_input_id,
                source_benchmark_evidence_id=source_benchmark_evidence_id,
            )
        current_close = Decimal(str(industry_risk_input.system_bars[-1].close))
        previous_close = Decimal(str(ordered[-1].industry_close))
        two_ago_close = Decimal(str(ordered[-2].industry_close))
        if min(current_close, previous_close, two_ago_close) <= 0:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "industry Close must be positive")
        industry_return = current_close / previous_close - Decimal(1)
        previous_industry_return = previous_close / two_ago_close - Decimal(1)
        stock_return = Decimal(str(stock_60m_result["current_return"]))
        relative = stock_return - industry_return
        market_context = stock_60m_result.get("market_context") or {}
        market_return_raw = market_context.get("market_median_return")
        market_return = Decimal(str(market_return_raw)) if market_return_raw is not None else None
        market_light = str(market_context.get("market_risk_light") or "")
        broad = bool(market_context.get("broad_selloff_resonance"))

        complete_days = self._complete_days(ordered, industry_risk_input.trading_date)
        minimum_days = int(self.rules.raw["relative_weakness"]["minimum_complete_trading_days"])
        selected_days = complete_days[-minimum_days:]
        relative_p10 = None
        weak_vs_industry = False
        relative_status = "RELATIVE_REFERENCE_UNAVAILABLE"
        if len(selected_days) == minimum_days:
            allowed = set(selected_days)
            values = [
                Decimal(str(item.stock_industry_relative_return))
                for item in ordered
                if item.trading_date in allowed
            ]
            relative_p10 = percentile(
                values, Decimal(str(self.rules.raw["relative_weakness"]["percentile"]))
            )
            weak_vs_industry = relative < 0 and relative <= relative_p10
            relative_status = "AVAILABLE"

        persistent = industry_return < 0 and previous_industry_return < 0
        weak_resonance = stock_return < 0 and industry_return < 0
        triple = weak_resonance and (market_light in {"ORANGE", "RED"} or broad)
        stock_strong = stock_return > 0 and industry_return < 0
        industry_strength = industry_return > 0 and market_light in {"ORANGE", "RED"}
        context = self._context_classification(stock_return, industry_return, market_light)
        market_independent = bool(stock_60m_result.get("relative_weakness"))
        decomposition = (
            "INDUSTRY_AND_MARKET_INDEPENDENT"
            if market_independent and weak_vs_industry
            else "MARKET_INDEPENDENT_ONLY"
            if market_independent
            else "NONE"
        )
        degradation = []
        if relative_status != "AVAILABLE":
            degradation.append(
                self._degradation(
                    "stock_weak_vs_industry",
                    relative_status,
                    "historical_stock_industry_relative_p10",
                    source_industry_risk_input_id,
                )
            )
        if benchmark.minute_15m_capability != "DIRECT":
            degradation.append(
                self._degradation(
                    "industry_15m_internal",
                    "NO_DIRECT_MINUTE_BENCHMARK",
                    "industry_15m_internal",
                    source_benchmark_evidence_id,
                )
            )
        source_ids = self._source_ids(
            source_stock_60m_result_id,
            source_market_60m_result_id,
            source_industry_risk_input_id,
            source_benchmark_evidence_id,
        )
        return StockIndustryContextResult(
            rules_version=self.rules.rules_version,
            instrument_id=instrument_id,
            industry_id=benchmark.industry_id,
            industry_name=benchmark.industry_name,
            industry_provider=benchmark.provider,
            industry_provider_symbol=benchmark.provider_symbol,
            industry_mapping_type=benchmark.mapping_type,
            industry_confidence=benchmark.confidence,
            period_end=period_end,
            stock_risk_score=int(stock_60m_result["risk_score"]),
            stock_risk_light=str(stock_60m_result["risk_light"]),
            stock_return=float(stock_return),
            industry_return=float(industry_return),
            market_return=float(market_return) if market_return is not None else None,
            stock_industry_relative_return=float(relative),
            historical_stock_industry_relative_p10=float(relative_p10) if relative_p10 is not None else None,
            relative_reference_status=relative_status,
            industry_persistent_weakness=persistent,
            stock_industry_weak_resonance=weak_resonance,
            triple_weak_resonance=triple,
            stock_weak_vs_industry=weak_vs_industry,
            stock_strong_against_industry=stock_strong,
            industry_relative_strength=industry_strength,
            context_classification=context,
            independent_weakness_decomposition=decomposition,
            industry_15m_internal=None,
            joint_15m_flags=(),
            data_quality={
                "preflight": industry_risk_input.preflight_status.value,
                "close_quality": industry_risk_input.system_bars[-1].field_quality.get("close"),
                "used_fields": ["close"],
                "ignored_for_flags": list(self.rules.raw["ignored_for_flags"]),
                "complete_relative_reference_days": len(selected_days),
                "feature_degradation": degradation,
                "lookahead_safe": True,
                "period_alignment": "PASS",
            },
            source_ids=source_ids,
            status="READY",
            unavailable_reason=None,
        )

    @staticmethod
    def _complete_days(
        history: Sequence[IndustryReferenceObservation], current_day: str | None
    ) -> list[str]:
        grouped: dict[str, list[IndustryReferenceObservation]] = defaultdict(list)
        for item in history:
            if current_day is None or item.trading_date < current_day:
                grouped[item.trading_date].append(item)
        return sorted(day for day, values in grouped.items() if len(values) == 4)

    def _validate_stock_result(self, instrument_id: str, value: Mapping[str, Any]) -> None:
        if value.get("rules_version") != self.rules.raw["source_stock_60m_rules_version"]:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "frozen Stock 60m result linkage changed")
        if value.get("instrument_id") != instrument_id:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock result instrument mismatch")
        if value.get("risk_score") is None or value.get("current_return") is None:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "stock result core fields are missing")

    @staticmethod
    def _input_error(
        value: RiskInput | None, benchmark: IndustryBenchmark, source_id: str | None
    ) -> str | None:
        if value is None or not source_id:
            return "INDUSTRY_RISK_INPUT_MISSING"
        if value.instrument_id != benchmark.industry_id:
            return "INDUSTRY_MAPPING_MISMATCH"
        if value.analysis_period is not AnalysisPeriod.MIN_60:
            return "NOT_60M_INDUSTRY_RISK_INPUT"
        if value.preflight_status is PreflightStatus.BLOCKED:
            return "PREFLIGHT_BLOCKED"
        if not value.system_bars or not value.source_trace.raw_path:
            return "SYSTEM_BAR_OR_SOURCE_TRACE_MISSING"
        bar = value.system_bars[-1]
        if bar.completion_status != "COMPLETED":
            return "CURRENT_PERIOD_INCOMPLETE"
        if bar.field_quality.get("close") not in {"TRUSTED", "TRUSTED_WITH_TRANSFORMATION"}:
            return "CLOSE_NOT_TRUSTED"
        if not bar.source_bar_ids or not bar.source_raw_paths:
            return "LINEAGE_MISSING"
        return None

    @staticmethod
    def _context_classification(
        stock_return: Decimal, industry_return: Decimal, market_light: str
    ) -> str:
        market_weak = market_light in {"ORANGE", "RED"}
        market_stable = market_light in {"GREEN", "YELLOW"}
        if market_weak and industry_return < 0 and stock_return < 0:
            return "TRIPLE_WEAKNESS"
        if market_weak and industry_return > 0 and stock_return < 0:
            return "STOCK_WEAK_IN_STRONG_INDUSTRY_WEAK_MARKET"
        if market_weak and industry_return < 0 and stock_return > 0:
            return "STOCK_RESILIENT_IN_WEAK_INDUSTRY_AND_MARKET"
        if market_stable and industry_return < 0 and stock_return < 0:
            return "INDUSTRY_LED_WEAKNESS"
        if market_stable and industry_return >= 0 and stock_return < 0:
            return "STOCK_SPECIFIC_WEAKNESS"
        return "MIXED_CONTEXT"

    def _unavailable(
        self,
        benchmark: IndustryBenchmark,
        stock: Mapping[str, Any],
        *,
        reason: str,
        source_stock_60m_result_id: str,
        source_market_60m_result_id: str | None,
        source_industry_risk_input_id: str | None,
        source_benchmark_evidence_id: str,
    ) -> StockIndustryContextResult:
        market = stock.get("market_context") or {}
        source_ids = self._source_ids(
            source_stock_60m_result_id,
            source_market_60m_result_id,
            source_industry_risk_input_id,
            source_benchmark_evidence_id,
        )
        return StockIndustryContextResult(
            rules_version=self.rules.rules_version,
            instrument_id=benchmark.instrument_id,
            industry_id=benchmark.industry_id,
            industry_name=benchmark.industry_name,
            industry_provider=benchmark.provider,
            industry_provider_symbol=benchmark.provider_symbol,
            industry_mapping_type=benchmark.mapping_type,
            industry_confidence=benchmark.confidence,
            period_end=stock.get("period_end"),
            stock_risk_score=int(stock["risk_score"]),
            stock_risk_light=str(stock.get("risk_light")) if stock.get("risk_light") else None,
            stock_return=float(stock["current_return"]),
            industry_return=None,
            market_return=(
                float(market["market_median_return"])
                if market.get("market_median_return") is not None
                else None
            ),
            stock_industry_relative_return=None,
            historical_stock_industry_relative_p10=None,
            relative_reference_status="DISABLED",
            industry_persistent_weakness=None,
            stock_industry_weak_resonance=None,
            triple_weak_resonance=None,
            stock_weak_vs_industry=None,
            stock_strong_against_industry=None,
            industry_relative_strength=None,
            context_classification="UNAVAILABLE",
            independent_weakness_decomposition="UNAVAILABLE",
            industry_15m_internal=None,
            joint_15m_flags=(),
            data_quality={
                "mapping_status": "VERIFIED" if benchmark.constituent_verified else "UNVERIFIED",
                "quote_capability": benchmark.quote_capability,
                "daily_capability": benchmark.daily_capability,
                "minute_15m_capability": benchmark.minute_15m_capability,
                "minute_60m_capability": benchmark.minute_60m_capability,
                "used_fields": [],
                "feature_degradation": [
                    self._degradation(
                        "industry_context",
                        reason,
                        "industry_return_and_context_flags",
                        source_benchmark_evidence_id,
                    )
                ],
                "lookahead_safe": True,
                "period_alignment": "NOT_APPLICABLE",
            },
            source_ids=source_ids,
            status="UNAVAILABLE",
            unavailable_reason=reason,
        )

    @staticmethod
    def _source_ids(
        stock: str, market: str | None, industry: str | None, evidence: str
    ) -> dict[str, str | None]:
        return {
            "stock_60m_risk_result": stock,
            "market_60m_risk_result": market,
            "industry_risk_input": industry,
            "benchmark_mapping_evidence": evidence,
        }

    @staticmethod
    def _degradation(feature: str, reason: str, affected: str, source: str | None) -> dict[str, Any]:
        return {
            "feature": feature,
            "status": "DISABLED",
            "reason": reason,
            "affected_field": affected,
            "source": source,
            "lineage": [],
        }
