"""Close-only deterministic Market 60m Risk Engine v0.1."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.market_risk.rules import Market60mRiskRules
from trend_monitor.schemas import (
    AnalysisPeriod,
    CloseRepairState,
    FeatureEligibility,
    GroupRiskState,
    IndexRiskState,
    Market60mRiskResult,
    PreflightStatus,
    RiskBar,
    RiskChangeDirection,
    RiskInput,
    RiskLight,
    SignalConfidence,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
TRUSTED_CLOSE = {"TRUSTED", "TRUSTED_WITH_TRANSFORMATION"}


def _direction(change: Decimal) -> str:
    return "↑" if change > 0 else "↓" if change < 0 else "→"


def _iso_epoch(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI).isoformat()


def _day(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI).date().isoformat()


def _percentile(values: Sequence[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "percentile input is empty")
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


class Market60mRiskEngine:
    def __init__(self, rules: Market60mRiskRules) -> None:
        self.rules = rules

    def evaluate(
        self,
        current_inputs: Mapping[str, RiskInput],
        *,
        history_inputs: Mapping[str, Sequence[RiskInput]],
        source_snapshot_ids: Mapping[str, str],
        previous_result: Market60mRiskResult | Mapping[str, object] | None = None,
    ) -> Market60mRiskResult:
        candidates: dict[str, RiskInput] = {}
        invalid: dict[str, str] = {}
        for instrument_id in self.rules.instrument_ids:
            value = current_inputs.get(instrument_id)
            source_id = source_snapshot_ids.get(instrument_id)
            reason = self._input_error(value, source_id)
            if reason:
                invalid[instrument_id] = reason
            else:
                assert value is not None
                candidates[instrument_id] = value
        if not candidates:
            return self._blocked(current_inputs, invalid, SignalConfidence.LOW)

        end_counts = Counter(int(item.system_bars[-1].end) for item in candidates.values())
        target_end = sorted(end_counts, key=lambda value: (end_counts[value], value), reverse=True)[0]
        for instrument_id, item in tuple(candidates.items()):
            if item.system_bars[-1].end != target_end:
                invalid[instrument_id] = "LAST_COMPLETED_PERIOD_MISMATCH"
                del candidates[instrument_id]

        covered_groups = {
            group
            for group, members in self.rules.groups.items()
            if any(item in candidates for item in members)
        }
        if len(candidates) == 8:
            confidence = SignalConfidence.HIGH
        elif len(candidates) >= int(self.rules.raw["confidence"]["medium_valid_count_min"]) and covered_groups == set(self.rules.groups):
            confidence = SignalConfidence.MEDIUM
        else:
            return self._blocked(current_inputs, invalid, SignalConfidence.LOW)

        states: dict[str, IndexRiskState] = {}
        for instrument_id, risk_input in candidates.items():
            history = tuple(history_inputs.get(instrument_id, ()))
            bars = self._close_series(risk_input, history, target_end)
            if len(bars) < 3:
                invalid[instrument_id] = "INSUFFICIENT_CLOSE_HISTORY"
                continue
            state = self._index_state(
                instrument_id,
                bars,
                source_snapshot_ids[instrument_id],
            )
            states[instrument_id] = state

        covered_groups = {
            group
            for group, members in self.rules.groups.items()
            if any(item in states for item in members)
        }
        if len(states) < 6 or covered_groups != set(self.rules.groups):
            return self._blocked(current_inputs, invalid, SignalConfidence.LOW)
        confidence = SignalConfidence.HIGH if len(states) == 8 else SignalConfidence.MEDIUM

        advancers = sum(item.one_period_direction == "↑" for item in states.values())
        decliners = sum(item.one_period_direction == "↓" for item in states.values())
        unchanged = len(states) - advancers - decliners
        persistent_count = sum(item.persistent_weak for item in states.values())
        shock_count = sum(item.downside_shock for item in states.values())
        repair_count = sum(item.repair_state is not CloseRepairState.NONE for item in states.values())

        large = self.rules.groups["LARGE_CAP"]
        others = tuple(item for item in self.rules.instrument_ids if item not in large)
        weighted = (
            all(item in states and states[item].one_period_direction == "↑" for item in large)
            and sum(item in states and states[item].one_period_direction == "↓" for item in others)
            >= int(self.rules.raw["weighted_support_distortion"]["other_decliners_min"])
        )
        mid_small = self.rules.groups["MID_SMALL"]
        growth = self.rules.groups["GROWTH"]
        small_stress = (
            all(item in states and states[item].one_period_direction == "↓" for item in mid_small)
            and sum(item in states and states[item].one_period_direction == "↓" for item in growth)
            >= int(self.rules.raw["small_cap_stress"]["growth_decliners_min"])
        )
        broad_selloff = decliners >= int(self.rules.raw["broad_selloff_decliners_min"])
        strong_weakness = (
            decliners >= int(self.rules.raw["strong_broad_weakness"]["decliners_min"])
            and persistent_count >= int(self.rules.raw["strong_broad_weakness"]["persistent_weak_min"])
        )
        broad_repair = repair_count >= int(self.rules.raw["broad_repair"]["repair_count_min"])

        components = {
            "breadth_points": self.rules.breadth_points(decliners),
            "persistent_weakness_points": self.rules.persistent_points(persistent_count),
            "downside_shock_points": self.rules.shock_points(shock_count),
            "weighted_support_distortion_points": int(self.rules.raw["weighted_support_distortion"]["points"]) if weighted else 0,
            "broad_repair_offset": int(self.rules.raw["broad_repair"]["score_offset"]) if broad_repair else 0,
        }
        score = max(
            0,
            components["breadth_points"]
            + components["persistent_weakness_points"]
            + components["downside_shock_points"]
            + components["weighted_support_distortion_points"]
            - components["broad_repair_offset"],
        )
        light_name, light_symbol = self.rules.light(score)
        groups = tuple(self._group_state(group, members, states) for group, members in self.rules.groups.items())
        group_map = {item.group: item for item in groups}
        large_return = group_map["LARGE_CAP"].median_close_change_pct
        mid_return = group_map["MID_SMALL"].median_close_change_pct
        growth_return = group_map["GROWTH"].median_close_change_pct
        broad_return = group_map["BROAD_MARKET"].median_close_change_pct
        as_of = max(item.as_of for item in candidates.values())
        direction = self._risk_change(score, target_end, previous_result)
        ordered_states = tuple(states[item] for item in self.rules.instrument_ids if item in states)
        unavailable_shocks = tuple(
            item.instrument_id
            for item in ordered_states
            if item.shock_feature_status == "SHOCK_FEATURE_UNAVAILABLE"
        )
        return Market60mRiskResult(
            trading_date=_day(target_end),
            as_of=as_of,
            last_completed_bar_end=_iso_epoch(target_end),
            risk_score=score,
            risk_light=RiskLight(light_name),
            risk_light_symbol=light_symbol,
            risk_direction=direction,
            signal_confidence=confidence,
            breadth={"advancers": advancers, "decliners": decliners, "unchanged": unchanged},
            persistent_weakness={"count": persistent_count, "points": components["persistent_weakness_points"]},
            downside_shocks={
                "count": shock_count,
                "points": components["downside_shock_points"],
                "feature_unavailable": list(unavailable_shocks),
            },
            weighted_support_distortion=weighted,
            small_cap_stress=small_stress,
            style_divergence_strong=weighted and small_stress,
            broad_selloff_resonance=broad_selloff,
            strong_broad_weakness=strong_weakness,
            broad_repair=broad_repair,
            repair_count=repair_count,
            group_states=groups,
            index_states=ordered_states,
            style_spreads={
                "large_cap_median_return": large_return,
                "mid_small_median_return": mid_return,
                "large_minus_mid_small_spread": large_return - mid_return,
                "large_vs_mid_small": self._relative_label(large_return - mid_return, "LARGE_CAP", "MID_SMALL"),
                "growth_median_return": growth_return,
                "broad_market_median_return": broad_return,
                "growth_minus_broad_spread": growth_return - broad_return,
                "growth_vs_broad": self._relative_label(growth_return - broad_return, "GROWTH", "BROAD_MARKET"),
            },
            score_components=components,
            data_quality={
                "valid_index_count": len(states),
                "required_index_count": 8,
                "unavailable": invalid,
                "used_fields": ["close"],
                "ignored_fields": list(self.rules.raw["ignored_fields"]),
                "preflight": {item: candidates[item].preflight_status.value for item in states},
            },
            source_snapshot_ids=tuple(source_snapshot_ids[item] for item in self.rules.instrument_ids if item in states),
            rules_version=self.rules.rules_version,
        )

    def _input_error(self, value: RiskInput | None, source_id: str | None) -> str | None:
        if value is None:
            return "RISK_INPUT_MISSING"
        if not source_id:
            return "SOURCE_SNAPSHOT_ID_MISSING"
        if value.analysis_period is not AnalysisPeriod.MIN_60:
            return "NOT_60M_RISK_INPUT"
        if value.preflight_status is PreflightStatus.BLOCKED:
            return "PREFLIGHT_BLOCKED"
        if not value.system_bars:
            return "SYSTEM_BARS_MISSING"
        current = value.system_bars[-1]
        if current.completion_status != "COMPLETED":
            return "CURRENT_BAR_INCOMPLETE"
        if not current.source_raw_paths or not current.source_bar_ids or not value.source_trace.raw_path:
            return "PROVENANCE_MISSING"
        if current.field_quality.get("close") not in TRUSTED_CLOSE:
            return "CLOSE_NOT_TRUSTED"
        enabled = {
            item.feature_name: item
            for item in value.feature_inputs
            if item.eligibility is FeatureEligibility.ENABLED
        }
        feature = enabled.get("current_period_close")
        if feature is None or Decimal(str(feature.value)) != Decimal(str(current.close)):
            return "SAFE_CLOSE_FEATURE_MISSING_OR_MISMATCHED"
        if not feature.lineage or any(not item.source_raw_paths or not item.source_bar_ids for item in feature.lineage):
            return "FEATURE_LINEAGE_MISSING"
        as_of = datetime.fromisoformat(value.as_of)
        if as_of.tzinfo is None or current.end > int(as_of.timestamp() * 1000):
            return "LOOKAHEAD_OR_NAIVE_AS_OF"
        return None

    def _close_series(
        self,
        current: RiskInput,
        history: Sequence[RiskInput],
        target_end: int,
    ) -> tuple[RiskBar, ...]:
        by_end: dict[int, RiskBar] = {}
        for item in history:
            if item.preflight_status is PreflightStatus.BLOCKED:
                continue
            if item.analysis_period is not AnalysisPeriod.MIN_60:
                raise TrendMonitorError(ErrorCategory.INVALID_DATA, "history contains non-60m input")
            if item.system_bars and item.system_bars[-1].end >= target_end:
                raise TrendMonitorError(ErrorCategory.INVALID_DATA, "historical Risk Input is not before current period")
            for bar in item.system_bars:
                if bar.end >= target_end:
                    raise TrendMonitorError(ErrorCategory.INVALID_DATA, "historical bar violates as-of boundary")
                by_end[bar.end] = bar
        for bar in current.system_bars:
            if bar.end > target_end:
                raise TrendMonitorError(ErrorCategory.INVALID_DATA, "current Risk Input contains future completed bar")
            by_end[bar.end] = bar
        return tuple(by_end[key] for key in sorted(by_end))

    def _index_state(
        self,
        instrument_id: str,
        bars: tuple[RiskBar, ...],
        source_snapshot_id: str,
    ) -> IndexRiskState:
        closes = [Decimal(str(item.close)) for item in bars]
        current, previous, two_ago = closes[-1], closes[-2], closes[-3]
        current_return = current / previous - Decimal(1) if previous else Decimal(0)
        previous_return = previous / two_ago - Decimal(1) if two_ago else Decimal(0)
        if previous_return < 0 and current > previous:
            repair = (
                CloseRepairState.FULL_CLOSE_REPAIR
                if current >= two_ago
                else CloseRepairState.REPAIR_ATTEMPT
            )
        else:
            repair = CloseRepairState.NONE
        last_two = (previous_return, current_return)
        three_direction = "↑" if all(item > 0 for item in last_two) else "↓" if all(item < 0 for item in last_two) else "→"
        window = closes[-int(self.rules.raw["recent_close_window_periods"]):]
        recent_high, recent_low = max(window), min(window)

        prior_bars = bars[:-1]
        prior_dates = sorted({_day(item.end) for item in prior_bars})
        min_days = int(self.rules.raw["downside_shock"]["minimum_complete_trading_days"])
        threshold = None
        shock = False
        shock_status = "SHOCK_FEATURE_UNAVAILABLE"
        if len(prior_dates) >= min_days:
            selected_dates = set(prior_dates[-min_days:])
            historical_returns = [
                abs(Decimal(str(right.close)) / Decimal(str(left.close)) - Decimal(1))
                for left, right in zip(prior_bars, prior_bars[1:])
                if _day(right.end) in selected_dates and left.close != 0
            ]
            if historical_returns:
                threshold = _percentile(
                    historical_returns,
                    Decimal(str(self.rules.raw["downside_shock"]["percentile"])),
                )
                shock = current_return < 0 and abs(current_return) >= threshold
                shock_status = "AVAILABLE"
        return IndexRiskState(
            instrument_id=instrument_id,
            name=self.rules.instrument_name(instrument_id),
            close=float(current),
            close_change_pct=float(current_return),
            one_period_direction=_direction(current_return),
            two_period_direction=_direction(current - two_ago),
            three_period_close_direction=three_direction,
            persistent_weak=current_return < 0 and previous_return < 0,
            repair_state=repair,
            downside_shock=shock,
            shock_feature_status=shock_status,
            shock_reference_p95=float(threshold) if threshold is not None else None,
            recent_close_high=float(recent_high),
            recent_close_low=float(recent_low),
            close_drawdown_from_recent_close_high=float(current / recent_high - Decimal(1)),
            quality=bars[-1].field_quality["close"],
            source_snapshot_id=source_snapshot_id,
        )

    @staticmethod
    def _group_state(
        group: str,
        members: tuple[str, ...],
        states: Mapping[str, IndexRiskState],
    ) -> GroupRiskState:
        available = [states[item] for item in members if item in states]
        advances = sum(item.one_period_direction == "↑" for item in available)
        declines = sum(item.one_period_direction == "↓" for item in available)
        unchanged = len(available) - advances - declines
        direction = "↑" if advances > declines else "↓" if declines > advances else "→"
        return GroupRiskState(
            group=group,
            group_advancers=advances,
            group_decliners=declines,
            group_unchanged=unchanged,
            median_close_change_pct=float(median(item.close_change_pct for item in available)),
            group_direction=direction,
            valid_count=len(available),
        )

    @staticmethod
    def _relative_label(spread: float, stronger: str, weaker: str) -> str:
        return f"{stronger}_STRONGER" if spread > 0 else f"{stronger}_WEAKER" if spread < 0 else "IN_SYNC"

    @staticmethod
    def _risk_change(
        score: int,
        target_end: int,
        previous: Market60mRiskResult | Mapping[str, object] | None,
    ) -> RiskChangeDirection:
        if previous is None:
            return RiskChangeDirection.NOT_AVAILABLE
        if isinstance(previous, Market60mRiskResult):
            previous_score = previous.risk_score
            previous_end_text = previous.last_completed_bar_end
        else:
            previous_score = previous.get("risk_score")
            previous_end_text = previous.get("last_completed_bar_end")
        if previous_score is None or not isinstance(previous_end_text, str):
            return RiskChangeDirection.NOT_AVAILABLE
        previous_end = datetime.fromisoformat(previous_end_text)
        if previous_end.tzinfo is None or int(previous_end.timestamp() * 1000) >= target_end:
            return RiskChangeDirection.NOT_AVAILABLE
        delta = score - int(previous_score)
        return RiskChangeDirection.RISING if delta > 0 else RiskChangeDirection.FALLING if delta < 0 else RiskChangeDirection.FLAT

    def _blocked(
        self,
        inputs: Mapping[str, RiskInput],
        invalid: Mapping[str, str],
        confidence: SignalConfidence,
    ) -> Market60mRiskResult:
        as_of = max(
            (item.as_of for item in inputs.values()),
            default="1970-01-01T00:00:00+08:00",
        )
        return Market60mRiskResult(
            trading_date=None,
            as_of=as_of,
            last_completed_bar_end=None,
            risk_score=None,
            risk_light=None,
            risk_light_symbol=None,
            risk_direction=RiskChangeDirection.NOT_AVAILABLE,
            signal_confidence=confidence,
            breadth={"advancers": 0, "decliners": 0, "unchanged": 0},
            persistent_weakness={"count": 0, "points": 0},
            downside_shocks={"count": 0, "points": 0, "feature_unavailable": []},
            weighted_support_distortion=False,
            small_cap_stress=False,
            style_divergence_strong=False,
            broad_selloff_resonance=False,
            strong_broad_weakness=False,
            broad_repair=False,
            repair_count=0,
            group_states=(),
            index_states=(),
            style_spreads={},
            score_components={},
            data_quality={
                "valid_index_count": 0,
                "required_index_count": 8,
                "unavailable": dict(invalid),
                "used_fields": ["close"],
                "ignored_fields": list(self.rules.raw["ignored_fields"]),
            },
            source_snapshot_ids=(),
            rules_version=self.rules.rules_version,
            status=ErrorCategory.DATA_INCOMPLETE.value,
        )
