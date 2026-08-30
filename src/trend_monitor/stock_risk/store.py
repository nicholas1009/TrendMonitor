"""Append-only TASK_010 results, replay, and exact Risk Input snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import RiskInput, StockIntradayMonitorResult


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StockIntradayOutputStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def save_monitor(self, value: StockIntradayMonitorResult, human_report: str) -> dict[str, str]:
        end = value.stock_15m_internal.period_60m_end
        stamp = _SAFE.sub("_", end).strip("._")
        identity = uuid4().hex[:8]
        outputs = {}
        components = (
            ("stocks_60m", value.stock_60m_risk.to_dict() if value.stock_60m_risk else None),
            ("stocks_15m_internal", value.stock_15m_internal.to_dict()),
            ("stock_intraday_monitor", value.to_dict()),
        )
        for directory, payload in components:
            if payload is None:
                continue
            rules_version = str(payload.get("rules_version", "combined_v0.1"))
            path = self.root / directory / "json" / f"{stamp}__{value.instrument_id}__{rules_version}__{identity}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write_new(path, payload)
            outputs[directory] = str(path)
            self._append_manifest(
                self.root / directory,
                {
                    "output_type": directory.upper(),
                    "schema_version": payload.get("schema_version", 1),
                    "rules_version": rules_version,
                    "instrument_id": value.instrument_id,
                    "period_end": end,
                    "path": str(path),
                },
            )
        human = self.root / "stock_intraday_monitor" / "markdown" / f"{stamp}__{value.instrument_id}__{identity}.md"
        human.parent.mkdir(parents=True, exist_ok=True)
        try:
            with human.open("x", encoding="utf-8") as handle:
                handle.write(human_report)
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to save stock human report") from exc
        outputs["human_report"] = str(human)
        return outputs

    def save_replay(self, payload: Mapping[str, object], *, period_end: str, rules_version: str) -> str:
        stamp = _SAFE.sub("_", period_end).strip("._")
        path = self.root / "stock_intraday_monitor" / "replay" / f"{stamp}__{rules_version}__{uuid4().hex[:8]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_new(path, payload)
        self._append_manifest(
            self.root / "stock_intraday_monitor",
            {
                "output_type": "HISTORICAL_REPLAY",
                "schema_version": payload.get("schema_version", 1),
                "rules_version": rules_version,
                "period_end": period_end,
                "path": str(path),
            },
        )
        return str(path)

    @staticmethod
    def _write_new(path: Path, payload: Mapping[str, object]) -> None:
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"unable to save stock output: {path}") from exc

    @staticmethod
    def _append_manifest(root: Path, payload: Mapping[str, object]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        try:
            with (root / "manifest.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to update stock output manifest") from exc


class StockRiskInputStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def save_period(
        self,
        *,
        as_of: str,
        inputs_60m: Mapping[str, RiskInput],
        inputs_15m: Mapping[str, RiskInput],
        rules_version: str,
        market_60m_result: Mapping[str, object] | None = None,
        market_15m_result: Mapping[str, object] | None = None,
    ) -> str:
        payload = {
            "schema_version": 1,
            "rules_version": rules_version,
            "as_of": as_of,
            "inputs_60m": {key: value.to_dict() for key, value in inputs_60m.items()},
            "inputs_15m": {key: value.to_dict() for key, value in inputs_15m.items()},
            "market_60m_result": dict(market_60m_result) if market_60m_result else None,
            "market_15m_result": dict(market_15m_result) if market_15m_result else None,
        }
        reusable = self._matching(payload)
        if reusable:
            return str(reusable)
        stamp = _SAFE.sub("_", as_of).strip("._")
        path = self.root / f"{stamp}__{rules_version}__{uuid4().hex[:8]}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        StockIntradayOutputStore._write_new(path, payload)
        try:
            with self.manifest.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "kind": "STOCK_INTRADAY_RISK_INPUT",
                            "rules_version": rules_version,
                            "as_of": as_of,
                            "path": str(path),
                            "instruments": list(inputs_60m),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unable to save stock Risk Input manifest") from exc
        return str(path)

    def mark_incomplete_attempts_superseded(self) -> int:
        """Append status events for snapshots left by an interrupted verifier."""
        if not self.manifest.exists():
            return 0
        try:
            entries = [json.loads(line) for line in self.manifest.read_text(encoding="utf-8").splitlines() if line]
            already = {
                str(item.get("path"))
                for item in entries
                if item.get("kind") == "STOCK_INTRADAY_RISK_INPUT_STATUS"
            }
            targets = []
            for entry in entries:
                if entry.get("kind") != "STOCK_INTRADAY_RISK_INPUT" or str(entry.get("path")) in already:
                    continue
                path = Path(str(entry["path"])).resolve()
                if not path.is_relative_to(self.root) or not path.is_file():
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("market_60m_result") is None or payload.get("market_15m_result") is None:
                    targets.append(path)
            if targets:
                with self.manifest.open("a", encoding="utf-8") as handle:
                    for path in targets:
                        handle.write(
                            json.dumps(
                                {
                                    "kind": "STOCK_INTRADAY_RISK_INPUT_STATUS",
                                    "status": "SUPERSEDED_INCOMPLETE_VERIFICATION_ATTEMPT",
                                    "path": str(path),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
            return len(already) + len(targets)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "invalid stock Risk Input audit") from exc

    def _matching(self, payload: Mapping[str, object]) -> Path | None:
        if not self.manifest.exists():
            return None
        try:
            for line in reversed(self.manifest.read_text(encoding="utf-8").splitlines()):
                entry = json.loads(line)
                if entry.get("as_of") != payload["as_of"] or entry.get("rules_version") != payload["rules_version"]:
                    continue
                path = Path(str(entry["path"])).resolve()
                if path.is_relative_to(self.root) and path.is_file():
                    if json.loads(path.read_text(encoding="utf-8")) == dict(payload):
                        return path
            return None
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "invalid stock Risk Input manifest") from exc
