"""Deterministic Close-only Stock 60m Risk Engine v0.1."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import (
    AnalysisPeriod,
    CloseRepairState,
    FeatureEligibility,
    PreflightStatus,
    RiskChangeDirection,
    RiskInput,
    RiskLight,
    SignalConfidence,
    Stock60mRiskResult,
)
from trend_monitor.stock_risk.rules import StockIntradayRiskRules


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class StockReferenceObservation:
    instrument_id: str
    trading_date: str
    period_end: str
    close: float
    stock_return: float
    market_median_return: float | None
    source_stock_risk_input_id: str
    source_market_60m_result_id: str | None

    @property
    def relative_return(self) -> float | None:
        return None if self.market_median_return is None else self.stock_return - self.market_median_return


def percentile(values: Sequence[Decimal], level: Decimal) -> Decimal:
    if not values:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "percentile input is empty")
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * level
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _iso_epoch(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI).isoformat()


class Stock60mRiskEngine:
    def __init__(self, rules: StockIntradayRiskRules) -> None:
        self.rules = rules

    def evaluate(
        self,
        stock_input: RiskInput | None,
        *,
        history: Sequence[StockReferenceObservation],
        market_60m_result: Mapping[str, Any] | None,
        market_15m_result: Mapping[str, Any] | None,
        source_stock_risk_input_id: str | None,
        source_market_60m_result_id: str | None,
        source_market_15m_result_id: str | None,
        previous_result: Stock60mRiskResult | Mapping[str, Any] | None = None,
    ) -> Stock60mRiskResult:
        instrument_id = stock_input.instrument_id if stock_input else "UNKNOWN"
        if instrument_id not in self.rules.instrument_ids:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, f"stock is outside TASK_010: {instrument_id}")
        symbol, name = self.rules.identity(instrument_id)
        error = self._input_error(stock_input, source_stock_risk_input_id)
        if error:
            return self._blocked(instrument_id, symbol, name, stock_input, error, source_stock_risk_input_id)
        assert stock_input is not None
        current_bar = stock_input.system_bars[-1]
        target_end = current_bar.end
        ordered_history = sorted(history, key=lambda item: item.period_end)
        if any(item.instrument_id != instrument_id for item in ordered_history):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock history instrument mismatch")
        if any(datetime.fromisoformat(item.period_end).timestamp() * 1000 >= target_end for item in ordered_history):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock reference contains current/future period")
        if len(ordered_history) < 2:
            return self._blocked(
                instrument_id, symbol, name, stock_input, "INSUFFICIENT_CLOSE_HISTORY", source_stock_risk_input_id
            )

        current = Decimal(str(current_bar.close))
        previous = Decimal(str(ordered_history[-1].close))
        two_ago = Decimal(str(ordered_history[-2].close))
        if previous <= 0 or two_ago <= 0:
            return self._blocked(instrument_id, symbol, name, stock_input, "INVALID_CLOSE_HISTORY", source_stock_risk_input_id)
        current_return = current / previous - Decimal(1)
        previous_return = previous / two_ago - Decimal(1)
        two_period_return = current / two_ago - Decimal(1)
        persistent = current_return < 0 and previous_return < 0
        consecutive = "DOWN" if persistent else "UP" if current_return > 0 and previous_return > 0 else "MIXED"
        repair = CloseRepairState.NONE
        if previous_return < 0 and current_return > 0:
            repair = CloseRepairState.FULL_CLOSE_REPAIR if current >= two_ago else CloseRepairState.REPAIR_ATTEMPT

        complete_days = self._complete_history_days(ordered_history, stock_input.trading_date)
        min_shock_days = int(self.rules.raw["downside_shock"]["minimum_complete_trading_days"])
        selected_shock_days = complete_days[-min_shock_days:]
        shock_threshold = None
        shock = False
        shock_status = "SHOCK_FEATURE_UNAVAILABLE"
        if len(selected_shock_days) == min_shock_days:
            allowed = set(selected_shock_days)
            values = [abs(Decimal(str(item.stock_return))) for item in ordered_history if item.trading_date in allowed]
            shock_threshold = percentile(values, Decimal(str(self.rules.raw["downside_shock"]["percentile"])))
            shock = current_return < 0 and abs(current_return) >= shock_threshold
            shock_status = "AVAILABLE"

        market_context, market_available = self._market_context(
            market_60m_result, source_market_60m_result_id, target_end
        )
        market_median = market_context.get("market_median_return")
        relative = None if market_median is None else current_return - Decimal(str(market_median))
        min_relative_days = int(self.rules.raw["relative_weakness"]["minimum_complete_trading_days"])
        relative_days = [
            day
            for day in complete_days
            if all(
                item.market_median_return is not None
                for item in ordered_history
                if item.trading_date == day
            )
        ][-min_relative_days:]
        relative_threshold = None
        relative_weak = False
        relative_status = "RELATIVE_REFERENCE_UNAVAILABLE"
        if relative is not None and len(relative_days) == min_relative_days:
            allowed = set(relative_days)
            relative_values = [
                Decimal(str(item.relative_return))
                for item in ordered_history
                if item.trading_date in allowed and item.relative_return is not None
            ]
            relative_threshold = percentile(
                relative_values, Decimal(str(self.rules.raw["relative_weakness"]["percentile"]))
            )
            relative_weak = relative < 0 and relative <= relative_threshold
            relative_status = "AVAILABLE"

        market_light = market_context.get("market_risk_light")
        broad = bool(market_context.get("broad_selloff_resonance"))
        strong = bool(market_context.get("strong_broad_weakness"))
        resonance = current_return < 0 and market_available and (
            market_light in {"ORANGE", "RED"} or broad or strong
        )
        relationship = (
            "STRONGER_THAN_MARKET"
            if relative is not None and relative > 0
            else "WEAKER_THAN_MARKET"
            if relative is not None and relative < 0
            else "IN_LINE_WITH_MARKET"
            if relative is not None
            else "UNAVAILABLE"
        )
        divergence = []
        if current_return < 0 and market_light in {"GREEN", "YELLOW"} and relative_weak:
            divergence.append("STOCK_WEAK_MARKET_STABLE")
        if current_return > 0 and market_light in {"ORANGE", "RED"}:
            divergence.append("STOCK_STRONG_MARKET_WEAK")

        components = {
            "persistent_weakness_points": int(self.rules.raw["persistent_weakness_points"]) if persistent else 0,
            "downside_shock_points": int(self.rules.raw["downside_shock"]["points"]) if shock else 0,
            "relative_weakness_points": int(self.rules.raw["relative_weakness"]["points"]) if relative_weak else 0,
            "market_resonance_points": int(self.rules.raw["market_resonance_points"]) if resonance else 0,
            "full_close_repair_offset": int(self.rules.raw["full_close_repair_offset"])
            if repair is CloseRepairState.FULL_CLOSE_REPAIR
            else 0,
        }
        score = max(0, sum(value for key, value in components.items() if key != "full_close_repair_offset") - components["full_close_repair_offset"])
        light_name, symbol_char = self.rules.light(score)
        direction = self._risk_direction(score, target_end, previous_result)
        market15_available = bool(
            market_15m_result
            and source_market_15m_result_id
            and market_15m_result.get("rules_version") == self.rules.raw["source_market_15m_rules_version"]
            and market_15m_result.get("60m_period_end") == _iso_epoch(target_end)
        )
        if market15_available:
            market_context["market_internal_state"] = market_15m_result.get("market_internal_state")
        confidence = (
            SignalConfidence.HIGH
            if market_available and market15_available and relative_status == "AVAILABLE"
            else SignalConfidence.MEDIUM
        )
        degradation = self._advisory_degradation(stock_input, source_stock_risk_input_id)
        if shock_status != "AVAILABLE":
            degradation.append(self._disabled("downside_shock", shock_status, "historical_abs_return_p95", source_stock_risk_input_id))
        if relative_status != "AVAILABLE":
            degradation.append(self._disabled("relative_weakness", relative_status, "historical_relative_return_p10", source_market_60m_result_id))
        if not market_available:
            degradation.append(self._disabled("market_resonance", "MARKET_60M_UNAVAILABLE", "market_context", source_market_60m_result_id))
        if not market15_available:
            degradation.append(self._disabled("joint_market_15m_flags", "MARKET_15M_UNAVAILABLE", "market_internal_state", source_market_15m_result_id))

        return Stock60mRiskResult(
            rules_version=self.rules.rules_version,
            instrument_id=instrument_id,
            symbol=symbol,
            name=name,
            trading_date=stock_input.trading_date,
            period_end=_iso_epoch(target_end),
            risk_score=score,
            risk_light=RiskLight(light_name),
            risk_light_symbol=symbol_char,
            risk_direction=direction,
            confidence=confidence,
            current_close=float(current),
            previous_close=float(previous),
            current_return=float(current_return),
            previous_return=float(previous_return),
            two_period_return=float(two_period_return),
            consecutive_close_direction=consecutive,
            persistent_weakness=persistent,
            downside_shock=shock,
            shock_feature_status=shock_status,
            historical_abs_return_p95=float(shock_threshold) if shock_threshold is not None else None,
            market_median_return=float(market_median) if market_median is not None else None,
            relative_return=float(relative) if relative is not None else None,
            relative_weakness=relative_weak,
            relative_weakness_status=relative_status,
            historical_relative_return_p10=float(relative_threshold) if relative_threshold is not None else None,
            market_resonance=resonance,
            repair_state=repair,
            market_relationship=relationship,
            divergence_flags=tuple(divergence),
            market_context=market_context,
            score_components=components,
            data_quality={
                "preflight": stock_input.preflight_status.value,
                "close_quality": current_bar.field_quality.get("close"),
                "used_fields": ["close"],
                "ignored_scoring_fields": list(self.rules.raw["ignored_scoring_fields"]),
                "complete_shock_reference_days": len(selected_shock_days),
                "complete_relative_reference_days": len(relative_days),
                "feature_degradation": degradation,
                "lookahead_safe": all(datetime.fromisoformat(item.period_end).timestamp() * 1000 < target_end for item in ordered_history),
            },
            source_risk_input_id=source_stock_risk_input_id,
            source_market_60m_result_id=source_market_60m_result_id,
        )

    @staticmethod
    def _complete_history_days(history: Sequence[StockReferenceObservation], current_day: str | None) -> list[str]:
        grouped: dict[str, list[StockReferenceObservation]] = defaultdict(list)
        for item in history:
            if current_day is None or item.trading_date < current_day:
                grouped[item.trading_date].append(item)
        return sorted(day for day, items in grouped.items() if len(items) == 4)

    def _market_context(
        self, value: Mapping[str, Any] | None, source_id: str | None, target_end: int
    ) -> tuple[dict[str, Any], bool]:
        empty = {
            "market_risk_score": None,
            "market_risk_light": None,
            "market_risk_direction": None,
            "market_internal_state": None,
            "market_median_return": None,
            "broad_selloff_resonance": None,
            "strong_broad_weakness": None,
        }
        if not value or not source_id or value.get("rules_version") != self.rules.raw["source_market_60m_rules_version"]:
            return empty, False
        if value.get("last_completed_bar_end") != _iso_epoch(target_end):
            return empty, False
        states = value.get("index_states")
        returns = [item.get("close_change_pct") for item in states or () if item.get("close_change_pct") is not None]
        if len(returns) != 8 or value.get("risk_score") is None or value.get("risk_light") is None:
            return empty, False
        result = dict(empty)
        result.update(
            {
                "market_risk_score": int(value["risk_score"]),
                "market_risk_light": str(value["risk_light"]),
                "market_risk_direction": value.get("risk_direction"),
                "market_median_return": float(median(float(item) for item in returns)),
                "broad_selloff_resonance": bool(value.get("broad_selloff_resonance")),
                "strong_broad_weakness": bool(value.get("strong_broad_weakness")),
            }
        )
        return result, True

    def _input_error(self, value: RiskInput | None, source_id: str | None) -> str | None:
        if value is None:
            return "RISK_INPUT_MISSING"
        if not source_id:
            return "SOURCE_RISK_INPUT_ID_MISSING"
        if value.analysis_period is not AnalysisPeriod.MIN_60:
            return "NOT_60M_RISK_INPUT"
        if value.preflight_status is PreflightStatus.BLOCKED:
            return "PREFLIGHT_BLOCKED"
        if not value.system_bars or not value.source_trace.raw_path:
            return "SYSTEM_BAR_OR_SOURCE_TRACE_MISSING"
        bar = value.system_bars[-1]
        if bar.completion_status != "COMPLETED":
            return "CURRENT_PERIOD_INCOMPLETE"
        if bar.field_quality.get("close") not in set(self.rules.raw["trusted_close_quality"]):
            return "CLOSE_NOT_TRUSTED"
        if not bar.source_bar_ids or not bar.source_raw_paths:
            return "LINEAGE_MISSING"
        enabled = next(
            (
                item
                for item in value.feature_inputs
                if item.feature_name == "current_period_close" and item.eligibility is FeatureEligibility.ENABLED
            ),
            None,
        )
        if enabled is None or Decimal(str(enabled.value)) != Decimal(str(bar.close)):
            return "SAFE_CLOSE_FEATURE_MISSING_OR_MISMATCHED"
        as_of = datetime.fromisoformat(value.as_of)
        if as_of.tzinfo is None or bar.end > int(as_of.timestamp() * 1000):
            return "LOOKAHEAD_OR_NAIVE_AS_OF"
        return None

    @staticmethod
    def _risk_direction(
        score: int, target_end: int, previous: Stock60mRiskResult | Mapping[str, Any] | None
    ) -> RiskChangeDirection:
        if previous is None:
            return RiskChangeDirection.NOT_AVAILABLE
        previous_score = previous.risk_score if isinstance(previous, Stock60mRiskResult) else previous.get("risk_score")
        previous_end = previous.period_end if isinstance(previous, Stock60mRiskResult) else previous.get("period_end")
        if previous_score is None or not isinstance(previous_end, str):
            return RiskChangeDirection.NOT_AVAILABLE
        parsed = datetime.fromisoformat(previous_end)
        if parsed.tzinfo is None or parsed.timestamp() * 1000 >= target_end:
            return RiskChangeDirection.NOT_AVAILABLE
        delta = score - int(previous_score)
        return RiskChangeDirection.RISING if delta > 0 else RiskChangeDirection.FALLING if delta < 0 else RiskChangeDirection.FLAT

    @staticmethod
    def _disabled(feature: str, reason: str, affected: str, source: str | None) -> dict[str, Any]:
        return {
            "feature": feature,
            "status": "DISABLED",
            "reason": reason,
            "affected_field": affected,
            "source": source,
            "lineage": [],
        }

    @staticmethod
    def _advisory_degradation(value: RiskInput, source_id: str | None) -> list[dict[str, Any]]:
        bar = value.system_bars[-1]
        return [
            {
                "feature": f"{field}_context",
                "status": "ADVISORY_ONLY",
                "reason": "SAFE_FEATURE_CONTRACT_NOT_SCORING",
                "affected_field": field,
                "source": source_id,
                "lineage": list(bar.source_bar_ids),
            }
            for field in ("open", "high", "low", "volume", "turnover")
        ]

    def _blocked(
        self,
        instrument_id: str,
        symbol: str,
        name: str,
        value: RiskInput | None,
        reason: str,
        source_id: str | None,
    ) -> Stock60mRiskResult:
        return Stock60mRiskResult(
            rules_version=self.rules.rules_version,
            instrument_id=instrument_id,
            symbol=symbol,
            name=name,
            trading_date=value.trading_date if value else None,
            period_end=value.last_completed_bar_end if value else None,
            risk_score=None,
            risk_light=None,
            risk_light_symbol=None,
            risk_direction=RiskChangeDirection.NOT_AVAILABLE,
            confidence=SignalConfidence.LOW,
            current_close=None,
            previous_close=None,
            current_return=None,
            previous_return=None,
            two_period_return=None,
            consecutive_close_direction="N/A",
            persistent_weakness=False,
            downside_shock=False,
            shock_feature_status="SHOCK_FEATURE_UNAVAILABLE",
            historical_abs_return_p95=None,
            market_median_return=None,
            relative_return=None,
            relative_weakness=False,
            relative_weakness_status="RELATIVE_REFERENCE_UNAVAILABLE",
            historical_relative_return_p10=None,
            market_resonance=False,
            repair_state=CloseRepairState.NONE,
            market_relationship="UNAVAILABLE",
            divergence_flags=(),
            market_context={},
            score_components={},
            data_quality={"blocking_reason": reason, "feature_degradation": []},
            source_risk_input_id=source_id,
            source_market_60m_result_id=None,
            status="DATA_INCOMPLETE",
        )
