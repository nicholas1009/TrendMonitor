"""Deterministic preflight gate before any future risk engine."""

from __future__ import annotations

from dataclasses import dataclass

from trend_monitor.schemas import (
    FeatureInput,
    PreflightStatus,
    RiskBar,
    RiskInputDataStatus,
)


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: PreflightStatus
    data_status: RiskInputDataStatus
    reasons: tuple[str, ...]


class PreflightGate:
    @staticmethod
    def evaluate_daily(bars: tuple[RiskBar, ...], *, errors: tuple[str, ...] = ()) -> PreflightResult:
        reasons = list(errors)
        if not bars:
            reasons.append("formal_daily_missing")
        for bar in bars:
            if bar.period != "1d" or bar.transformation != "DIRECT_DAILY":
                reasons.append("formal_daily_not_direct")
            if bar.close is None:
                reasons.append("current_period_close_missing")
            if not bar.source_raw_paths or not bar.source_bar_ids:
                reasons.append("lineage_missing")
        if reasons:
            return PreflightResult(
                PreflightStatus.BLOCKED,
                RiskInputDataStatus.DATA_INCOMPLETE,
                tuple(dict.fromkeys(reasons)),
            )
        return PreflightResult(PreflightStatus.PASS, RiskInputDataStatus.VALID, ())

    @staticmethod
    def evaluate_minute(
        bars: tuple[RiskBar, ...],
        *,
        expected_count: int,
        enabled: tuple[FeatureInput, ...],
        degraded: tuple[FeatureInput, ...],
        disabled: tuple[FeatureInput, ...],
        errors: tuple[str, ...] = (),
    ) -> PreflightResult:
        reasons = list(errors)
        if expected_count < 1:
            reasons.append("no_completed_period_expected")
        if len(bars) != expected_count:
            reasons.append(f"system_bar_count_incomplete:{len(bars)}/{expected_count}")
        starts = [item.start for item in bars]
        if starts != sorted(starts) or len(starts) != len(set(starts)):
            reasons.append("timestamp_invalid_or_duplicate")
        for bar in bars:
            if bar.completion_status != "COMPLETED" or bar.end <= bar.start:
                reasons.append("period_incomplete")
            if bar.close is None:
                reasons.append("current_period_close_missing")
            if bar.quality_status == "INVALID":
                reasons.append("current_core_bar_invalid")
            if not bar.source_raw_paths or not bar.source_bar_ids:
                reasons.append("lineage_missing")
        if not enabled:
            reasons.append("no_enabled_core_features")
        if reasons:
            return PreflightResult(
                PreflightStatus.BLOCKED,
                RiskInputDataStatus.DATA_INCOMPLETE,
                tuple(dict.fromkeys(reasons)),
            )
        if degraded or disabled:
            details = tuple(
                [f"degraded:{item.feature_name}" for item in degraded]
                + [f"disabled:{item.feature_name}" for item in disabled]
            )
            return PreflightResult(
                PreflightStatus.PASS_WITH_DEGRADATION,
                RiskInputDataStatus.DEGRADED,
                details,
            )
        return PreflightResult(PreflightStatus.PASS, RiskInputDataStatus.VALID, ())
