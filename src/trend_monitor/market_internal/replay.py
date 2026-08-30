"""Build as-of-safe 15m Risk Inputs and replay them against frozen TASK_008 results."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
import json
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.market_internal.engine import Market15mInternalEngine
from trend_monitor.market_internal.rules import Market15mInternalRules
from trend_monitor.market_risk.rules import Market60mRiskRules
from trend_monitor.risk_input import RiskInputAssembler
from trend_monitor.schemas import InternalClassification, Market15mInternalResult, ProviderDataResult, RiskInput
from trend_monitor.validation import record_timestamp


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class InternalReplayPeriod:
    as_of: datetime
    period_start: datetime
    period_end: datetime
    inputs: dict[str, RiskInput]
    source_risk_input_ids: dict[str, str]
    previous_60m_closes: dict[str, tuple[float, str]]
    source_60m_risk_result: dict[str, Any]
    source_60m_risk_result_id: str


@dataclass(frozen=True, slots=True)
class InternalReplayReport:
    results: tuple[Market15mInternalResult, ...]
    classification_distribution: dict[str, int]
    market_state_distribution: dict[str, int]
    cohort_analysis: dict[str, dict[str, float | int]]
    risk_up_precursors: dict[str, float | int]
    risk_down_precursors: dict[str, float | int]
    sample_audit: dict[str, list[dict[str, object]]]
    deterministic: bool
    lookahead_safe: bool
    score_immutable: bool
    periods: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "periods": self.periods,
            "classification_distribution": dict(self.classification_distribution),
            "market_state_distribution": dict(self.market_state_distribution),
            "cohort_analysis": self.cohort_analysis,
            "risk_up_precursors": self.risk_up_precursors,
            "risk_down_precursors": self.risk_down_precursors,
            "sample_audit": self.sample_audit,
            "deterministic": self.deterministic,
            "lookahead_safe": self.lookahead_safe,
            "score_immutable": self.score_immutable,
            "results": [item.to_dict() for item in self.results],
        }


class Historical15mRiskInputBuilder:
    def __init__(
        self,
        assembler: RiskInputAssembler,
        rules: Market15mInternalRules,
        source_rules: Market60mRiskRules,
        asset_types: Mapping[str, object],
    ) -> None:
        self.assembler = assembler
        self.rules = rules
        self.source_rules = source_rules
        self.asset_types = asset_types

    def build(
        self,
        source_results: Mapping[str, ProviderDataResult],
        source_60m_results: Sequence[Mapping[str, Any]],
        *,
        source_60m_replay_id: str,
    ) -> tuple[InternalReplayPeriod, ...]:
        if len(source_60m_results) != 80:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "TASK_009 replay requires exactly 80 TASK_008 periods")
        if set(source_results) != set(self.source_rules.instrument_ids):
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "historical 15m source coverage is not 8/8")
        first_end = datetime.fromisoformat(str(source_60m_results[0]["last_completed_bar_end"]))
        previous = self._previous_day_closes(source_results, first_end.date())
        periods = []
        for risk_result in source_60m_results:
            end = datetime.fromisoformat(str(risk_result["last_completed_bar_end"])).astimezone(SHANGHAI)
            start = end - timedelta(hours=1)
            inputs = self.build_inputs_at(source_results, trading_day=end.date(), as_of=end)
            source_ids = {
                instrument_id: f"{risk_input.source_trace.raw_path}#risk_input_as_of={end.isoformat()}"
                for instrument_id, risk_input in inputs.items()
            }
            periods.append(
                InternalReplayPeriod(
                    as_of=end,
                    period_start=start,
                    period_end=end,
                    inputs=inputs,
                    source_risk_input_ids=source_ids,
                    previous_60m_closes=dict(previous),
                    source_60m_risk_result=dict(risk_result),
                    source_60m_risk_result_id=f"{source_60m_replay_id}#period={end.isoformat()}",
                )
            )
            for instrument_id, risk_input in inputs.items():
                if risk_input.system_bars:
                    last = risk_input.system_bars[-1]
                    previous[instrument_id] = (last.close, last.field_quality.get("close", "UNKNOWN"))
        return tuple(periods)

    def build_inputs_at(
        self,
        source_results: Mapping[str, ProviderDataResult],
        *,
        trading_day: date,
        as_of: datetime,
    ) -> dict[str, RiskInput]:
        result = {}
        for instrument_id in self.source_rules.instrument_ids:
            source = source_results[instrument_id]
            selected = tuple(
                item
                for item in source.normalized
                if record_timestamp(item).date() == trading_day
                and (
                    record_timestamp(item) < as_of
                    or record_timestamp(item) == as_of
                    and as_of.astimezone(SHANGHAI).strftime("%H:%M") == "15:00"
                )
            )
            if not selected:
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, f"15m history missing: {instrument_id} {trading_day}")
            metadata = replace(
                source.metadata,
                source_timestamp=max(item.timestamp for item in selected if item.timestamp is not None),
            )
            sliced = replace(source, normalized=selected, metadata=metadata)
            risk_input = self.assembler.assemble_minute(
                sliced,
                asset_type=self.asset_types[instrument_id],
                period="15m",
                as_of=as_of,
                trading_date=trading_day.isoformat(),
            )
            result[instrument_id] = risk_input
        return result

    def _previous_day_closes(
        self,
        source_results: Mapping[str, ProviderDataResult],
        first_day: date,
    ) -> dict[str, tuple[float, str]]:
        common = None
        for source in source_results.values():
            days = {record_timestamp(item).date() for item in source.normalized if record_timestamp(item).date() < first_day}
            common = days if common is None else common & days
        if not common:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "previous trading day 15m Risk Input is missing")
        previous_day = max(common)
        as_of = datetime.combine(previous_day, time(15, 0), tzinfo=SHANGHAI)
        inputs = self.build_inputs_at(source_results, trading_day=previous_day, as_of=as_of)
        result = {}
        for instrument_id, risk_input in inputs.items():
            if not risk_input.system_bars:
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "previous 60m Close is missing")
            last = risk_input.system_bars[-1]
            result[instrument_id] = (last.close, last.field_quality.get("close", "UNKNOWN"))
        return result


def run_internal_replay(
    engine: Market15mInternalEngine,
    periods: Sequence[InternalReplayPeriod],
) -> InternalReplayReport:
    if len(periods) != 80:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "internal replay requires 80 periods")
    frozen_before = json.dumps(
        [deepcopy(item.source_60m_risk_result) for item in periods],
        ensure_ascii=False,
        sort_keys=True,
    )
    results = []
    deterministic = True
    lookahead = True
    for period in periods:
        kwargs = {
            "as_of": period.as_of,
            "period_start": period.period_start,
            "period_end": period.period_end,
            "source_risk_input_ids": period.source_risk_input_ids,
            "source_60m_risk_result": period.source_60m_risk_result,
            "source_60m_risk_result_id": period.source_60m_risk_result_id,
            "previous_60m_closes": period.previous_60m_closes,
        }
        first = engine.evaluate(period.inputs, **kwargs)
        second = engine.evaluate(period.inputs, **kwargs)
        deterministic = deterministic and first.to_dict() == second.to_dict()
        lookahead = lookahead and bool(first.data_quality.get("lookahead_safe"))
        results.append(first)
    frozen_after = json.dumps(
        [item.source_60m_risk_result for item in periods],
        ensure_ascii=False,
        sort_keys=True,
    )
    score_immutable = frozen_before == frozen_after and all(
        result.linked_60m_risk["risk_score"] == period.source_60m_risk_result["risk_score"]
        for result, period in zip(results, periods)
    )

    classifications = Counter(
        state.classification.value
        for result in results
        for state in result.index_internal_states
    )
    market_states = Counter(result.market_internal_state.value for result in results)
    samples = {
        item.value: []
        for item in InternalClassification
        if item is not InternalClassification.UNAVAILABLE
    }
    for result in results:
        for state in result.index_internal_states:
            bucket = samples.get(state.classification.value)
            if bucket is not None and len(bucket) < 3:
                bucket.append(
                    {
                        "60m_period_end": result.period_60m_end,
                        "instrument_id": state.instrument_id,
                        "direction_sequence": list(state.direction_sequence),
                        "closes": list(state.closes),
                        "source_risk_input_id": state.source_risk_input_id,
                    }
                )

    return InternalReplayReport(
        results=tuple(results),
        classification_distribution={key: classifications[key] for key in engine.rules.complete_classifications},
        market_state_distribution=dict(sorted(market_states.items())),
        cohort_analysis={
            "ORANGE_RED": _cohort(results, {"ORANGE", "RED"}),
            "GREEN": _cohort(results, {"GREEN"}),
        },
        risk_up_precursors=_precursors(results, rising=True),
        risk_down_precursors=_precursors(results, rising=False),
        sample_audit=samples,
        deterministic=deterministic,
        lookahead_safe=lookahead,
        score_immutable=score_immutable,
        periods=len(results),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _cohort(
    results: Sequence[Market15mInternalResult],
    lights: set[str],
) -> dict[str, float | int]:
    selected = [item for item in results if item.linked_60m_risk["risk_light"] in lights]
    classifications = Counter(
        state.classification.value
        for result in selected
        for state in result.index_internal_states
        if state.classification is not InternalClassification.UNAVAILABLE
    )
    total = sum(classifications.values())
    repair_periods = sum(item.market_internal_state.value == "REPAIR_BROADENING" for item in selected)
    return {
        "periods": len(selected),
        "valid_index_observations": total,
        "healthy_down_count": classifications["HEALTHY_DOWN"],
        "healthy_down_ratio": _ratio(classifications["HEALTHY_DOWN"], total),
        "late_weakening_count": classifications["LATE_WEAKENING"],
        "late_weakening_ratio": _ratio(classifications["LATE_WEAKENING"], total),
        "failed_repair_count": classifications["FAILED_REPAIR"],
        "failed_repair_ratio": _ratio(classifications["FAILED_REPAIR"], total),
        "repair_broadening_count": repair_periods,
        "repair_broadening_ratio": _ratio(repair_periods, len(selected)),
    }


def _precursors(
    results: Sequence[Market15mInternalResult],
    *,
    rising: bool,
) -> dict[str, float | int]:
    events = []
    for current, following in zip(results, results[1:]):
        delta = int(following.linked_60m_risk["risk_score"]) - int(current.linked_60m_risk["risk_score"])
        if (rising and delta > 0) or (not rising and delta < 0):
            events.append(current)
    if rising:
        keys = ("LATE_WEAKENING", "FAILED_REPAIR")
        market_key = "WEAKNESS_BROADENING"
        names = ("late_weakening", "failed_repair", "weakness_broadening")
    else:
        keys = ("LATE_REPAIR",)
        market_key = "REPAIR_BROADENING"
        names = ("late_repair", "repair_broadening")
    hits = {name: 0 for name in names}
    union = 0
    for result in events:
        present = {state.classification.value for state in result.index_internal_states}
        flags = [key in present for key in keys] + [result.market_internal_state.value == market_key]
        for name, flag in zip(names, flags):
            hits[name] += int(flag)
        union += int(any(flags))
    output: dict[str, float | int] = {"events": len(events), "union_hits": union, "hit_rate": _ratio(union, len(events))}
    for name in names:
        output[f"{name}_hits"] = hits[name]
        output[f"{name}_rate"] = _ratio(hits[name], len(events))
    return output
