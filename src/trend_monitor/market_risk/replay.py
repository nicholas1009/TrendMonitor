"""As-of-safe historical Risk Input construction and deterministic replay."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from time import sleep
from typing import Mapping
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.market_risk.engine import Market60mRiskEngine
from trend_monitor.market_risk.rules import Market60mRiskRules
from trend_monitor.risk_input import RiskInputAssembler
from trend_monitor.schemas import Market60mRiskResult, PreflightStatus, ProviderDataResult, RiskInput
from trend_monitor.services import MarketDataService
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
class ReplayPeriod:
    as_of: datetime
    inputs: dict[str, RiskInput]
    source_snapshot_ids: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    results: tuple[Market60mRiskResult, ...]
    stats: dict[str, int]
    sample_audit: dict[str, list[dict[str, object]]]
    deterministic: bool
    lookahead_safe: bool
    complete_days: int
    periods: int

    def to_dict(self) -> dict[str, object]:
        return {
            "complete_days": self.complete_days,
            "periods": self.periods,
            "stats": dict(self.stats),
            "sample_audit": self.sample_audit,
            "deterministic": self.deterministic,
            "lookahead_safe": self.lookahead_safe,
            "results": [item.to_dict() for item in self.results],
        }


class HistoricalRiskInputBuilder:
    def __init__(
        self,
        market_data: MarketDataService,
        assembler: RiskInputAssembler,
        rules: Market60mRiskRules,
    ) -> None:
        self.market_data = market_data
        self.assembler = assembler
        self.rules = rules

    def build(
        self,
        *,
        start: date,
        end: date,
        required_days: int = 80,
        provider: str = "longbridge",
        source_results: Mapping[str, ProviderDataResult] | None = None,
    ) -> tuple[ReplayPeriod, ...]:
        resolved_results = dict(source_results or {})
        unexpected = set(resolved_results) - set(self.rules.instrument_ids)
        if unexpected:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"historical source results contain unexpected indexes: {sorted(unexpected)}",
            )
        requested = 0
        for instrument_id in self.rules.instrument_ids:
            if instrument_id in resolved_results:
                continue
            if requested:
                # Deliberately pace authenticated historical calls; this is a
                # bounded verification job, not a rate-limit stress test.
                sleep(0.75)
            resolved_results[instrument_id] = self._get_history_with_network_retry(
                instrument_id=instrument_id,
                provider=provider,
                start=start,
                end=end,
            )
            requested += 1
        complete_days = None
        grouped_results = {}
        for instrument_id in self.rules.instrument_ids:
            result = resolved_results[instrument_id]
            grouped: dict[str, list] = defaultdict(list)
            for record in result.normalized:
                grouped[record_timestamp(record).date().isoformat()].append(record)
            complete = {
                day
                for day, records in grouped.items()
                if tuple(record_timestamp(item).strftime("%H:%M") for item in records)
                == EXPECTED_TIMES["60m"]
            }
            complete_days = complete if complete_days is None else complete_days & complete
            grouped_results[instrument_id] = grouped
        common = sorted(complete_days or ())
        if len(common) < required_days:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"historical replay requires {required_days} common complete days, got {len(common)}",
            )
        selected = common[-required_days:]
        periods = []
        for day_text in selected:
            day = date.fromisoformat(day_text)
            for end_label in PERIOD_ENDS:
                as_of = datetime.combine(day, time.fromisoformat(end_label), tzinfo=SHANGHAI)
                inputs = {}
                source_ids = {}
                allowed = set(SOURCE_PREFIXES[end_label])
                for instrument_id in self.rules.instrument_ids:
                    result = resolved_results[instrument_id]
                    prefix = tuple(
                        item
                        for item in grouped_results[instrument_id][day_text]
                        if record_timestamp(item).strftime("%H:%M") in allowed
                    )
                    metadata = replace(
                        result.metadata,
                        source_timestamp=max(item.timestamp for item in prefix if item.timestamp is not None),
                    )
                    sliced = replace(result, normalized=prefix, metadata=metadata)
                    risk_input = self.assembler.assemble_minute(
                        sliced,
                        asset_type=self.market_data.registry.get_instrument(instrument_id).asset_type,
                        period="60m",
                        as_of=as_of,
                        trading_date=day_text,
                    )
                    if risk_input.preflight_status is PreflightStatus.BLOCKED:
                        raise TrendMonitorError(
                            ErrorCategory.DATA_INCOMPLETE,
                            f"historical Risk Input blocked: {instrument_id} {as_of.isoformat()}",
                        )
                    inputs[instrument_id] = risk_input
                    source_ids[instrument_id] = f"{result.metadata.raw_path}#as_of={as_of.isoformat()}"
                periods.append(ReplayPeriod(as_of, inputs, source_ids))
        return tuple(periods)

    def build_intraday_prefix(
        self,
        *,
        as_of: datetime,
        source_results: Mapping[str, ProviderDataResult],
    ) -> tuple[ReplayPeriod, ...]:
        """Rebuild today's completed periods from the same historical Raw snapshots.

        Complete-day replay remains unchanged. This method supplies only the
        current trading-day prefix so a live current result is compared with
        the same period rather than the prior complete day's 15:00 result.
        """
        if as_of.tzinfo is None:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of must be timezone-aware")
        local_as_of = as_of.astimezone(SHANGHAI)
        expected = set(self.rules.instrument_ids)
        if set(source_results) != expected:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                "intraday replay requires source results for every market index",
            )
        day_text = local_as_of.date().isoformat()
        periods = []
        for end_label in PERIOD_ENDS:
            period_as_of = datetime.combine(
                local_as_of.date(), time.fromisoformat(end_label), tzinfo=SHANGHAI
            )
            if period_as_of > local_as_of:
                break
            allowed = set(SOURCE_PREFIXES[end_label])
            inputs = {}
            source_ids = {}
            for instrument_id in self.rules.instrument_ids:
                result = source_results[instrument_id]
                prefix = tuple(
                    item
                    for item in result.normalized
                    if record_timestamp(item).date() == local_as_of.date()
                    and record_timestamp(item).strftime("%H:%M") in allowed
                )
                if not prefix:
                    raise TrendMonitorError(
                        ErrorCategory.DATA_INCOMPLETE,
                        f"intraday replay source prefix missing: {instrument_id} {period_as_of.isoformat()}",
                    )
                metadata = replace(
                    result.metadata,
                    source_timestamp=max(
                        item.timestamp for item in prefix if item.timestamp is not None
                    ),
                )
                sliced = replace(result, normalized=prefix, metadata=metadata)
                risk_input = self.assembler.assemble_minute(
                    sliced,
                    asset_type=self.market_data.registry.get_instrument(instrument_id).asset_type,
                    period="60m",
                    as_of=period_as_of,
                    trading_date=day_text,
                )
                if risk_input.preflight_status is PreflightStatus.BLOCKED:
                    raise TrendMonitorError(
                        ErrorCategory.DATA_INCOMPLETE,
                        f"intraday replay Risk Input blocked: {instrument_id} {period_as_of.isoformat()}",
                    )
                inputs[instrument_id] = risk_input
                source_ids[instrument_id] = (
                    f"{result.metadata.raw_path}#as_of={period_as_of.isoformat()}"
                )
            periods.append(ReplayPeriod(period_as_of, inputs, source_ids))
        return tuple(periods)

    def _get_history_with_network_retry(
        self,
        *,
        instrument_id: str,
        provider: str,
        start: date,
        end: date,
        attempts: int = 3,
    ) -> ProviderDataResult:
        """Retry only a fully classified transient provider network failure.

        MarketDataService deliberately wraps provider failures as DATA_INCOMPLETE.
        The structured failure_details are therefore inspected here; permission,
        mapping, validation and empty-data failures are never retried.
        """
        for attempt in range(1, attempts + 1):
            try:
                return self.market_data.get_history_bars(
                    instrument_id,
                    provider,
                    period="60m",
                    start=start,
                    end=end,
                )
            except TrendMonitorError as exc:
                details = exc.details.get("failure_details", ())
                transient = (
                    exc.category is ErrorCategory.DATA_INCOMPLETE
                    and bool(details)
                    and all(
                        isinstance(item, Mapping)
                        and item.get("category") == ErrorCategory.NETWORK_ERROR.value
                        for item in details
                    )
                )
                if not transient or attempt == attempts:
                    raise
                sleep(2.0 * attempt)
        raise AssertionError("unreachable")


def run_replay(
    engine: Market60mRiskEngine,
    periods: tuple[ReplayPeriod, ...],
    *,
    replay_days: int = 20,
) -> ReplayReport:
    requested_periods = replay_days * 4
    if len(periods) < requested_periods + 1:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "replay has no warm-up period")
    start_index = len(periods) - requested_periods
    history: dict[str, list[RiskInput]] = defaultdict(list)
    previous = None
    results = []
    deterministic = True
    lookahead_safe = True
    for index, period in enumerate(periods):
        if index < start_index - 1:
            for instrument_id, risk_input in period.inputs.items():
                history[instrument_id].append(risk_input)
            continue
        evaluated = engine.evaluate(
            period.inputs,
            history_inputs=history,
            source_snapshot_ids=period.source_snapshot_ids,
            previous_result=previous,
        )
        repeated = engine.evaluate(
            period.inputs,
            history_inputs=history,
            source_snapshot_ids=period.source_snapshot_ids,
            previous_result=previous,
        )
        deterministic = deterministic and evaluated.to_dict() == repeated.to_dict()
        boundary = int(period.as_of.timestamp() * 1000)
        lookahead_safe = lookahead_safe and all(
            bar.end <= boundary
            for risk_input in period.inputs.values()
            for bar in risk_input.system_bars
        )
        if index >= start_index:
            results.append(evaluated)
        previous = evaluated
        for instrument_id, risk_input in period.inputs.items():
            history[instrument_id].append(risk_input)

    lights = Counter(item.risk_light.value for item in results if item.risk_light)
    directions = Counter(item.risk_direction.value for item in results)
    stats = {
        "GREEN": lights["GREEN"],
        "YELLOW": lights["YELLOW"],
        "ORANGE": lights["ORANGE"],
        "RED": lights["RED"],
        "RISK_RISING": directions["RISING"],
        "RISK_FALLING": directions["FALLING"],
        "WEIGHTED_SUPPORT_DISTORTION": sum(item.weighted_support_distortion for item in results),
        "BROAD_SELLOFF_RESONANCE": sum(item.broad_selloff_resonance for item in results),
    }
    audit = {light: [] for light in ("GREEN", "YELLOW", "ORANGE", "RED")}
    for result in results:
        if result.risk_light is None:
            continue
        bucket = audit[result.risk_light.value]
        if len(bucket) < 3:
            bucket.append(
                {
                    "last_completed_bar_end": result.last_completed_bar_end,
                    "risk_score": result.risk_score,
                    "score_components": result.score_components,
                    "breadth": result.breadth,
                }
            )
    return ReplayReport(
        results=tuple(results),
        stats=stats,
        sample_audit=audit,
        deterministic=deterministic,
        lookahead_safe=lookahead_safe,
        complete_days=replay_days,
        periods=len(results),
    )
