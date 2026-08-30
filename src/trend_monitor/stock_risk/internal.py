"""Stock 15m internal auxiliary using the frozen shared four-Close classifier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.market_internal import classify_close_structure
from trend_monitor.schemas import (
    AnalysisPeriod,
    FeatureEligibility,
    InternalClassification,
    InternalPeriodStatus,
    PreflightStatus,
    RiskInput,
    Stock15mInternalResult,
)
from trend_monitor.stock_risk.rules import StockIntradayRiskRules


SHANGHAI = ZoneInfo("Asia/Shanghai")
VALID_ENDS = {(10, 30), (11, 30), (14, 0), (15, 0)}


def _epoch(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class Stock15mInternalEngine:
    def __init__(self, rules: StockIntradayRiskRules) -> None:
        self.rules = rules

    def evaluate(
        self,
        stock_input: RiskInput,
        *,
        as_of: datetime,
        period_start: datetime,
        period_end: datetime,
        source_stock_risk_input_id: str | None,
        market_15m_result: Mapping[str, Any] | None,
        source_market_15m_result_id: str | None,
        external_previous_close: tuple[float, str] | None = None,
    ) -> Stock15mInternalResult:
        self._validate_period(as_of, period_start, period_end)
        instrument_id = stock_input.instrument_id
        if instrument_id not in self.rules.instrument_ids:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock is outside TASK_010")
        symbol, name = self.rules.identity(instrument_id)
        status = InternalPeriodStatus.COMPLETED if as_of >= period_end else InternalPeriodStatus.IN_PROGRESS
        error = self._input_error(stock_input, source_stock_risk_input_id, as_of)
        start_ms, end_ms, as_of_ms = _epoch(period_start), _epoch(period_end), _epoch(as_of)
        bars = tuple(
            item for item in stock_input.system_bars if item.start >= start_ms and item.end <= min(end_ms, as_of_ms)
        )
        expected_ends = tuple(start_ms + (index + 1) * 15 * 60 * 1000 for index in range(len(bars)))
        if not error and tuple(item.end for item in bars) != expected_ends:
            error = "15M_PERIOD_ALIGNMENT_INVALID"
        if not error and status is InternalPeriodStatus.COMPLETED and len(bars) != 4:
            error = f"COMPLETED_15M_COUNT:{len(bars)}/4"
        if not error and status is InternalPeriodStatus.IN_PROGRESS and not 1 <= len(bars) <= 3:
            error = f"IN_PROGRESS_15M_COUNT:{len(bars)}"
        baseline = next((item for item in stock_input.system_bars if item.end == start_ms), None)
        if baseline:
            previous_close = baseline.close
            previous_quality = baseline.field_quality.get("close", "UNKNOWN")
        elif external_previous_close:
            previous_close, previous_quality = external_previous_close
        else:
            previous_close, previous_quality = 0.0, "UNKNOWN"
            error = error or "PREVIOUS_60M_CLOSE_MISSING"
        trusted = set(self.rules.raw["trusted_close_quality"])
        if not error and (previous_quality not in trusted or any(item.field_quality.get("close") not in trusted for item in bars)):
            error = "CLOSE_NOT_TRUSTED"
        if error:
            return Stock15mInternalResult(
                rules_version=self.rules.internal_rules_version,
                instrument_id=instrument_id,
                symbol=symbol,
                name=name,
                period_60m_start=period_start.isoformat(),
                period_60m_end=period_end.isoformat(),
                period_status=status,
                completed_15m_count=0,
                classification=InternalClassification.UNAVAILABLE,
                direction_sequence=(),
                closes=(),
                close_changes_pct=(),
                repair_strength=None,
                finish_position=None,
                joint_market_flags=(),
                source_stock_risk_input_id=source_stock_risk_input_id,
                source_market_15m_result_id=source_market_15m_result_id,
                data_quality={"reason": error, "lookahead_safe": False},
                status="DATA_INCOMPLETE",
            )
        closes = tuple(item.close for item in bars)
        structure = classify_close_structure(
            closes,
            previous_close=previous_close,
            precedence=self.rules.raw["classification_precedence"],
            healthy_direction_min=int(self.rules.raw["healthy_direction_min"]),
            completed=status is InternalPeriodStatus.COMPLETED,
        )
        market_state = None
        market_available = bool(
            market_15m_result
            and source_market_15m_result_id
            and market_15m_result.get("rules_version") == self.rules.raw["source_market_15m_rules_version"]
            and (
                market_15m_result.get("60m_period_end") == period_end.isoformat()
                if status is InternalPeriodStatus.COMPLETED
                else True
            )
        )
        if market_available:
            market_state = str(market_15m_result.get("market_internal_state"))
        joint = []
        classification = structure.classification
        if classification in {
            InternalClassification.HEALTHY_DOWN,
            InternalClassification.LATE_WEAKENING,
            InternalClassification.FAILED_REPAIR,
        } and market_state == "WEAKNESS_BROADENING":
            joint.append("JOINT_WEAKNESS")
        if classification in {InternalClassification.LATE_REPAIR, InternalClassification.HEALTHY_UP}:
            if market_state == "WEAKNESS_BROADENING":
                joint.append("STOCK_REPAIR_AGAINST_WEAK_MARKET")
            if market_state == "REPAIR_BROADENING":
                joint.append("JOINT_REPAIR")
        return Stock15mInternalResult(
            rules_version=self.rules.internal_rules_version,
            instrument_id=instrument_id,
            symbol=symbol,
            name=name,
            period_60m_start=period_start.isoformat(),
            period_60m_end=period_end.isoformat(),
            period_status=status,
            completed_15m_count=len(bars),
            classification=classification,
            direction_sequence=structure.direction_sequence,
            closes=tuple(float(item) for item in closes),
            close_changes_pct=structure.close_changes_pct,
            repair_strength=structure.repair_strength,
            finish_position=structure.finish_position,
            joint_market_flags=tuple(joint),
            source_stock_risk_input_id=source_stock_risk_input_id,
            source_market_15m_result_id=source_market_15m_result_id,
            data_quality={
                "market_15m_available": market_available,
                "market_internal_state": market_state,
                "used_fields": ["close"],
                "ignored_for_classification": list(self.rules.raw["ignored_scoring_fields"]),
                "lookahead_safe": all(item.end <= as_of_ms for item in bars),
                "source_bar_ids": [bar_id for item in bars for bar_id in item.source_bar_ids],
            },
        )

    @staticmethod
    def _validate_period(as_of: datetime, start: datetime, end: datetime) -> None:
        if any(item.tzinfo is None for item in (as_of, start, end)):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock internal timestamps must be timezone-aware")
        if end.astimezone(SHANGHAI) - start.astimezone(SHANGHAI) != timedelta(hours=1):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "stock internal period must span one hour")
        local_end = end.astimezone(SHANGHAI)
        if (local_end.hour, local_end.minute) not in VALID_ENDS or not start <= as_of <= end:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "invalid stock 60m internal period")

    @staticmethod
    def _input_error(value: RiskInput, source_id: str | None, as_of: datetime) -> str | None:
        if not source_id:
            return "SOURCE_RISK_INPUT_ID_MISSING"
        if value.analysis_period is not AnalysisPeriod.MIN_15:
            return "NOT_15M_RISK_INPUT"
        if value.preflight_status is PreflightStatus.BLOCKED:
            return "PREFLIGHT_BLOCKED"
        if not value.system_bars or not value.source_trace.raw_path:
            return "SYSTEM_BAR_OR_SOURCE_TRACE_MISSING"
        enabled = next(
            (
                item
                for item in value.feature_inputs
                if item.feature_name == "current_period_close" and item.eligibility is FeatureEligibility.ENABLED
            ),
            None,
        )
        if enabled is None or Decimal(str(enabled.value)) != Decimal(str(value.system_bars[-1].close)):
            return "SAFE_CLOSE_FEATURE_MISSING_OR_MISMATCHED"
        if any(not item.source_bar_ids or not item.source_raw_paths for item in value.system_bars):
            return "LINEAGE_MISSING"
        if any(item.end > _epoch(as_of) for item in value.system_bars):
            return "LOOKAHEAD_BAR_PRESENT"
        return None
