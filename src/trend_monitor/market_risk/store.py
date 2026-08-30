"""Append-only machine and human Market 60m Risk output storage."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import Market60mRiskResult


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class MarketRiskOutputStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def save(self, result: Market60mRiskResult, human_report: str) -> tuple[str, str]:
        if result.last_completed_bar_end is None:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "blocked result is not persisted")
        stamp = _SAFE.sub("_", result.last_completed_bar_end).strip("._")
        identity = uuid4().hex[:8]
        machine_dir = self.root / "json"
        human_dir = self.root / "markdown"
        machine_dir.mkdir(parents=True, exist_ok=True)
        human_dir.mkdir(parents=True, exist_ok=True)
        machine = machine_dir / f"{stamp}__{result.rules_version}__{identity}.json"
        human = human_dir / f"{stamp}__{result.rules_version}__{identity}.md"
        try:
            with machine.open("x", encoding="utf-8") as handle:
                json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            with human.open("x", encoding="utf-8") as handle:
                handle.write(human_report)
            self.root.mkdir(parents=True, exist_ok=True)
            with self.manifest.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "output_type": "CURRENT_RISK_RESULT",
                            "schema_version": result.schema_version,
                            "rules_version": result.rules_version,
                            "last_completed_bar_end": result.last_completed_bar_end,
                            "machine_path": str(machine),
                            "human_path": str(human),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to save Market risk output") from exc
        return str(machine), str(human)

    def save_replay(
        self,
        replay: Mapping[str, object],
        *,
        last_completed_bar_end: str,
        rules_version: str,
    ) -> str:
        """Persist an authoritative replay snapshot without overwriting history."""
        stamp = _SAFE.sub("_", last_completed_bar_end).strip("._")
        identity = uuid4().hex[:8]
        replay_dir = self.root / "replay"
        replay_dir.mkdir(parents=True, exist_ok=True)
        path = replay_dir / f"{stamp}__{rules_version}__{identity}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(dict(replay), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self.root.mkdir(parents=True, exist_ok=True)
            with self.manifest.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "output_type": "HISTORICAL_REPLAY",
                            "schema_version": replay.get("schema_version", 1),
                            "rules_version": rules_version,
                            "last_completed_bar_end": last_completed_bar_end,
                            "replay_path": str(path),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to save Market replay output") from exc
        return str(path)

    def load(self, path: str | Path) -> dict[str, object]:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "risk output path is outside root")
        try:
            value = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "invalid Market risk output") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unsupported Market risk output schema")
        return value
