"""Deterministic Close-only 15m internal structure auxiliary engine."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.market_internal.close_structure import classify_close_structure
from trend_monitor.market_internal.rules import Market15mInternalRules
from trend_monitor.market_risk.rules import Market60mRiskRules
from trend_monitor.schemas import (
    AnalysisPeriod,
    FeatureEligibility,
    Group15mInternalState,
    Index15mInternalState,
    InternalClassification,
    InternalPeriodStatus,
    Market15mInternalResult,
    MarketInternalState,
    PreflightStatus,
    RiskBar,
    RiskInput,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
VALID_PERIOD_ENDS = {(10, 30), (11, 30), (14, 0), (15, 0)}
POSITIVE = {
    InternalClassification.HEALTHY_UP,
    InternalClassification.LATE_REPAIR,
    InternalClassification.EARLY_STRENGTH,
}
NEGATIVE = {
    InternalClassification.HEALTHY_DOWN,
    InternalClassification.LATE_WEAKENING,
    InternalClassification.FAILED_REPAIR,
    InternalClassification.EARLY_WEAKNESS,
}


def _epoch(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _iso_epoch(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(SHANGHAI).isoformat()


class Market15mInternalEngine:
    def __init__(
        self,
        rules: Market15mInternalRules,
        source_rules: Market60mRiskRules,
    ) -> None:
        self.rules = rules
        self.source_rules = source_rules
        if source_rules.rules_version != rules.source_60m_rules_version:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "15m/60m rules linkage mismatch")

    def evaluate(
        self,
        inputs: Mapping[str, RiskInput],
        *,
        as_of: datetime,
        period_start: datetime,
        period_end: datetime,
        source_risk_input_ids: Mapping[str, str],
        source_60m_risk_result: Mapping[str, Any],
        source_60m_risk_result_id: str,
        previous_60m_closes: Mapping[str, tuple[float, str]] | None = None,
    ) -> Market15mInternalResult:
        self._validate_period(as_of, period_start, period_end)
        linked = self._linked_60m(source_60m_risk_result, source_60m_risk_result_id)
        period_status = (
            InternalPeriodStatus.COMPLETED
            if as_of.astimezone(SHANGHAI) >= period_end.astimezone(SHANGHAI)
            else InternalPeriodStatus.IN_PROGRESS
        )
        linked_end = datetime.fromisoformat(str(linked["last_completed_bar_end"]))
        if period_status is InternalPeriodStatus.COMPLETED and linked_end != period_end:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "completed 15m view is not linked to the same 60m period")
        if period_status is InternalPeriodStatus.IN_PROGRESS and linked_end > period_start:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "in-progress view links a future 60m result")

        previous = previous_60m_closes or {}
        states = {}
        invalid = {}
        for instrument_id in self.source_rules.instrument_ids:
            state = self._index_state(
                instrument_id,
                inputs.get(instrument_id),
                as_of=as_of,
                period_start=period_start,
                period_end=period_end,
                period_status=period_status,
                source_id=source_risk_input_ids.get(instrument_id),
                external_previous=previous.get(instrument_id),
            )
            states[instrument_id] = state
            if state.classification is InternalClassification.UNAVAILABLE:
                invalid[instrument_id] = state.reason or "UNAVAILABLE"

        valid = {
            key: value
            for key, value in states.items()
            if value.classification is not InternalClassification.UNAVAILABLE
        }
        covered_groups = {
            group
            for group, members in self.source_rules.groups.items()
            if any(member in valid for member in members)
        }
        target_count = 4 if period_status is InternalPeriodStatus.COMPLETED else max(
            (item.completed_15m_count for item in valid.values()),
            default=0,
        )
        mismatched = {
            key: value.completed_15m_count
            for key, value in valid.items()
            if value.completed_15m_count != target_count
        }
        if mismatched:
            for key in mismatched:
                invalid[key] = f"COMPLETED_15M_COUNT_MISMATCH:{mismatched[key]}/{target_count}"
                del valid[key]
            covered_groups = {
                group
                for group, members in self.source_rules.groups.items()
                if any(member in valid for member in members)
            }

        classification_counts = Counter(item.classification.value for item in valid.values())
        keys = (
            self.rules.complete_classifications
            if period_status is InternalPeriodStatus.COMPLETED
            else self.rules.early_classifications
        )
        counts = {key: classification_counts[key] for key in keys}
        counts["UNAVAILABLE"] = 8 - len(valid)
        coverage_ok = (
            len(valid) >= int(self.rules.raw["minimum_valid_indexes"])
            and covered_groups == set(self.source_rules.groups)
        )
        if not coverage_ok:
            market_state = MarketInternalState.DATA_INCOMPLETE
            status = ErrorCategory.DATA_INCOMPLETE.value
        elif period_status is InternalPeriodStatus.IN_PROGRESS:
            market_state = MarketInternalState.INTERNAL_MIXED
            status = "READY"
        else:
            repair = counts["LATE_REPAIR"] + counts["HEALTHY_UP"]
            weakness = counts["HEALTHY_DOWN"] + counts["LATE_WEAKENING"] + counts["FAILED_REPAIR"]
            threshold = int(self.rules.raw["market_broadening_min"])
            market_state = (
                MarketInternalState.REPAIR_BROADENING
                if repair >= threshold
                else MarketInternalState.WEAKNESS_BROADENING
                if weakness >= threshold
                else MarketInternalState.INTERNAL_MIXED
            )
            status = "READY"

        ordered = tuple(states[item] for item in self.source_rules.instrument_ids)
        groups = tuple(
            self._group_state(group, members, valid)
            for group, members in self.source_rules.groups.items()
        )
        return Market15mInternalResult(
            trading_date=period_end.astimezone(SHANGHAI).date().isoformat(),
            period_60m_start=period_start.astimezone(SHANGHAI).isoformat(),
            period_60m_end=period_end.astimezone(SHANGHAI).isoformat(),
            period_status=period_status,
            completed_15m_count=target_count,
            market_internal_state=market_state,
            classification_counts=counts,
            index_internal_states=ordered,
            group_states=groups,
            source_risk_input_ids=tuple(
                source_risk_input_ids[item]
                for item in self.source_rules.instrument_ids
                if item in source_risk_input_ids
            ),
            source_60m_risk_result_id=source_60m_risk_result_id,
            linked_60m_risk=linked,
            data_quality={
                "valid_index_count": len(valid),
                "required_index_count": 8,
                "minimum_valid_index_count": int(self.rules.raw["minimum_valid_indexes"]),
                "covered_groups": sorted(covered_groups),
                "unavailable": invalid,
                "used_fields": ["close"],
                "ignored_for_classification": ["open", "high", "low", "volume", "turnover"],
                "lookahead_safe": all(
                    bar.end <= _epoch(as_of)
                    for item in valid
                    for bar in inputs[item].system_bars
                ),
            },
            rules_version=self.rules.rules_version,
            status=status,
        )

    def _index_state(
        self,
        instrument_id: str,
        value: RiskInput | None,
        *,
        as_of: datetime,
        period_start: datetime,
        period_end: datetime,
        period_status: InternalPeriodStatus,
        source_id: str | None,
        external_previous: tuple[float, str] | None,
    ) -> Index15mInternalState:
        reason = self._input_error(value, source_id, as_of)
        if reason:
            return self._unavailable(instrument_id, source_id, reason)
        assert value is not None
        start_ms, end_ms, as_of_ms = _epoch(period_start), _epoch(period_end), _epoch(as_of)
        bars = tuple(
            item
            for item in value.system_bars
            if item.start >= start_ms and item.end <= min(end_ms, as_of_ms)
        )
        expected_ends = tuple(start_ms + (index + 1) * 15 * 60 * 1000 for index in range(len(bars)))
        if tuple(item.end for item in bars) != expected_ends:
            return self._unavailable(instrument_id, source_id, "15M_PERIOD_ALIGNMENT_INVALID")
        if period_status is InternalPeriodStatus.COMPLETED and len(bars) != 4:
            return self._unavailable(instrument_id, source_id, f"COMPLETED_15M_COUNT:{len(bars)}/4")
        if period_status is InternalPeriodStatus.IN_PROGRESS and not 1 <= len(bars) <= 3:
            return self._unavailable(instrument_id, source_id, f"IN_PROGRESS_15M_COUNT:{len(bars)}")
        trusted = set(self.rules.raw["trusted_close_quality"])
        if any(item.field_quality.get("close") not in trusted for item in bars):
            return self._unavailable(instrument_id, source_id, "CLOSE_NOT_TRUSTED")

        baseline_bar = next((item for item in value.system_bars if item.end == start_ms), None)
        if baseline_bar is not None:
            previous_close = baseline_bar.close
            previous_quality = baseline_bar.field_quality.get("close", "UNKNOWN")
        elif external_previous is not None:
            previous_close, previous_quality = external_previous
        else:
            return self._unavailable(instrument_id, source_id, "PREVIOUS_60M_CLOSE_MISSING")
        if previous_quality not in trusted or previous_close == 0:
            return self._unavailable(instrument_id, source_id, "PREVIOUS_60M_CLOSE_NOT_TRUSTED")

        closes = tuple(Decimal(str(item.close)) for item in bars)
        structure = classify_close_structure(
            closes,
            previous_close=previous_close,
            precedence=self.rules.raw["classification_precedence"],
            healthy_direction_min=int(self.rules.raw["healthy_direction_min"]),
            completed=period_status is InternalPeriodStatus.COMPLETED,
        )
        raw_paths = tuple(dict.fromkeys(path for item in bars for path in item.source_raw_paths))
        bar_ids = tuple(bar_id for item in bars for bar_id in item.source_bar_ids)
        return Index15mInternalState(
            instrument_id=instrument_id,
            name=self.source_rules.instrument_name(instrument_id),
            classification=structure.classification,
            completed_15m_count=len(bars),
            direction_sequence=structure.direction_sequence,
            closes=tuple(float(item) for item in closes),
            close_changes_pct=structure.close_changes_pct,
            repair_strength=structure.repair_strength,
            finish_position=structure.finish_position,
            close_quality=tuple(item.field_quality["close"] for item in bars),
            source_risk_input_id=source_id,
            source_bar_ids=bar_ids,
            source_raw_paths=raw_paths,
        )

    @staticmethod
    def _group_state(
        group: str,
        members: tuple[str, ...],
        valid: Mapping[str, Index15mInternalState],
    ) -> Group15mInternalState:
        values = [valid[item].classification for item in members if item in valid]
        counts = Counter(item.value for item in values)
        if not values:
            state = "UNAVAILABLE"
        elif len(set(values)) == 1:
            state = values[0].value
        elif all(item in POSITIVE for item in values):
            state = "REPAIRING"
        elif all(item in NEGATIVE for item in values):
            state = "WEAKENING"
        else:
            state = "MIXED"
        return Group15mInternalState(
            group=group,
            state=state,
            valid_count=len(values),
            classification_counts=dict(sorted(counts.items())),
        )

    def _input_error(self, value: RiskInput | None, source_id: str | None, as_of: datetime) -> str | None:
        if value is None:
            return "RISK_INPUT_MISSING"
        if not source_id:
            return "RISK_INPUT_SNAPSHOT_ID_MISSING"
        if value.analysis_period is not AnalysisPeriod.MIN_15:
            return "NOT_15M_RISK_INPUT"
        if value.layer_role != "internal_structure_support_for_60m_only":
            return "LAYER_ROLE_MISMATCH"
        if value.preflight_status is PreflightStatus.BLOCKED:
            return "PREFLIGHT_BLOCKED"
        if not value.source_trace.raw_path or not value.system_bars:
            return "SOURCE_TRACE_OR_SYSTEM_BARS_MISSING"
        enabled_close = next(
            (
                item
                for item in value.feature_inputs
                if item.feature_name == "current_period_close"
                and item.eligibility is FeatureEligibility.ENABLED
            ),
            None,
        )
        if enabled_close is None or Decimal(str(enabled_close.value)) != Decimal(str(value.system_bars[-1].close)):
            return "SAFE_CLOSE_FEATURE_MISSING_OR_MISMATCHED"
        if any(
            item.completion_status != "COMPLETED"
            or not item.source_bar_ids
            or not item.source_raw_paths
            for item in value.system_bars
        ):
            return "SYSTEM_BAR_PROVENANCE_INVALID"
        if any(item.end > _epoch(as_of) for item in value.system_bars):
            return "LOOKAHEAD_BAR_PRESENT"
        return None

    def _linked_60m(self, value: Mapping[str, Any], source_id: str) -> dict[str, Any]:
        if not source_id:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "source 60m Risk Result ID is missing")
        if value.get("rules_version") != self.rules.source_60m_rules_version:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "source 60m Risk Result rules changed")
        required = ("risk_score", "risk_light", "risk_direction", "last_completed_bar_end")
        if any(value.get(key) is None for key in required):
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "source 60m Risk Result is incomplete")
        return {
            "rules_version": value["rules_version"],
            "last_completed_bar_end": value["last_completed_bar_end"],
            "risk_score": value["risk_score"],
            "risk_light": value["risk_light"],
            "risk_light_symbol": value.get("risk_light_symbol"),
            "risk_direction": value["risk_direction"],
        }

    @staticmethod
    def _validate_period(as_of: datetime, start: datetime, end: datetime) -> None:
        if any(item.tzinfo is None for item in (as_of, start, end)):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "15m internal timestamps must be timezone-aware")
        local_start, local_end = start.astimezone(SHANGHAI), end.astimezone(SHANGHAI)
        if local_end - local_start != timedelta(hours=1):
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "internal view must span exactly one hour")
        if (local_end.hour, local_end.minute) not in VALID_PERIOD_ENDS:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "invalid formal 60m period end")
        if as_of.astimezone(SHANGHAI) < local_start or as_of.astimezone(SHANGHAI) > local_end:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of is outside target 60m period")

    def _unavailable(
        self, instrument_id: str, source_id: str | None, reason: str
    ) -> Index15mInternalState:
        return Index15mInternalState(
            instrument_id=instrument_id,
            name=self.source_rules.instrument_name(instrument_id),
            classification=InternalClassification.UNAVAILABLE,
            completed_15m_count=0,
            direction_sequence=(),
            closes=(),
            close_changes_pct=(),
            repair_strength=None,
            finish_position=None,
            close_quality=(),
            source_risk_input_id=source_id,
            source_bar_ids=(),
            source_raw_paths=(),
            reason=reason,
        )
