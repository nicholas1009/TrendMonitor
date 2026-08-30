"""Append-only TASK_011 evidence and context result storage."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import StockIndustryContextResult


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StockIndustryContextStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def save_result(self, value: StockIndustryContextResult, human_report: str) -> dict[str, str]:
        stamp = _SAFE.sub("_", value.period_end or "unknown").strip("._")
        identity = uuid4().hex[:8]
        json_path = self.root / "json" / (
            f"{stamp}__{value.instrument_id}__{value.rules_version}__{identity}.json"
        )
        markdown_path = self.root / "markdown" / f"{stamp}__{value.instrument_id}__{identity}.md"
        self._write_new(json_path, value.to_dict())
        self._write_new_text(markdown_path, human_report)
        self._append_manifest(
            {
                "output_type": "STOCK_INDUSTRY_CONTEXT",
                "schema_version": value.schema_version,
                "rules_version": value.rules_version,
                "instrument_id": value.instrument_id,
                "period_end": value.period_end,
                "status": value.status,
                "path": str(json_path),
                "human_report": str(markdown_path),
            }
        )
        return {"json": str(json_path), "human_report": str(markdown_path)}

    def save_evidence(self, payload: Mapping[str, object], *, observed_at: str) -> str:
        stamp = _SAFE.sub("_", observed_at).strip("._")
        path = self.root / "evidence" / f"{stamp}__benchmark_mapping__{uuid4().hex[:8]}.json"
        self._write_new(path, payload)
        self._append_manifest(
            {
                "output_type": "BENCHMARK_MAPPING_EVIDENCE",
                "schema_version": payload.get("schema_version", 1),
                "observed_at": observed_at,
                "path": str(path),
            }
        )
        return str(path)

    def _append_manifest(self, payload: Mapping[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with (self.root / "manifest.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to append industry manifest") from exc

    @staticmethod
    def _write_new(path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"unable to save: {path}") from exc

    @staticmethod
    def _write_new_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(value)
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"unable to save: {path}") from exc
