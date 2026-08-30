"""Minimal provider boundary shared by business-facing data services."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from trend_monitor.registry.models import Instrument, ProviderMapping
from trend_monitor.schemas import AssetType, MarketRecord, SourceTrace


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, provider_symbol: str, asset_type: AssetType) -> dict[str, Any]: ...

    def get_daily(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        start: int,
        end: int,
    ) -> dict[str, Any]: ...

    def get_bars(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        period: str,
        count: int,
    ) -> dict[str, Any]: ...

    def get_history_bars(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        period: str,
        start: date,
        end: date,
    ) -> dict[str, Any]: ...

    def normalize_quote(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
    ) -> list[MarketRecord]: ...

    def normalize_daily(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
    ) -> list[MarketRecord]: ...

    def normalize_bars(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
        *,
        period: str,
    ) -> list[MarketRecord]: ...
