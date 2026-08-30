"""Adapter from Longbridge raw responses to the common provider interface."""

from __future__ import annotations

from datetime import date
from typing import Any

from trend_monitor.errors import ErrorCategory
from trend_monitor.normalization.longbridge import (
    normalize_longbridge_candlesticks,
    normalize_longbridge_quote,
)
from trend_monitor.providers.longbridge.errors import LongbridgeProviderError
from trend_monitor.providers.longbridge.provider import LongbridgeProvider
from trend_monitor.registry.models import Instrument, ProviderMapping
from trend_monitor.schemas import AssetType, MarketRecord, SourceTrace
from trend_monitor.validation import validate_common_records, validate_source_minute_records


class LongbridgeMarketDataAdapter:
    name = "longbridge"

    def __init__(self, provider: LongbridgeProvider) -> None:
        self.provider = provider

    @staticmethod
    def _check_asset_type(asset_type: AssetType) -> None:
        if asset_type is AssetType.SECTOR:
            raise LongbridgeProviderError(
                ErrorCategory.UNSUPPORTED,
                "Longbridge sector identity/capability is not verified",
            )

    def get_quote(self, provider_symbol: str, asset_type: AssetType) -> dict[str, Any]:
        self._check_asset_type(asset_type)
        return self.provider.get_quote(provider_symbol)

    def get_daily(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        start: int,
        end: int,
    ) -> dict[str, Any]:
        self._check_asset_type(asset_type)
        return self.provider.get_daily(provider_symbol, start=start, end=end)

    def get_bars(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        period: str,
        count: int,
    ) -> dict[str, Any]:
        self._check_asset_type(asset_type)
        return self.provider.get_candlesticks(provider_symbol, period=period, count=count)

    def get_history_bars(
        self,
        provider_symbol: str,
        asset_type: AssetType,
        *,
        period: str,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        self._check_asset_type(asset_type)
        return self.provider.get_history_candlesticks(
            provider_symbol,
            period=period,
            start=start,
            end=end,
        )

    def normalize_quote(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
    ) -> list[MarketRecord]:
        records = normalize_longbridge_quote(
            raw,
            instrument_id=instrument.instrument_id,
            name=mapping.provider_name or instrument.display_name,
            asset_type=instrument.asset_type,
            source_trace=source_trace,
        )
        validate_common_records(records)
        return records

    def normalize_daily(
        self,
        raw: dict[str, Any],
        instrument: Instrument,
        mapping: ProviderMapping,
        source_trace: SourceTrace,
    ) -> list[MarketRecord]:
        assert mapping.provider_symbol is not None
        records = normalize_longbridge_candlesticks(
            raw,
            instrument_id=instrument.instrument_id,
            symbol=mapping.provider_symbol,
            name=mapping.provider_name or instrument.display_name,
            asset_type=instrument.asset_type,
            period="1d",
            source_trace=source_trace,
        )
        validate_common_records(records, require_strict_time_order=True)
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
        assert mapping.provider_symbol is not None
        records = normalize_longbridge_candlesticks(
            raw,
            instrument_id=instrument.instrument_id,
            symbol=mapping.provider_symbol,
            name=mapping.provider_name or instrument.display_name,
            asset_type=instrument.asset_type,
            period=period,
            source_trace=source_trace,
        )
        # Longbridge A-share opening buckets have a separately evidenced source
        # boundary quirk. This validator permits only that narrow 09:30 case;
        # every non-boundary OHLC violation remains INVALID_DATA.
        validate_source_minute_records(
            records,
            allowed_negative_fields=(
                frozenset({"volume", "turnover"})
                if instrument.asset_type is AssetType.INDEX
                else frozenset()
            ),
        )
        return records
