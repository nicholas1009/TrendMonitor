"""Load and validate the immutable Market 60m Risk v0.1 rule config."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from trend_monitor.errors import ErrorCategory, TrendMonitorError


EXPECTED_GROUPS = {"LARGE_CAP", "BROAD_MARKET", "MID_SMALL", "GROWTH"}


@dataclass(frozen=True, slots=True)
class Market60mRiskRules:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "Market60mRiskRules":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw["rules_version"] != "market_60m_risk_v0.1":
                raise ValueError("unexpected rules version")
            instruments = raw["instruments"]
            groups = raw["groups"]
            if len(instruments) != 8 or set(groups) != EXPECTED_GROUPS:
                raise ValueError("rules must define exactly eight indexes and four groups")
            grouped = [item for members in groups.values() for item in members]
            if len(grouped) != 8 or set(grouped) != set(instruments):
                raise ValueError("groups must partition the eight indexes")
            if any(len(members) != 2 for members in groups.values()):
                raise ValueError("each v0.1 group must contain exactly two indexes")
            if raw["scoring_fields"] != ["close"]:
                raise ValueError("v0.1 scoring may use Close only")
            if set(raw["ignored_fields"]) != {"open", "high", "low", "volume", "turnover"}:
                raise ValueError("v0.1 ignored field contract is incomplete")
            if int(raw["downside_shock"]["minimum_complete_trading_days"]) < 60:
                raise ValueError("shock history must cover at least 60 complete days")
            for key in ("breadth_points", "persistent_weakness_points"):
                cls._validate_ranges(raw[key], 0, 8)
            cls._validate_ranges(raw["downside_shock"]["points"], 0, 8)
            cls._validate_ranges(raw["risk_lights"], 0, 8, value_key="name")
            return cls(raw=raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"invalid Market 60m risk rules: {path}",
            ) from exc

    @staticmethod
    def _validate_ranges(
        ranges: list[dict[str, Any]], start: int, end: int, *, value_key: str = "points"
    ) -> None:
        covered = {}
        for item in ranges:
            value = item[value_key]
            for number in range(int(item["min"]), int(item["max"]) + 1):
                if number in covered:
                    raise ValueError("overlapping configured range")
                covered[number] = value
        if set(covered) != set(range(start, end + 1)):
            raise ValueError("configured ranges do not cover 0..8")

    @property
    def rules_version(self) -> str:
        return str(self.raw["rules_version"])

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(self.raw["instruments"])

    def instrument_name(self, instrument_id: str) -> str:
        return str(self.raw["instruments"][instrument_id]["name"])

    @property
    def groups(self) -> dict[str, tuple[str, ...]]:
        return {key: tuple(value) for key, value in self.raw["groups"].items()}

    @staticmethod
    def _points(ranges: list[dict[str, Any]], value: int) -> int:
        for item in ranges:
            if int(item["min"]) <= value <= int(item["max"]):
                return int(item["points"])
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, f"score input outside 0..8: {value}")

    def breadth_points(self, decliners: int) -> int:
        return self._points(self.raw["breadth_points"], decliners)

    def persistent_points(self, count: int) -> int:
        return self._points(self.raw["persistent_weakness_points"], count)

    def shock_points(self, count: int) -> int:
        return self._points(self.raw["downside_shock"]["points"], count)

    def light(self, score: int) -> tuple[str, str]:
        for item in self.raw["risk_lights"]:
            if int(item["min"]) <= score <= int(item["max"]):
                return str(item["name"]), str(item["symbol"])
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, f"risk score outside 0..8: {score}")
