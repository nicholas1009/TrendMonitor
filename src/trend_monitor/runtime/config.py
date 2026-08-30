"""Validated runtime configuration and frozen-rule guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class RuntimeConfig:
    def __init__(self, raw: dict[str, Any], *, project_root: str | Path):
        self.raw = raw
        self.project_root = Path(project_root).resolve()
        self.validate()

    @classmethod
    def load(cls, path: str | Path, *, project_root: str | Path) -> "RuntimeConfig":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), project_root=project_root)

    def validate(self) -> None:
        if self.raw["runtime_rules_version"] != "intraday_runtime_v0.1":
            raise ValueError("unexpected runtime rules version")
        if self.raw["business_timezone"] != "Asia/Shanghai":
            raise ValueError("A-share runtime timezone must be Asia/Shanghai")
        ZoneInfo(self.raw["business_timezone"])
        expected = [
            {"start": "09:30", "end": "10:30"},
            {"start": "10:30", "end": "11:30"},
            {"start": "13:00", "end": "14:00"},
            {"start": "14:00", "end": "15:00"},
        ]
        if self.raw["periods"] != expected:
            raise ValueError("formal 60m periods changed")
        if int(self.raw["buffer_minutes"]) < 0 or int(self.raw["buffer_minutes"]) > 15:
            raise ValueError("invalid data arrival buffer")
        if self.raw["industry_context"] != "DEFERRED":
            raise ValueError("industry context cannot be a runtime dependency")
        if int(self.raw["retry"]["max_attempts"]) < 1:
            raise ValueError("retry attempts must be finite and positive")
        if len(self.raw["retry"]["backoff_seconds"]) < int(self.raw["retry"]["max_attempts"]) - 1:
            raise ValueError("retry backoff list is incomplete")

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.raw["business_timezone"])

    @property
    def rules_versions(self) -> dict[str, str]:
        return {
            "runtime": self.raw["runtime_rules_version"],
            "market_60m": "market_60m_risk_v0.1",
            "market_15m": "market_15m_internal_v0.1",
            "stock_60m": "stock_60m_risk_v0.1",
            "stock_15m": "stock_15m_internal_v0.1",
        }

    def verify_frozen_rules(self) -> dict[str, str]:
        actual = {}
        for relative, expected in self.raw["frozen_rule_hashes"].items():
            path = self.project_root / relative
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected:
                raise ValueError(f"FROZEN_RULE_MUTATION: {relative}")
            actual[relative] = digest
        return actual
