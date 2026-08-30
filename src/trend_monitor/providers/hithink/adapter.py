"""Adapter from the TASK_001 Hithink client to the common provider interface."""

from __future__ import annotations

from typing import Any

from trend_monitor.normalization import normalize_historical, normalize_snapshot
from trend_monitor.providers.hithink.errors import ErrorCategory, HithinkProviderError
from trend_monitor.providers.hithink.provider import HithinkProvider
from trend_monitor.registry.models import Instrument, ProviderMapping
from trend_monitor.schemas import AssetType, MarketRecord, SourceTrace
from trend_monitor.validation import validate_records


class HithinkMarketDataAdapter:
    name = "hithink"

    def __init__(self, provider: HithinkProvider) -> None:
        self.provider = provider

    def get_quote(self, provider_symbol: str, asset_type: AssetType) -> dict[str, Any]:
        if asset_type is AssetType.STOCK:
            return self.provider.stock_snapshot([provider_symbol])
        if asset_type in {AssetType.INDEX, AssetType.SECTOR}:
            return self.provider.index_snapshot([provider_symbol])
        if asset_type is AssetType.ETF:
            return self.provider.fund_snapshot(provider_symbol)
        raise HithinkProviderError(
            ErrorCategory.UNSUPPORTED,
            f"unsupported asset_type for quote: {asset_type}",
        )

    def get_daily(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        if asset_type is AssetType.STOCK:
            return self.provider.stock_history(
                provider_symbol, start=start, end=end, interval="1d", adjust="none"
            )
        if asset_type in {AssetType.INDEX, AssetType.SECTOR}:
            return self.provider.index_history(
                provider_symbol, start=start, end=end, interval="1d"
            )
        if asset_type is AssetType.ETF:
            return self.provider.fund_history(
                provider_symbol, start=start, end=end, interval="1d"
            )
        raise HithinkProviderError(
            ErrorCategory.UNSUPPORTED,
            f"unsupported asset_type for daily: {asset_type}",
        )

    def get_bars(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        period: str,
        count: int,
    ) -> dict[str, Any]:
        del provider_symbol, asset_type, count
        raise HithinkProviderError(
            ErrorCategory.UNSUPPORTED,
            f"TASK_001 verified Hithink direct {period} kline is unsupported",
        )

    def normalize_quote(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
    ) -> list[MarketRecord]:
        assert mapping.provider_symbol is not None
        records = normalize_snapshot(
            raw,
            asset_type=instrument.asset_type,
            names={mapping.provider_symbol: mapping.provider_name or instrument.display_name},
            source_trace=source_trace,
            instrument_id=instrument.instrument_id,
        )
        validate_records(records)
        return records

    def normalize_daily(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
    ) -> list[MarketRecord]:
        assert mapping.provider_symbol is not None
        records = normalize_historical(
            raw,
            symbol=mapping.provider_symbol,
            name=mapping.provider_name or instrument.display_name,
            asset_type=instrument.asset_type,
            period="1d",
            source_trace=source_trace,
            instrument_id=instrument.instrument_id,
        )
        validate_records(records)
        return records

    def normalize_bars(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
        *,
        period: str,
    ) -> list[MarketRecord]:
        del raw, instrument, mapping, source_trace
        raise HithinkProviderError(
            ErrorCategory.UNSUPPORTED,
            f"TASK_001 verified Hithink direct {period} kline is unsupported",
        )
