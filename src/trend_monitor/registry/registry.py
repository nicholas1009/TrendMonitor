"""Load and resolve the human-reviewable instrument registry."""

from __future__ import annotations

import json
from pathlib import Path

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.registry.models import (
    Instrument,
    MappingConfidence,
    MappingStatus,
    MappingType,
    ProviderMapping,
)
from trend_monitor.schemas.market import AssetType


class InstrumentRegistry:
    def __init__(
        self,
        instruments: dict[str, Instrument],
        mappings: dict[tuple[str, str], ProviderMapping],
    ) -> None:
        self._instruments = instruments
        self._mappings = mappings

    @classmethod
    def load(cls, path: str | Path) -> "InstrumentRegistry":
        registry_path = Path(path)
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"Unable to load instrument registry: {registry_path}",
            ) from exc

        try:
            raw_instruments = payload["instruments"]
            raw_mappings = payload["mappings"]
            if not isinstance(raw_instruments, list) or not isinstance(raw_mappings, list):
                raise TypeError("instruments and mappings must be arrays")

            instruments: dict[str, Instrument] = {}
            for item in raw_instruments:
                instrument = Instrument(
                    instrument_id=str(item["instrument_id"]),
                    display_name=str(item["display_name"]),
                    asset_type=AssetType(str(item["asset_type"])),
                    market=str(item["market"]),
                    currency=str(item["currency"]),
                    enabled=bool(item["enabled"]),
                )
                if instrument.instrument_id in instruments:
                    raise ValueError(f"duplicate instrument_id: {instrument.instrument_id}")
                instruments[instrument.instrument_id] = instrument

            mappings: dict[tuple[str, str], ProviderMapping] = {}
            for item in raw_mappings:
                instrument_id = str(item["instrument_id"])
                provider = str(item["provider"]).lower()
                if instrument_id not in instruments:
                    raise ValueError(f"mapping references unknown instrument: {instrument_id}")
                mapping = ProviderMapping(
                    instrument_id=instrument_id,
                    provider=provider,
                    provider_symbol=str(item["provider_symbol"]),
                    provider_name=str(item["provider_name"]),
                    mapping_type=MappingType(str(item["mapping_type"])),
                    confidence=MappingConfidence(str(item["confidence"])),
                    status=MappingStatus(str(item["status"])),
                    notes=str(item.get("notes", "")),
                )
                key = (instrument_id, provider)
                if key in mappings:
                    raise ValueError(f"duplicate provider mapping: {instrument_id}/{provider}")
                if mapping.mapping_type is MappingType.UNMAPPED:
                    raise ValueError("UNMAPPED is returned by the resolver, not stored as a symbol")
                if (
                    mapping.mapping_type is MappingType.CANDIDATE_PROXY
                    and mapping.confidence is MappingConfidence.HIGH
                ):
                    raise ValueError("CANDIDATE_PROXY cannot have HIGH confidence")
                mappings[key] = mapping
        except (KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"Invalid instrument registry: {exc}",
            ) from exc

        return cls(instruments, mappings)

    @property
    def instruments(self) -> tuple[Instrument, ...]:
        return tuple(self._instruments.values())

    @property
    def mappings(self) -> tuple[ProviderMapping, ...]:
        return tuple(self._mappings.values())

    def get_instrument(self, instrument_id: str) -> Instrument:
        try:
            return self._instruments[instrument_id]
        except KeyError as exc:
            raise TrendMonitorError(
                ErrorCategory.UNMAPPED,
                f"Unknown internal instrument: {instrument_id}",
            ) from exc

    def resolve(self, instrument_id: str, provider: str) -> ProviderMapping:
        self.get_instrument(instrument_id)
        normalized_provider = provider.lower()
        return self._mappings.get(
            (instrument_id, normalized_provider),
            ProviderMapping.unmapped(instrument_id, normalized_provider),
        )
