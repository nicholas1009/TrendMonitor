"""Derived TrendMonitor System Bar schema with complete source lineage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from trend_monitor.schemas.quality import FieldQualityMap


class SourceQualityStatus(StrEnum):
    VALID = "VALID"
    SOURCE_BOUNDARY_QUIRK = "SOURCE_BOUNDARY_QUIRK"
    INVALID = "INVALID"


class SystemBarTransformation(StrEnum):
    DIRECT_NORMALIZED = "DIRECT_NORMALIZED"
    SOURCE_BOUNDARY_ENVELOPE = "SOURCE_BOUNDARY_ENVELOPE"
    MERGE_CLOSING_BUCKET = "MERGE_CLOSING_BUCKET"


class SystemBarQualityStatus(StrEnum):
    DIRECT_NORMALIZED = "DIRECT_NORMALIZED"
    MERGED_CLOSING_BUCKET = "MERGED_CLOSING_BUCKET"
    SOURCE_BOUNDARY_QUIRK = "SOURCE_BOUNDARY_QUIRK"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class SystemBar:
    """A derived bar; it is never represented as Provider Raw data."""

    instrument_id: str
    period: str
    system_start: int
    system_end: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float
    source_provider: str
    source_bar_ids: tuple[str, ...]
    source_raw_paths: tuple[str, ...]
    transformation: SystemBarTransformation
    quality_status: SystemBarQualityStatus
    field_quality: FieldQualityMap = field(default_factory=FieldQualityMap)

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_bar_ids"] = list(self.source_bar_ids)
        result["source_raw_paths"] = list(self.source_raw_paths)
        result["transformation"] = self.transformation.value
        result["quality_status"] = self.quality_status.value
        result["field_quality"] = self.field_quality.to_dict()
        return result
