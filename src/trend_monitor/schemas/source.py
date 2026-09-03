"""Source lineage and provider-result metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trend_monitor.schemas.market import MarketRecord


class DataType(StrEnum):
    STATIC_INFO = "static_info"
    QUOTE = "quote"
    AUCTION = "auction"
    DAILY = "daily"
    KLINE_1M = "1m"
    KLINE_15M = "15m"
    KLINE_60M = "60m"
    TRADING_SESSION = "trading_session"

    @property
    def is_minute(self) -> bool:
        return self in {self.KLINE_1M, self.KLINE_15M, self.KLINE_60M}


@dataclass(frozen=True, slots=True)
class SourceTrace:
    provider: str
    provider_symbol: str
    raw_path: str
    fetched_at: str
    source_timestamp: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderResultMetadata:
    provider: str
    provider_symbol: str
    instrument_id: str
    fetched_at: str
    source_timestamp: int | None
    data_type: DataType
    mapping_type: str
    requested_provider: str
    actual_provider: str
    fallback_used: bool
    fallback_reason: str | None
    raw_path: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["data_type"] = self.data_type.value
        return result


@dataclass(frozen=True, slots=True)
class ProviderDataResult:
    raw: dict[str, Any]
    normalized: tuple[MarketRecord, ...]
    metadata: ProviderResultMetadata
