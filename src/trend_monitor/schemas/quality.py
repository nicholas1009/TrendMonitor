"""Field-level quality states for safe downstream feature use."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class FieldQuality(StrEnum):
    TRUSTED = "TRUSTED"
    TRUSTED_WITH_TRANSFORMATION = "TRUSTED_WITH_TRANSFORMATION"
    APPROXIMATE = "APPROXIMATE"
    ADVISORY_ONLY = "ADVISORY_ONLY"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class MarketField(StrEnum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    TURNOVER = "turnover"


@dataclass(frozen=True, slots=True)
class FieldQualityMap:
    open: FieldQuality = FieldQuality.UNKNOWN
    high: FieldQuality = FieldQuality.UNKNOWN
    low: FieldQuality = FieldQuality.UNKNOWN
    close: FieldQuality = FieldQuality.UNKNOWN
    volume: FieldQuality = FieldQuality.UNKNOWN
    turnover: FieldQuality = FieldQuality.UNKNOWN

    def get(self, field: MarketField | str) -> FieldQuality:
        return getattr(self, MarketField(field).value)

    def to_dict(self) -> dict[str, str]:
        return {key: value.value for key, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "FieldQualityMap":
        expected = {field.value for field in MarketField}
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            raise ValueError(f"invalid field quality keys; missing={missing}; extra={extra}")
        return cls(**{key: FieldQuality(item) for key, item in value.items()})
