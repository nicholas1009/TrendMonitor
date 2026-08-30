"""As-of-safe Stock Risk Input construction and TASK_010 replay studies."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
import json
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.risk_input import RiskInputAssembler
from trend_monitor.schemas import (
    InternalClassification,
    ProviderDataResult,
    RiskInput,
    Stock15mInternalResult,
    Stock60mRiskResult,
)
from trend_monitor.stock_risk.engine import Stock60mRiskEngine, StockReferenceObservation
from trend_monitor.stock_risk.internal import Stock15mInternalEngine
from trend_monitor.stock_risk.rules import StockIntradayRiskRules
from trend_monitor.validation import record_timestamp
from trend_monitor.validation.minute_structure import EXPECTED_TIMES


SHANGHAI = ZoneInfo("Asia/Shanghai")
PERIOD_ENDS = ("10:30", "11:30", "14:00", "15:00")
SOURCE_PREFIXES = {
    "10:30": ("09:30",),
    "11:30": ("09:30", "10:30"),
    "14:00": ("09:30", "10:30", "13:00"),
    "15:00": EXPECTED_TIMES["60m"],
}


@dataclass(frozen=True, slots=True)
class StockInputPeriod:
    as_of: datetime
    inputs: dict[str, RiskInput]
    source_ids: dict[str, str]


@dataclass(frozen=True, slots=True)
class StockReplayItem:
    stock_60m: Stock60mRiskResult
    stock_15m: Stock15mInternalResult

    def to_dict(self) -> dict[str, object]:
        return {"stock_60m": self.stock_60m.to_dict(), "stock_15m": self.stock_15m.to_dict()}


@dataclass(frozen=True, slots=True)
class StockReplayReport:
    results: dict[str, tuple[StockReplayItem, ...]]
    stats: dict[str, dict[str, Any]]
    risk_up_precursors: dict[str, dict[str, Any]]
    risk_down_precursors: dict[str, dict[str, Any]]
    market_resonance_study: dict[str, dict[str, Any]]
    sample_audit: dict[str, dict[str, list[dict[str, Any]]]]
    deterministic: bool
    lookahead_safe: bool
    score_immutable: bool
    periods: int
    observations: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "periods_per_stock": self.periods,
            "observations": self.observations,
            "stats": self.stats,
            "risk_up_precursors": self.risk_up_precursors,
            "risk_down_precursors": self.risk_down_precursors,
            "market_resonance_study": self.market_resonance_study,
            "sample_audit": self.sample_audit,
            "deterministic": self.deterministic,
            "lookahead_safe": self.lookahead_safe,
            "score_immutable": self.score_immutable,
            "results": {key: [item.to_dict() for item in values] for key, values in self.results.items()},
        }


class HistoricalStockRiskInputBuilder:
    def __init__(
        self,
        assembler: RiskInputAssembler,
        rules: StockIntradayRiskRules,
        asset_types: Mapping[str, object],
    ) -> None:
        self.assembler = assembler
        self.rules = rules
        self.asset_types = asset_types

    def build_60m(
        self,
        source_results: Mapping[str, ProviderDataResult],
        *,
        required_days: int = 82,
    ) -> tuple[StockInputPeriod, ...]:
        if set(source_results) != set(self.rules.instrument_ids):
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "stock 60m source coverage is incomplete")
        common = None
        grouped: dict[str, dict[str, list[Any]]] = {}
        for instrument_id, source in source_results.items():
            by_day: dict[str, list[Any]] = defaultdict(list)
            for item in source.normalized:
                by_day[record_timestamp(item).date().isoformat()].append(item)
            for items in by_day.values():
                items.sort(key=record_timestamp)
            complete = {
                day
                for day, items in by_day.items()
                if tuple(record_timestamp(item).strftime("%H:%M") for item in items) == EXPECTED_TIMES["60m"]
            }
            common = complete if common is None else common & complete
            grouped[instrument_id] = by_day
        complete_days = sorted(common or ())
        if len(complete_days) < required_days:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"stock replay requires {required_days} common complete days, got {len(complete_days)}",
            )
        selected_complete = complete_days[-required_days:]
        # Preserve valid earlier periods on a day whose Closing Bucket is
        # missing. Such a day is not a formal replay day or percentile day,
        # but its completed 10:30/11:30/14:00 closes remain the correct
        # adjacent as-of history for the following period.
        available_days = set(grouped[self.rules.instrument_ids[0]])
        for instrument_id in self.rules.instrument_ids[1:]:
            available_days &= set(grouped[instrument_id])
        days = sorted(day for day in available_days if selected_complete[0] <= day <= selected_complete[-1])
        periods = []
        for day_text in days:
            trading_day = date.fromisoformat(day_text)
            for end_label in PERIOD_ENDS:
                as_of = datetime.combine(trading_day, time.fromisoformat(end_label), tzinfo=SHANGHAI)
                inputs, source_ids = {}, {}
                allowed = set(SOURCE_PREFIXES[end_label])
                if any(
                    not allowed.issubset(
                        {record_timestamp(item).strftime("%H:%M") for item in grouped[instrument_id][day_text]}
                    )
                    for instrument_id in self.rules.instrument_ids
                ):
                    continue
                for instrument_id in self.rules.instrument_ids:
                    source = source_results[instrument_id]
                    selected = tuple(
                        item
                        for item in grouped[instrument_id][day_text]
                        if record_timestamp(item).strftime("%H:%M") in allowed
                    )
                    metadata = replace(
                        source.metadata,
                        source_timestamp=max(item.timestamp for item in selected if item.timestamp is not None),
                    )
                    risk_input = self.assembler.assemble_minute(
                        replace(source, normalized=selected, metadata=metadata),
                        asset_type=self.asset_types[instrument_id],
                        period="60m",
                        as_of=as_of,
                        trading_date=day_text,
                    )
                    inputs[instrument_id] = risk_input
                    source_ids[instrument_id] = f"{source.metadata.raw_path}#risk_input_as_of={as_of.isoformat()}"
                periods.append(StockInputPeriod(as_of, inputs, source_ids))
        return tuple(periods)

    def build_15m_at(
        self,
        source_results: Mapping[str, ProviderDataResult],
        *,
        trading_day: date,
        as_of: datetime,
    ) -> dict[str, RiskInput]:
        if set(source_results) != set(self.rules.instrument_ids):
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "stock 15m source coverage is incomplete")
        result = {}
        for instrument_id in self.rules.instrument_ids:
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
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, f"stock 15m source missing: {instrument_id}")
            metadata = replace(
                source.metadata,
                source_timestamp=max(item.timestamp for item in selected if item.timestamp is not None),
            )
            result[instrument_id] = self.assembler.assemble_minute(
                replace(source, normalized=selected, metadata=metadata),
                asset_type=self.asset_types[instrument_id],
                period="15m",
                as_of=as_of,
                trading_date=trading_day.isoformat(),
            )
        return result


def build_reference_observations(
    stock_periods: Sequence[StockInputPeriod],
    market_periods: Sequence[Mapping[str, Any]],
    *,
    rules: StockIntradayRiskRules,
) -> dict[str, tuple[StockReferenceObservation, ...]]:
    market_by_end = {str(item["period_end"]): item for item in market_periods}
    previous: dict[str, float] = {}
    observations: dict[str, list[StockReferenceObservation]] = defaultdict(list)
    for period in stock_periods:
        end = period.as_of.isoformat()
        market = market_by_end.get(end)
        market_median = market.get("market_median_return") if market else None
        for instrument_id in rules.instrument_ids:
            close = period.inputs[instrument_id].system_bars[-1].close
            prior = previous.get(instrument_id)
            if prior is not None and prior > 0:
                observations[instrument_id].append(
                    StockReferenceObservation(
                        instrument_id=instrument_id,
                        trading_date=period.as_of.date().isoformat(),
                        period_end=end,
                        close=close,
                        stock_return=close / prior - 1,
                        market_median_return=float(market_median) if market_median is not None else None,
                        source_stock_risk_input_id=period.source_ids[instrument_id],
                        source_market_60m_result_id=str(market.get("source_id")) if market else None,
                    )
                )
            previous[instrument_id] = close
    return {key: tuple(value) for key, value in observations.items()}


def run_stock_replay(
    stock_engine: Stock60mRiskEngine,
    internal_engine: Stock15mInternalEngine,
    *,
    stock_60m_periods: Sequence[StockInputPeriod],
    stock_15m_inputs: Mapping[str, Mapping[str, RiskInput]],
    references: Mapping[str, Sequence[StockReferenceObservation]],
    market_60m_results: Sequence[Mapping[str, Any]],
    market_15m_results: Sequence[Mapping[str, Any]],
    replay_days: int = 20,
) -> StockReplayReport:
    requested = replay_days * 4
    if len(market_60m_results) != requested or len(market_15m_results) != requested:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "stock replay requires frozen 80-period market results")
    market60 = {str(item["last_completed_bar_end"]): item for item in market_60m_results}
    market15 = {str(item["60m_period_end"]): item for item in market_15m_results}
    stock_periods = {period.as_of.isoformat(): period for period in stock_60m_periods}
    output: dict[str, list[StockReplayItem]] = defaultdict(list)
    deterministic = True
    lookahead = True
    immutable = True
    for instrument_id in stock_engine.rules.instrument_ids:
        prior_result = None
        all_refs = tuple(references[instrument_id])
        for market_result in market_60m_results:
            end_text = str(market_result["last_completed_bar_end"])
            period = stock_periods.get(end_text)
            if period is None:
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, f"stock 60m period missing: {end_text}")
            historical = tuple(item for item in all_refs if item.period_end < end_text)
            market_internal = market15[end_text]
            source60 = str(
                market_result.get("_source_context_id")
                or f"{market_result.get('_source_replay_id', 'market_60m_replay')}#period={end_text}"
            )
            source15 = str(
                market_internal.get("_source_context_id")
                or f"{market_internal.get('_source_replay_id', 'market_15m_replay')}#period={end_text}"
            )
            kwargs = {
                "history": historical,
                "market_60m_result": market_result,
                "market_15m_result": market_internal,
                "source_stock_risk_input_id": period.source_ids[instrument_id],
                "source_market_60m_result_id": source60,
                "source_market_15m_result_id": source15,
                "previous_result": prior_result,
            }
            frozen60 = json.dumps(deepcopy(market_result), ensure_ascii=False, sort_keys=True)
            frozen15 = json.dumps(deepcopy(market_internal), ensure_ascii=False, sort_keys=True)
            first = stock_engine.evaluate(period.inputs[instrument_id], **kwargs)
            second = stock_engine.evaluate(period.inputs[instrument_id], **kwargs)
            deterministic = deterministic and first.to_dict() == second.to_dict()
            current_15m = stock_15m_inputs[end_text][instrument_id]
            period_end = datetime.fromisoformat(end_text)
            period_start = period_end - timedelta(hours=1)
            prior_ref = historical[-1]
            internal_kwargs = {
                "as_of": period_end,
                "period_start": period_start,
                "period_end": period_end,
                "source_stock_risk_input_id": f"{current_15m.source_trace.raw_path}#risk_input_as_of={end_text}",
                "market_15m_result": market_internal,
                "source_market_15m_result_id": source15,
                "external_previous_close": (prior_ref.close, "TRUSTED"),
            }
            internal_first = internal_engine.evaluate(current_15m, **internal_kwargs)
            internal_second = internal_engine.evaluate(current_15m, **internal_kwargs)
            deterministic = deterministic and internal_first.to_dict() == internal_second.to_dict()
            immutable = immutable and frozen60 == json.dumps(market_result, ensure_ascii=False, sort_keys=True)
            immutable = immutable and frozen15 == json.dumps(market_internal, ensure_ascii=False, sort_keys=True)
            lookahead = lookahead and bool(first.data_quality.get("lookahead_safe")) and bool(
                internal_first.data_quality.get("lookahead_safe")
            )
            output[instrument_id].append(StockReplayItem(first, internal_first))
            prior_result = first

    finalized = {key: tuple(value) for key, value in output.items()}
    stats = {key: _stats(value) for key, value in finalized.items()}
    return StockReplayReport(
        results=finalized,
        stats=stats,
        risk_up_precursors={key: _precursors(value, rising=True) for key, value in finalized.items()},
        risk_down_precursors={key: _precursors(value, rising=False) for key, value in finalized.items()},
        market_resonance_study={key: _resonance(value) for key, value in finalized.items()},
        sample_audit={key: _samples(value) for key, value in finalized.items()},
        deterministic=deterministic,
        lookahead_safe=lookahead,
        score_immutable=immutable,
        periods=requested,
        observations=sum(len(value) for value in finalized.values()),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _stats(items: Sequence[StockReplayItem]) -> dict[str, Any]:
    lights = Counter(item.stock_60m.risk_light.value for item in items if item.stock_60m.risk_light)
    directions = Counter(item.stock_60m.risk_direction.value for item in items)
    classifications = Counter(item.stock_15m.classification.value for item in items)
    return {
        "risk_distribution": {key: lights[key] for key in ("GREEN", "YELLOW", "ORANGE", "RED")},
        "risk_up": directions["RISING"],
        "risk_down": directions["FALLING"],
        "persistent_weakness": sum(item.stock_60m.persistent_weakness for item in items),
        "downside_shock": sum(item.stock_60m.downside_shock for item in items),
        "relative_weakness": sum(item.stock_60m.relative_weakness for item in items),
        "market_resonance": sum(item.stock_60m.market_resonance for item in items),
        "independent_weakness": sum("STOCK_WEAK_MARKET_STABLE" in item.stock_60m.divergence_flags for item in items),
        "counter_market_strength": sum("STOCK_STRONG_MARKET_WEAK" in item.stock_60m.divergence_flags for item in items),
        "classification_distribution": {
            key: classifications[key]
            for key in ("HEALTHY_UP", "HEALTHY_DOWN", "LATE_REPAIR", "FAILED_REPAIR", "LATE_WEAKENING", "MIXED")
        },
    }


def _precursors(items: Sequence[StockReplayItem], *, rising: bool) -> dict[str, Any]:
    events = []
    for current, following in zip(items, items[1:]):
        delta = int(following.stock_60m.risk_score or 0) - int(current.stock_60m.risk_score or 0)
        if (rising and delta > 0) or (not rising and delta < 0):
            events.append(current.stock_15m)
    if rising:
        classifications = {"LATE_WEAKENING", "FAILED_REPAIR", "HEALTHY_DOWN"}
        joint_flags = {"JOINT_WEAKNESS"}
    else:
        classifications = {"LATE_REPAIR", "HEALTHY_UP"}
        joint_flags = {"JOINT_REPAIR", "STOCK_REPAIR_AGAINST_WEAK_MARKET"}
    classification_hits = sum(item.classification.value in classifications for item in events)
    joint_hits = sum(bool(set(item.joint_market_flags) & joint_flags) for item in events)
    union_hits = sum(
        item.classification.value in classifications or bool(set(item.joint_market_flags) & joint_flags)
        for item in events
    )
    return {
        "events": len(events),
        "classification_hits": classification_hits,
        "classification_hit_rate": _ratio(classification_hits, len(events)),
        "joint_flag_hits": joint_hits,
        "joint_flag_hit_rate": _ratio(joint_hits, len(events)),
        "union_hits": union_hits,
        "hit_rate": _ratio(union_hits, len(events)),
    }


def _resonance(items: Sequence[StockReplayItem]) -> dict[str, Any]:
    high = [item for item in items if item.stock_60m.risk_light and item.stock_60m.risk_light.value in {"ORANGE", "RED"}]
    market_lights = Counter(str(item.stock_60m.market_context.get("market_risk_light")) for item in high)
    resonant = sum(item.stock_60m.market_resonance for item in high)
    independent = sum("STOCK_WEAK_MARKET_STABLE" in item.stock_60m.divergence_flags for item in high)
    return {
        "stock_orange_red_periods": len(high),
        "market_light_distribution": {key: market_lights[key] for key in ("GREEN", "YELLOW", "ORANGE", "RED")},
        "market_resonance_count": resonant,
        "market_resonance_ratio": _ratio(resonant, len(high)),
        "independent_weakness_count": independent,
        "independent_weakness_ratio": _ratio(independent, len(high)),
    }


def _samples(items: Sequence[StockReplayItem]) -> dict[str, list[dict[str, Any]]]:
    keys = (
        "GREEN", "YELLOW", "ORANGE", "RED", "LATE_WEAKENING", "LATE_REPAIR",
        "FAILED_REPAIR", "JOINT_WEAKNESS", "INDEPENDENT_WEAKNESS",
    )
    samples = {key: [] for key in keys}
    for item in items:
        risk, internal = item.stock_60m, item.stock_15m
        candidates = []
        if risk.risk_light:
            candidates.append(risk.risk_light.value)
        candidates.append(internal.classification.value)
        if "JOINT_WEAKNESS" in internal.joint_market_flags:
            candidates.append("JOINT_WEAKNESS")
        if "STOCK_WEAK_MARKET_STABLE" in risk.divergence_flags:
            candidates.append("INDEPENDENT_WEAKNESS")
        sample = {
            "period_end": risk.period_end,
            "risk_score": risk.risk_score,
            "risk_light": risk.risk_light.value if risk.risk_light else None,
            "classification": internal.classification.value,
            "source_risk_input_id": risk.source_risk_input_id,
        }
        for key in candidates:
            if key in samples and len(samples[key]) < 3:
                samples[key].append(sample)
    return samples
