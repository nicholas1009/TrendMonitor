"""Service-only orchestration for Daily/60m/15m Risk Input bundles."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.quality import RiskFeatureContract
from trend_monitor.risk_input.assembler import RiskInputAssembler
from trend_monitor.schemas import (
    AnalysisPeriod,
    InstrumentRiskInputBundle,
    PreflightStatus,
    RiskInputDataStatus,
    ProviderDataResult,
)
from trend_monitor.services.market_data import MarketDataService


class RiskInputService:
    def __init__(
        self,
        market_data: MarketDataService,
        contract: RiskFeatureContract,
    ) -> None:
        self.market_data = market_data
        self.assembler = RiskInputAssembler(contract)

    def build_bundle(
        self,
        instrument_id: str,
        *,
        as_of: datetime,
        requested_provider: str,
        fallback_providers: tuple[str, ...] = (),
        minute_results: Mapping[str, ProviderDataResult] | None = None,
    ) -> InstrumentRiskInputBundle:
        if as_of.tzinfo is None:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "as_of must be timezone-aware")
        instrument = self.market_data.registry.get_instrument(instrument_id)
        local = as_of.astimezone(ZoneInfo("Asia/Shanghai"))
        start = int((local - timedelta(days=160)).timestamp() * 1000)
        end = int(local.timestamp() * 1000)
        # Data Source Contract v0.1 has not approved Hithink Daily as a
        # production substitute for canonical Longbridge Daily.  Keep the
        # generic MarketDataService fallback facility available to explicit
        # research/cross-validation callers, but block it at the formal Risk
        # Input boundary until a separate human approval closes every field
        # semantic (including turnover and missing/null behavior).
        daily_fallbacks = tuple(
            item
            for item in fallback_providers
            if not (
                requested_provider.lower() == "longbridge"
                and item.lower() == "hithink"
            )
        )
        hithink_daily_blocked = (
            requested_provider.lower() == "longbridge"
            and any(item.lower() == "hithink" for item in fallback_providers)
        )
        try:
            daily_result = self.market_data.get_daily(
                instrument_id,
                requested_provider,
                start=start,
                end=end,
                fallback_providers=daily_fallbacks,
            )
            daily = self.assembler.assemble_daily(
                daily_result, asset_type=instrument.asset_type, as_of=local
            )
        except TrendMonitorError as exc:
            reason = f"{exc.category.value}:{exc.message}"
            if hithink_daily_blocked:
                reason += ";HITHINK_DAILY_FALLBACK_BLOCKED_PENDING_CONTRACT_VALIDATION"
            daily = self.assembler.blocked(
                instrument_id=instrument_id,
                asset_type=instrument.asset_type,
                period=AnalysisPeriod.DAILY,
                as_of=local,
                requested_provider=requested_provider,
                reason=reason,
            )
        minute_inputs = {}
        for period, analysis_period in (("60m", AnalysisPeriod.MIN_60), ("15m", AnalysisPeriod.MIN_15)):
            try:
                # Risk Input is a latest/current-period snapshot, not a
                # historical-quality scan. Fetch two complete Source-Bar days
                # plus one bar so a partial current day and the latest complete
                # day are both available. Long-window quality is verified by
                # the dedicated convention/coverage scripts; an older bad
                # non-core field must not silently block an otherwise-valid
                # latest trading day.
                result = (minute_results or {}).get(period)
                if result is None:
                    count = 35 if period == "15m" else 11
                    result = self.market_data.get_bars(
                        instrument_id,
                        requested_provider,
                        period=period,
                        count=count,
                        fallback_providers=fallback_providers,
                    )
                elif result.metadata.instrument_id != instrument_id:
                    raise TrendMonitorError(
                        ErrorCategory.INVALID_DATA,
                        "preloaded minute result instrument mismatch",
                    )
                minute_inputs[period] = self.assembler.assemble_minute(
                    result,
                    asset_type=instrument.asset_type,
                    period=period,
                    as_of=local,
                )
            except TrendMonitorError as exc:
                minute_inputs[period] = self.assembler.blocked(
                    instrument_id=instrument_id,
                    asset_type=instrument.asset_type,
                    period=analysis_period,
                    as_of=local,
                    requested_provider=requested_provider,
                    reason=f"{exc.category.value}:{exc.message}",
                )
        inputs = (daily, minute_inputs["60m"], minute_inputs["15m"])
        blocked = [item.analysis_period.value for item in inputs if item.preflight_status is PreflightStatus.BLOCKED]
        degraded = [item.analysis_period.value for item in inputs if item.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION]
        if blocked:
            status = PreflightStatus.BLOCKED
            data_status = RiskInputDataStatus.DATA_INCOMPLETE
            reasons = tuple(f"blocked:{item}" for item in blocked)
        elif degraded:
            status = PreflightStatus.PASS_WITH_DEGRADATION
            data_status = RiskInputDataStatus.DEGRADED
            reasons = tuple(f"degraded:{item}" for item in degraded)
        else:
            status = PreflightStatus.PASS
            data_status = RiskInputDataStatus.VALID
            reasons = ()
        return InstrumentRiskInputBundle(
            instrument_id=instrument_id,
            asset_type=instrument.asset_type,
            as_of=local.isoformat(),
            daily=daily,
            risk_60m=minute_inputs["60m"],
            support_15m=minute_inputs["15m"],
            data_status=data_status,
            preflight_status=status,
            reasons=reasons,
        )
