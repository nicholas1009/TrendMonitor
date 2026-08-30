"""Registry-driven retrieval with explicit, traceable provider fallback."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Iterable

from trend_monitor.cache import CacheEntry, CacheStatus, RawCache
from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.providers.base import MarketDataProvider
from trend_monitor.registry import InstrumentRegistry, MappingStatus, MappingType
from trend_monitor.schemas import (
    DataType,
    ProviderDataResult,
    ProviderResultMetadata,
    SourceTrace,
)


def _source_timestamp(raw: dict[str, object]) -> int | None:
    data = raw.get("data")
    if not isinstance(data, dict):
        return None
    timestamp = data.get("timestamp")
    if isinstance(timestamp, (int, float)):
        epoch = int(timestamp)
        return epoch * 1000 if epoch < 10_000_000_000 else epoch
    items = data.get("item")
    if not isinstance(items, list):
        return None
    dates = [
        int(item.get("date_ms", item.get("timestamp")))
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("date_ms", item.get("timestamp")), (int, float))
    ]
    if not dates:
        return None
    latest = max(dates)
    return latest * 1000 if latest < 10_000_000_000 else latest


class MarketDataService:
    def __init__(
        self,
        registry: InstrumentRegistry,
        providers: Iterable[MarketDataProvider],
        cache: RawCache,
    ) -> None:
        self.registry = registry
        self.providers = {provider.name.lower(): provider for provider in providers}
        self.cache = cache

    def get_quote(
        self,
        instrument_id: str,
        requested_provider: str,
        *,
        fallback_providers: Iterable[str] = (),
    ) -> ProviderDataResult:
        return self._get(
            instrument_id,
            requested_provider,
            fallback_providers=fallback_providers,
            data_type=DataType.QUOTE,
        )

    def get_daily(
        self,
        instrument_id: str,
        requested_provider: str,
        *,
        start: int,
        end: int,
        fallback_providers: Iterable[str] = (),
    ) -> ProviderDataResult:
        return self._get(
            instrument_id,
            requested_provider,
            fallback_providers=fallback_providers,
            data_type=DataType.DAILY,
            start=start,
            end=end,
        )

    def get_bars(
        self,
        instrument_id: str,
        requested_provider: str,
        *,
        period: str,
        count: int = 100,
        fallback_providers: Iterable[str] = (),
    ) -> ProviderDataResult:
        try:
            data_type = DataType(period)
        except ValueError as exc:
            raise TrendMonitorError(
                ErrorCategory.UNSUPPORTED,
                f"unsupported direct bar period: {period}",
            ) from exc
        if not data_type.is_minute:
            raise TrendMonitorError(
                ErrorCategory.UNSUPPORTED,
                f"get_bars only accepts minute periods, got: {period}",
            )
        if count < 1 or count > 1000:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                "bar count must be between 1 and 1000",
            )
        return self._get(
            instrument_id,
            requested_provider,
            fallback_providers=fallback_providers,
            data_type=data_type,
            count=count,
        )

    def get_history_bars(
        self,
        instrument_id: str,
        requested_provider: str,
        *,
        period: str,
        start: date,
        end: date,
        fallback_providers: Iterable[str] = (),
    ) -> ProviderDataResult:
        try:
            data_type = DataType(period)
        except ValueError as exc:
            raise TrendMonitorError(
                ErrorCategory.UNSUPPORTED,
                f"unsupported historical bar period: {period}",
            ) from exc
        if not data_type.is_minute:
            raise TrendMonitorError(
                ErrorCategory.UNSUPPORTED,
                f"get_history_bars only accepts minute periods, got: {period}",
            )
        if start > end:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                "history start date is after end date",
            )
        return self._get(
            instrument_id,
            requested_provider,
            fallback_providers=fallback_providers,
            data_type=data_type,
            history_start=start,
            history_end=end,
        )

    def load_cached(self, entry: CacheEntry) -> ProviderDataResult:
        """Revalidate cached Raw through the registered adapter without a Provider request."""
        if entry.status is CacheStatus.INVALID:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "cached entry is marked INVALID")
        instrument = self.registry.get_instrument(entry.instrument_id)
        mapping = self.registry.resolve(entry.instrument_id, entry.provider)
        provider = self.providers.get(entry.provider)
        if (
            provider is None
            or mapping.provider_symbol is None
            or mapping.provider_symbol != entry.provider_symbol
        ):
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "cached entry mapping/provider mismatch")
        raw = self.cache.load(entry)
        trace = SourceTrace(
            provider=entry.provider,
            provider_symbol=entry.provider_symbol,
            raw_path=entry.path,
            fetched_at=entry.fetched_at,
            source_timestamp=entry.source_timestamp,
        )
        if entry.data_type is DataType.QUOTE:
            normalized = provider.normalize_quote(raw, instrument, mapping, trace)
        elif entry.data_type is DataType.DAILY:
            normalized = provider.normalize_daily(raw, instrument, mapping, trace)
        elif entry.data_type.is_minute:
            normalized = provider.normalize_bars(
                raw,
                instrument,
                mapping,
                trace,
                period=entry.data_type.value,
            )
        else:
            raise TrendMonitorError(ErrorCategory.UNSUPPORTED, "cached data type is not normalizable")
        return ProviderDataResult(
            raw=raw,
            normalized=tuple(normalized),
            metadata=ProviderResultMetadata(
                provider=entry.provider,
                provider_symbol=entry.provider_symbol,
                instrument_id=entry.instrument_id,
                fetched_at=entry.fetched_at,
                source_timestamp=entry.source_timestamp,
                data_type=entry.data_type,
                mapping_type=mapping.mapping_type.value,
                requested_provider=entry.provider,
                actual_provider=entry.provider,
                fallback_used=False,
                fallback_reason=None,
                raw_path=entry.path,
            ),
        )

    def _get(
        self,
        instrument_id: str,
        requested_provider: str,
        *,
        fallback_providers: Iterable[str],
        data_type: DataType,
        start: int | None = None,
        end: int | None = None,
        count: int | None = None,
        history_start: date | None = None,
        history_end: date | None = None,
    ) -> ProviderDataResult:
        requested = requested_provider.lower()
        candidates = tuple(dict.fromkeys([requested, *(item.lower() for item in fallback_providers)]))
        instrument = self.registry.get_instrument(instrument_id)
        failures: list[str] = []
        failure_details: list[dict[str, object]] = []

        for candidate in candidates:
            mapping = self.registry.resolve(instrument_id, candidate)
            if mapping.mapping_type is MappingType.UNMAPPED or mapping.provider_symbol is None:
                failures.append(f"{candidate}:{ErrorCategory.UNMAPPED.value}")
                continue
            if mapping.status is MappingStatus.NOT_CONFIGURED:
                failures.append(f"{candidate}:NOT_CONFIGURED")
                continue
            provider = self.providers.get(candidate)
            if provider is None:
                failures.append(f"{candidate}:NOT_CONFIGURED")
                continue

            try:
                if data_type is DataType.QUOTE:
                    raw = provider.get_quote(mapping.provider_symbol, instrument.asset_type)
                elif data_type is DataType.DAILY:
                    assert start is not None and end is not None
                    raw = provider.get_daily(
                        mapping.provider_symbol,
                        instrument.asset_type,
                        start=start,
                        end=end,
                    )
                elif history_start is not None and history_end is not None:
                    operation = getattr(provider, "get_history_bars", None)
                    if operation is None:
                        raise TrendMonitorError(
                            ErrorCategory.UNSUPPORTED,
                            f"{candidate} has no historical minute adapter",
                        )
                    raw = operation(
                        mapping.provider_symbol,
                        instrument.asset_type,
                        period=data_type.value,
                        start=history_start,
                        end=history_end,
                    )
                else:
                    assert count is not None
                    raw = provider.get_bars(
                        mapping.provider_symbol,
                        instrument.asset_type,
                        period=data_type.value,
                        count=count,
                    )
            except TrendMonitorError as exc:
                failures.append(f"{candidate}:{exc.category.value}")
                detail: dict[str, object] = {
                    "provider": candidate,
                    "category": exc.category.value,
                    "message": exc.message,
                }
                provider_code = getattr(exc, "provider_code", None)
                if provider_code is not None:
                    detail["provider_code"] = provider_code
                if exc.details:
                    detail["provider_details"] = dict(exc.details)
                failure_details.append(detail)
                continue

            fetched = datetime.now(timezone.utc)
            source_timestamp = _source_timestamp(raw)
            cache_entry = self.cache.save(
                instrument_id=instrument_id,
                provider=candidate,
                provider_symbol=mapping.provider_symbol,
                data_type=data_type,
                raw=raw,
                fetched_at=fetched,
                source_timestamp=source_timestamp,
                request_start=(
                    int(datetime.combine(history_start, time.min, tzinfo=timezone.utc).timestamp() * 1000)
                    if history_start is not None
                    else start
                ),
                request_end=(
                    int(datetime.combine(history_end, time.max, tzinfo=timezone.utc).timestamp() * 1000)
                    if history_end is not None
                    else end
                ),
            )
            trace = SourceTrace(
                provider=candidate,
                provider_symbol=mapping.provider_symbol,
                raw_path=cache_entry.path,
                fetched_at=cache_entry.fetched_at,
                source_timestamp=source_timestamp,
            )
            if data_type is DataType.QUOTE:
                normalize = provider.normalize_quote
                normalize_kwargs = {}
            elif data_type is DataType.DAILY:
                normalize = provider.normalize_daily
                normalize_kwargs = {}
            else:
                normalize = provider.normalize_bars
                normalize_kwargs = {"period": data_type.value}
            try:
                normalized = normalize(raw, instrument, mapping, trace, **normalize_kwargs)
            except TrendMonitorError as exc:
                self.cache.record_status(cache_entry, CacheStatus.INVALID)
                failures.append(f"{candidate}:{exc.category.value}")
                failure_details.append(
                    {
                        "provider": candidate,
                        "category": exc.category.value,
                        "message": exc.message,
                        "stage": "normalization_or_validation",
                        "raw_path": cache_entry.path,
                        "provider_symbol": mapping.provider_symbol,
                        "fetched_at": cache_entry.fetched_at,
                        "source_timestamp": source_timestamp,
                    }
                )
                continue

            fallback_used = candidate != requested
            fallback_reason = "; ".join(failures) if fallback_used else None
            metadata = ProviderResultMetadata(
                provider=candidate,
                provider_symbol=mapping.provider_symbol,
                instrument_id=instrument_id,
                fetched_at=cache_entry.fetched_at,
                source_timestamp=source_timestamp,
                data_type=data_type,
                mapping_type=mapping.mapping_type.value,
                requested_provider=requested,
                actual_provider=candidate,
                fallback_used=fallback_used,
                fallback_reason=fallback_reason,
                raw_path=cache_entry.path,
            )
            return ProviderDataResult(
                raw=raw,
                normalized=tuple(normalized),
                metadata=metadata,
            )

        raise TrendMonitorError(
            ErrorCategory.DATA_INCOMPLETE,
            f"No provider returned {data_type.value} for {instrument_id}",
            details={
                "requested_provider": requested,
                "failures": tuple(failures),
                "failure_details": tuple(failure_details),
            },
        )
