"""Load and freeze the TASK_010 two-stock v0.1 rules."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from trend_monitor.errors import ErrorCategory, TrendMonitorError


@dataclass(frozen=True, slots=True)
class StockIntradayRiskRules:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "StockIntradayRiskRules":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw["rules_version"] != "stock_60m_risk_v0.1":
                raise ValueError("unexpected stock 60m rules version")
            if raw["internal_rules_version"] != "stock_15m_internal_v0.1":
                raise ValueError("unexpected stock 15m rules version")
            if raw["source_market_60m_rules_version"] != "market_60m_risk_v0.1":
                raise ValueError("market 60m linkage changed")
            if raw["source_market_15m_rules_version"] != "market_15m_internal_v0.1":
                raise ValueError("market 15m linkage changed")
            if set(raw["instruments"]) != {"stock.hengtong_optic", "stock.wus_printed_circuit"}:
                raise ValueError("v0.1 must contain exactly the two formal stocks")
            if raw["scoring_fields"] != ["close"]:
                raise ValueError("stock v0.1 score is Close-only")
            if set(raw["ignored_scoring_fields"]) != {"open", "high", "low", "volume", "turnover"}:
                raise ValueError("ignored scoring field contract changed")
            if int(raw["downside_shock"]["minimum_complete_trading_days"]) < 60:
                raise ValueError("shock baseline must have at least 60 complete days")
            if int(raw["relative_weakness"]["minimum_complete_trading_days"]) < 60:
                raise ValueError("relative baseline must have at least 60 complete days")
            covered = {}
            for item in raw["risk_lights"]:
                for score in range(int(item["min"]), int(item["max"]) + 1):
                    if score in covered:
                        raise ValueError("overlapping risk light range")
                    covered[score] = str(item["name"])
            if set(covered) != set(range(6)) or [covered[i] for i in range(6)] != [
                "GREEN", "YELLOW", "YELLOW", "ORANGE", "ORANGE", "RED"
            ]:
                raise ValueError("stock risk light thresholds changed")
            return cls(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, f"invalid stock risk rules: {path}") from exc

    @property
    def rules_version(self) -> str:
        return str(self.raw["rules_version"])

    @property
    def internal_rules_version(self) -> str:
        return str(self.raw["internal_rules_version"])

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(self.raw["instruments"])

    def identity(self, instrument_id: str) -> tuple[str, str]:
        value = self.raw["instruments"][instrument_id]
        return str(value["symbol"]), str(value["name"])

    def light(self, score: int) -> tuple[str, str]:
        for item in self.raw["risk_lights"]:
            if int(item["min"]) <= score <= int(item["max"]):
                return str(item["name"]), str(item["symbol"])
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, f"stock risk score outside 0..5: {score}")
