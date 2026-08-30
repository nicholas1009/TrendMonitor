from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trend_monitor.schemas.source import SourceTrace


class AssetType(StrEnum):
    STOCK = "stock"
    INDEX = "index"
    SECTOR = "sector"
    ETF = "etf"


@dataclass(frozen=True, slots=True)
class MarketRecord:
    symbol: str
    name: str | None
    asset_type: AssetType
    timestamp: int | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    turnover: float | None
    source: str
    period: str
    source_trace: SourceTrace | None = None
    instrument_id: str | None = None
    previous_close: float | None = None
    trade_session: str | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["asset_type"] = self.asset_type.value
        return result
