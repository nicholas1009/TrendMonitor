"""Assemble provider-independent, contract-gated Risk Input objects."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.quality import FeatureUsage, RiskFeatureContract, annotate_system_bar, evaluate_risk_input
from trend_monitor.risk_input.preflight import PreflightGate
from trend_monitor.schemas import (
    AnalysisPeriod,
    AssetType,
    DataType,
    FeatureEligibility,
    FeatureInput,
    FeatureLineage,
    FieldQuality,
    FieldQualityMap,
    MarketRecord,
    PreflightStatus,
    ProviderDataResult,
    RiskBar,
    RiskInput,
    RiskInputDataStatus,
    RiskSourceTrace,
    SystemBar,
)
from trend_monitor.transformation import (
    build_completed_system_bars,
    expected_completed_system_bar_count,
)
from trend_monitor.validation import record_timestamp, source_bar_id


SHANGHAI = ZoneInfo("Asia/Shanghai")
ASSEMBLED_FEATURES = (
    "current_period_close",
    "previous_period_close",
    "close_change",
    "close_change_pct",
    "consecutive_close_direction",
    "close_repair",
    "precise_high_low_break",
    "intraday_high_low_structure",
    "high_low_range_description",
    "stock_volume_context",
    "index_volume_signal",
    "turnover_context",
)


def _iso_epoch(epoch_ms: int | None) -> str | None:
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI).isoformat()


def _risk_source(result: ProviderDataResult) -> RiskSourceTrace:
    item = result.metadata
    return RiskSourceTrace(
        requested_provider=item.requested_provider,
        actual_provider=item.actual_provider,
        provider_symbol=item.provider_symbol,
        fallback_used=item.fallback_used,
        fallback_reason=item.fallback_reason,
        raw_path=item.raw_path,
        fetched_at=item.fetched_at,
        source_timestamp=item.source_timestamp,
    )


def _empty_source(requested_provider: str) -> RiskSourceTrace:
    return RiskSourceTrace(
        requested_provider=requested_provider,
        actual_provider="UNAVAILABLE",
        provider_symbol=None,
        fallback_used=False,
        fallback_reason=None,
        raw_path=None,
        fetched_at=None,
        source_timestamp=None,
    )


def _daily_bar(record: MarketRecord, result: ProviderDataResult) -> RiskBar:
    required = (record.timestamp, record.open, record.high, record.low, record.close, record.volume, record.turnover)
    if any(value is None for value in required) or record.source_trace is None:
        raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "DIRECT Daily record is incomplete")
    assert record.timestamp is not None and record.open is not None and record.high is not None
    assert record.low is not None and record.close is not None and record.volume is not None
    assert record.turnover is not None and record.source_trace is not None
    quality = FieldQualityMap(
        open=FieldQuality.TRUSTED,
        high=FieldQuality.TRUSTED,
        low=FieldQuality.TRUSTED,
        close=FieldQuality.TRUSTED,
        volume=FieldQuality.TRUSTED,
        turnover=FieldQuality.TRUSTED,
    )
    return RiskBar(
        instrument_id=record.instrument_id or result.metadata.instrument_id,
        period="1d",
        start=record.timestamp,
        end=record.timestamp,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        turnover=record.turnover,
        source_provider=record.source,
        provider_symbol=record.source_trace.provider_symbol,
        source_bar_ids=(source_bar_id(record),),
        source_raw_paths=(record.source_trace.raw_path,),
        fetched_at=record.source_trace.fetched_at,
        source_timestamp=record.source_trace.source_timestamp,
        transformation="DIRECT_DAILY",
        quality_status="DIRECT_NORMALIZED",
        field_quality=quality.to_dict(),
    )


def _system_risk_bar(bar: SystemBar, result: ProviderDataResult) -> RiskBar:
    return RiskBar(
        instrument_id=bar.instrument_id,
        period=bar.period,
        start=bar.system_start,
        end=bar.system_end,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        turnover=bar.turnover,
        source_provider=bar.source_provider,
        provider_symbol=result.metadata.provider_symbol,
        source_bar_ids=bar.source_bar_ids,
        source_raw_paths=bar.source_raw_paths,
        fetched_at=result.metadata.fetched_at,
        source_timestamp=result.metadata.source_timestamp,
        transformation=bar.transformation.value,
        quality_status=bar.quality_status.value,
        field_quality=bar.field_quality.to_dict(),
    )


def _lineage(bar: RiskBar) -> FeatureLineage:
    return FeatureLineage(
        period=bar.period,
        source_provider=bar.source_provider,
        provider_symbol=bar.provider_symbol,
        source_bar_ids=bar.source_bar_ids,
        source_raw_paths=bar.source_raw_paths,
        transformation=bar.transformation,
    )


def _consecutive_direction(bars: tuple[RiskBar, ...]) -> dict[str, object] | None:
    if len(bars) < 2:
        return None
    differences = [Decimal(str(right.close)) - Decimal(str(left.close)) for left, right in zip(bars, bars[1:])]
    last = differences[-1]
    direction = "UP" if last > 0 else "DOWN" if last < 0 else "FLAT"
    run = 1
    for value in reversed(differences[:-1]):
        current = "UP" if value > 0 else "DOWN" if value < 0 else "FLAT"
        if current != direction:
            break
        run += 1
    return {"direction": direction, "consecutive_transitions": run}


class RiskInputAssembler:
    def __init__(self, contract: RiskFeatureContract) -> None:
        self.contract = contract
        self.gate = PreflightGate()

    def blocked(
        self,
        *,
        instrument_id: str,
        asset_type: AssetType,
        period: AnalysisPeriod,
        as_of: datetime,
        requested_provider: str,
        reason: str,
        source_trace: RiskSourceTrace | None = None,
    ) -> RiskInput:
        trace = source_trace or _empty_source(requested_provider)
        return RiskInput(
            instrument_id=instrument_id,
            asset_type=asset_type,
            analysis_period=period,
            as_of=as_of.astimezone(SHANGHAI).isoformat(),
            trading_date=None,
            source_provider=(trace.actual_provider if trace.actual_provider != "UNAVAILABLE" else None),
            source_trace=trace,
            system_bars=(),
            feature_inputs=(),
            disabled_features=(),
            degraded_features=(),
            data_status=RiskInputDataStatus.DATA_INCOMPLETE,
            preflight_status=PreflightStatus.BLOCKED,
            last_completed_bar_end=None,
            data_fetched_at=None,
            layer_role=("formal_trend_and_trade_decision" if period is AnalysisPeriod.DAILY else "risk_input"),
            preflight_reasons=(reason,),
        )

    def assemble_daily(
        self,
        result: ProviderDataResult,
        *,
        asset_type: AssetType,
        as_of: datetime,
        source_kind: str = "DIRECT",
    ) -> RiskInput:
        if as_of.tzinfo is None:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of must be timezone-aware")
        if source_kind != "DIRECT" or result.metadata.data_type is not DataType.DAILY:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                "formal Daily rejects minute-derived or non-DIRECT input",
            )
        local_as_of = as_of.astimezone(SHANGHAI)
        eligible: list[MarketRecord] = []
        for record in result.normalized:
            if record.period != "1d":
                raise TrendMonitorError(ErrorCategory.INVALID_DATA, "formal Daily record period is not 1d")
            local = record_timestamp(record)
            if local.date() < local_as_of.date() or (
                local.date() == local_as_of.date() and local_as_of.time() >= time(15, 0)
            ):
                eligible.append(record)
        selected = sorted(eligible, key=lambda item: item.timestamp or 0)[-2:]
        bars = tuple(_daily_bar(item, result) for item in selected)
        gate = self.gate.evaluate_daily(bars)
        trading_date = record_timestamp(selected[-1]).date().isoformat() if selected else None
        return RiskInput(
            instrument_id=result.metadata.instrument_id,
            asset_type=asset_type,
            analysis_period=AnalysisPeriod.DAILY,
            as_of=local_as_of.isoformat(),
            trading_date=trading_date,
            source_provider=result.metadata.actual_provider,
            source_trace=_risk_source(result),
            system_bars=bars,
            feature_inputs=(),
            disabled_features=(),
            degraded_features=(),
            data_status=gate.data_status,
            preflight_status=gate.status,
            last_completed_bar_end=_iso_epoch(bars[-1].end) if bars else None,
            data_fetched_at=result.metadata.fetched_at,
            layer_role="formal_trend_and_trade_decision",
            preflight_reasons=gate.reasons,
        )

    def assemble_minute(
        self,
        result: ProviderDataResult,
        *,
        asset_type: AssetType,
        period: str,
        as_of: datetime,
        trading_date: str | None = None,
    ) -> RiskInput:
        if as_of.tzinfo is None:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of must be timezone-aware")
        data_type = DataType(period)
        if data_type not in {DataType.KLINE_15M, DataType.KLINE_60M} or result.metadata.data_type is not data_type:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "minute Risk Input period mismatch")
        grouped: dict[str, list[MarketRecord]] = defaultdict(list)
        for record in result.normalized:
            grouped[record_timestamp(record).date().isoformat()].append(record)
        candidates = sorted(grouped)
        if trading_date is not None:
            selected_day = trading_date
        else:
            current = as_of.astimezone(SHANGHAI).date().isoformat()
            selected_day = current if current in grouped else (candidates[-1] if candidates else "")
        if not selected_day or selected_day not in grouped:
            return self.blocked(
                instrument_id=result.metadata.instrument_id,
                asset_type=asset_type,
                period=AnalysisPeriod.MIN_15 if period == "15m" else AnalysisPeriod.MIN_60,
                as_of=as_of,
                requested_provider=result.metadata.requested_provider,
                reason="trading_day_unknown_or_unavailable",
                source_trace=_risk_source(result),
            )
        source_records = sorted(grouped[selected_day], key=lambda item: item.timestamp or 0)
        try:
            system = build_completed_system_bars(
                source_records,
                period=period,
                as_of=as_of,
                allowed_negative_fields=(
                    frozenset({"volume", "turnover"})
                    if asset_type is AssetType.INDEX
                    else frozenset()
                ),
            )
        except TrendMonitorError as exc:
            return self.blocked(
                instrument_id=result.metadata.instrument_id,
                asset_type=asset_type,
                period=AnalysisPeriod.MIN_15 if period == "15m" else AnalysisPeriod.MIN_60,
                as_of=as_of,
                requested_provider=result.metadata.requested_provider,
                reason=f"{exc.category.value}:{exc.message}",
                source_trace=_risk_source(result),
            )
        annotated: list[SystemBar] = []
        quality_reasons: list[str] = []
        source_field_issues = {
            source_bar_id(record): tuple(
                field
                for field in ("volume", "turnover")
                if getattr(record, field) is not None and getattr(record, field) < 0
            )
            for record in source_records
        }
        for bar in system:
            value, reasons = annotate_system_bar(bar, asset_type=asset_type, contract=self.contract)
            affected_fields = {
                field
                for bar_id in bar.source_bar_ids
                for field in source_field_issues.get(bar_id, ())
            }
            if affected_fields:
                values = value.field_quality.to_dict()
                for field in affected_fields:
                    values[field] = FieldQuality.BLOCKED.value
                value = replace(value, field_quality=FieldQualityMap.from_dict(values))
                reasons = (*reasons, *(
                    f"SOURCE_NEGATIVE_{field.upper()}" for field in sorted(affected_fields)
                ))
            annotated.append(value)
            quality_reasons.extend(reasons)
        bars = tuple(_system_risk_bar(item, result) for item in annotated)
        used_ids = {bar_id for item in bars for bar_id in item.source_bar_ids}
        in_progress = tuple(
            int(item.timestamp)
            for item in source_records
            if item.timestamp is not None and source_bar_id(item) not in used_ids
        )
        expected = expected_completed_system_bar_count(
            period=period,
            trading_day=record_timestamp(source_records[0]).date(),
            as_of=as_of,
        )
        enabled, degraded, disabled = self._features(
            annotated=tuple(annotated),
            bars=bars,
            asset_type=asset_type,
            quality_reasons=tuple(dict.fromkeys(quality_reasons)),
        )
        gate = self.gate.evaluate_minute(
            bars,
            expected_count=expected,
            enabled=enabled,
            degraded=degraded,
            disabled=disabled,
        )
        return RiskInput(
            instrument_id=result.metadata.instrument_id,
            asset_type=asset_type,
            analysis_period=AnalysisPeriod.MIN_15 if period == "15m" else AnalysisPeriod.MIN_60,
            as_of=as_of.astimezone(SHANGHAI).isoformat(),
            trading_date=selected_day,
            source_provider=result.metadata.actual_provider,
            source_trace=_risk_source(result),
            system_bars=bars,
            feature_inputs=enabled,
            disabled_features=disabled,
            degraded_features=degraded,
            data_status=gate.data_status,
            preflight_status=gate.status,
            last_completed_bar_end=_iso_epoch(bars[-1].end) if bars else None,
            data_fetched_at=result.metadata.fetched_at,
            layer_role=("risk_warning_and_detail_confirmation" if period == "60m" else "internal_structure_support_for_60m_only"),
            in_progress_source_bars=in_progress,
            preflight_reasons=gate.reasons,
        )

    def _features(
        self,
        *,
        annotated: tuple[SystemBar, ...],
        bars: tuple[RiskBar, ...],
        asset_type: AssetType,
        quality_reasons: tuple[str, ...],
    ) -> tuple[tuple[FeatureInput, ...], tuple[FeatureInput, ...], tuple[FeatureInput, ...]]:
        if not annotated or not bars:
            return (), (), ()
        assessment = evaluate_risk_input(
            annotated[-1],
            asset_type=asset_type,
            contract=self.contract,
            quality_reasons=quality_reasons,
        )
        decisions = {item.feature: item for item in assessment.features}
        all_inputs: list[FeatureInput] = []
        for name in ASSEMBLED_FEATURES:
            decision = decisions.get(name)
            if decision is None:
                continue
            value, contributors, field_source = self._feature_value(name, bars)
            if not decision.enabled or value is None:
                eligibility = FeatureEligibility.DISABLED
                reason = decision.reason if not decision.enabled else "INSUFFICIENT_COMPLETED_BARS"
            elif decision.usage is FeatureUsage.ADVISORY:
                eligibility = FeatureEligibility.DEGRADED
                reason = "CONTRACT_ADVISORY_ONLY"
            else:
                eligibility = FeatureEligibility.ENABLED
                reason = decision.reason
            all_inputs.append(
                FeatureInput(
                    feature_name=name,
                    value=value,
                    field_source=field_source,
                    quality=dict(decision.quality_status),
                    eligibility=eligibility,
                    reason=reason,
                    lineage=tuple(_lineage(item) for item in contributors),
                )
            )
        enabled = tuple(item for item in all_inputs if item.eligibility is FeatureEligibility.ENABLED)
        degraded = tuple(item for item in all_inputs if item.eligibility is FeatureEligibility.DEGRADED)
        disabled = tuple(item for item in all_inputs if item.eligibility is FeatureEligibility.DISABLED)
        return enabled, degraded, disabled

    @staticmethod
    def _feature_value(
        name: str, bars: tuple[RiskBar, ...]
    ) -> tuple[Any, tuple[RiskBar, ...], tuple[str, ...]]:
        current = bars[-1]
        previous = bars[-2] if len(bars) > 1 else None
        if name == "current_period_close":
            return current.close, (current,), ("current.close",)
        if name == "previous_period_close":
            return (previous.close, (previous,), ("previous.close",)) if previous else (None, (), ("previous.close",))
        if name in {"close_change", "close_change_pct", "close_repair"}:
            if previous is None:
                return None, (), ("current.close", "previous.close")
            difference = Decimal(str(current.close)) - Decimal(str(previous.close))
            if name == "close_change":
                value: Any = float(difference)
            elif name == "close_change_pct":
                value = None if previous.close == 0 else float(difference / Decimal(str(previous.close)))
            else:
                value = {"current_close": current.close, "previous_period_close": previous.close}
            return value, (previous, current), ("previous.close", "current.close")
        if name == "consecutive_close_direction":
            return _consecutive_direction(bars), bars, tuple(f"bar[{index}].close" for index in range(len(bars)))
        if name in {"precise_high_low_break", "intraday_high_low_structure", "high_low_range_description"}:
            return {"high": current.high, "low": current.low}, (current,), ("current.high", "current.low")
        if name in {"stock_volume_context", "index_volume_signal"}:
            return current.volume, (current,), ("current.volume",)
        if name == "turnover_context":
            return current.turnover, (current,), ("current.turnover",)
        return None, (), ()
