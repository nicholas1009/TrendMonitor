"""Append-only stores for 15m internal results and their Risk Input snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import Market15mInternalResult, RiskInput


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class Market15mInternalStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def save_result(
        self,
        result: Market15mInternalResult,
        human_report: str,
        *,
        output_type: str = "CURRENT_INTERNAL_RESULT",
    ) -> tuple[str, str]:
        stamp = _SAFE.sub("_", result.period_60m_end).strip("._")
        identity = uuid4().hex[:8]
        machine_dir, human_dir = self.root / "json", self.root / "markdown"
        machine_dir.mkdir(parents=True, exist_ok=True)
        human_dir.mkdir(parents=True, exist_ok=True)
        machine = machine_dir / f"{stamp}__{result.rules_version}__{identity}.json"
        human = human_dir / f"{stamp}__{result.rules_version}__{identity}.md"
        self._write_new(machine, result.to_dict())
        try:
            with human.open("x", encoding="utf-8") as handle:
                handle.write(human_report)
            self._append_manifest(
                {
                    "output_type": output_type,
                    "schema_version": result.schema_version,
                    "rules_version": result.rules_version,
                    "60m_period_end": result.period_60m_end,
                    "period_status": result.period_status.value,
                    "machine_path": str(machine),
                    "human_path": str(human),
                    "source_60m_risk_result_id": result.source_60m_risk_result_id,
                }
            )
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to save 15m human report") from exc
        return str(machine), str(human)

    def save_replay(
        self,
        replay: Mapping[str, object],
        *,
        last_period_end: str,
        rules_version: str,
    ) -> str:
        stamp = _SAFE.sub("_", last_period_end).strip("._")
        path = self.root / "replay" / f"{stamp}__{rules_version}__{uuid4().hex[:8]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_new(path, dict(replay))
        self._append_manifest(
            {
                "output_type": "HISTORICAL_REPLAY",
                "schema_version": replay.get("schema_version", 1),
                "rules_version": rules_version,
                "60m_period_end": last_period_end,
                "replay_path": str(path),
            }
        )
        return str(path)

    def load(self, path: str | Path) -> dict[str, object]:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "15m output path is outside root")
        try:
            value = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "invalid 15m output") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unsupported 15m output schema")
        return value

    @staticmethod
    def _write_new(path: Path, payload: Mapping[str, object]) -> None:
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"unable to save 15m output: {path}") from exc

    def _append_manifest(self, payload: Mapping[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with self.manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to update 15m manifest") from exc


class Market15mRiskInputStore:
    """Persist the exact 8-index Risk Inputs consumed for a period."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def save_period(
        self,
        *,
        as_of: str,
        inputs: Mapping[str, RiskInput],
        rules_version: str,
    ) -> str:
        payload = {
            "schema_version": 1,
            "rules_version": rules_version,
            "as_of": as_of,
            "inputs": {key: value.to_dict() for key, value in inputs.items()},
        }
        reusable = self._matching_snapshot(payload)
        if reusable is not None:
            return str(reusable)
        stamp = _SAFE.sub("_", as_of).strip("._")
        path = self.root / f"{stamp}__{rules_version}__{uuid4().hex[:8]}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            with self.manifest.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "kind": "MARKET_15M_PERIOD_RISK_INPUT",
                            "rules_version": rules_version,
                            "as_of": as_of,
                            "path": str(path),
                            "instruments": list(inputs),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to save 15m Risk Input snapshot") from exc
        return str(path)

    def _matching_snapshot(self, payload: Mapping[str, object]) -> Path | None:
        if not self.manifest.exists():
            return None
        try:
            entries = [json.loads(line) for line in self.manifest.read_text(encoding="utf-8").splitlines() if line]
            for entry in reversed(entries):
                if (
                    entry.get("as_of") != payload["as_of"]
                    or entry.get("rules_version") != payload["rules_version"]
                ):
                    continue
                path = Path(str(entry["path"])).resolve()
                if not path.is_relative_to(self.root) or not path.is_file():
                    continue
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing == dict(payload):
                    return path
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "invalid 15m Risk Input manifest") from exc
        return None
