"""Small deterministic schemas for the instrument registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trend_monitor.schemas.market import AssetType


class MappingType(StrEnum):
    EXACT = "EXACT"
    PROXY = "PROXY"
    CANDIDATE_PROXY = "CANDIDATE_PROXY"
    UNMAPPED = "UNMAPPED"


class MappingConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class MappingStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CANDIDATE = "CANDIDATE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNMAPPED = "UNMAPPED"


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    display_name: str
    asset_type: AssetType
    market: str
    currency: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ProviderMapping:
    instrument_id: str
    provider: str
    provider_symbol: str | None
    provider_name: str | None
    mapping_type: MappingType
    confidence: MappingConfidence
    status: MappingStatus
    notes: str

    @classmethod
    def unmapped(cls, instrument_id: str, provider: str) -> "ProviderMapping":
        return cls(
            instrument_id=instrument_id,
            provider=provider.lower(),
            provider_symbol=None,
            provider_name=None,
            mapping_type=MappingType.UNMAPPED,
            confidence=MappingConfidence.UNKNOWN,
            status=MappingStatus.UNMAPPED,
            notes="No configured provider mapping; no symbol was guessed.",
        )
