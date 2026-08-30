"""Load and validate Market 15m Internal v0.1 rules."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import InternalClassification


EXPECTED_COMPLETE = {
    "HEALTHY_UP",
    "HEALTHY_DOWN",
    "LATE_REPAIR",
    "FAILED_REPAIR",
    "LATE_WEAKENING",
    "MIXED",
}
EXPECTED_EARLY = {"EARLY_STRENGTH", "EARLY_WEAKNESS", "EARLY_MIXED"}


@dataclass(frozen=True, slots=True)
class Market15mInternalRules:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "Market15mInternalRules":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw["rules_version"] != "market_15m_internal_v0.1":
                raise ValueError("unexpected rules version")
            if raw["source_60m_rules_version"] != "market_60m_risk_v0.1":
                raise ValueError("unexpected source 60m rules version")
            if int(raw["completed_15m_bars"]) != 4:
                raise ValueError("a completed 60m period must contain four 15m bars")
            complete = {str(item) for item in raw["complete_classifications"]}
            early = {str(item) for item in raw["early_classifications"]}
            precedence = [str(item) for item in raw["classification_precedence"]]
            if complete != EXPECTED_COMPLETE or early != EXPECTED_EARLY:
                raise ValueError("classification set does not match v0.1")
            if set(precedence) != EXPECTED_COMPLETE or precedence[-1] != "MIXED":
                raise ValueError("classification precedence must cover v0.1 exactly")
            if set(raw["trusted_close_quality"]) != {"TRUSTED", "TRUSTED_WITH_TRANSFORMATION"}:
                raise ValueError("trusted Close contract changed")
            if int(raw["market_broadening_min"]) != 5 or int(raw["minimum_valid_indexes"]) != 6:
                raise ValueError("market coverage thresholds changed")
            for value in complete | early | {"UNAVAILABLE"}:
                InternalClassification(value)
            return cls(raw=raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"invalid Market 15m internal rules: {path}",
            ) from exc

    @property
    def rules_version(self) -> str:
        return str(self.raw["rules_version"])

    @property
    def source_60m_rules_version(self) -> str:
        return str(self.raw["source_60m_rules_version"])

    @property
    def complete_classifications(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.raw["complete_classifications"])

    @property
    def early_classifications(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.raw["early_classifications"])
