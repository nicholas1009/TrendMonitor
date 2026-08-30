"""Append-only Risk Input JSON snapshots for deterministic replay."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import InstrumentRiskInputBundle, RiskInputGroup


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SENSITIVE = {
    "api_key", "app_key", "app_secret", "access_token", "authorization", "secret", "token",
    "hithink_api_key", "hithink_finance_api_key", "longbridge_app_key",
    "longbridge_app_secret", "longbridge_access_token",
}


def _assert_safe(value: object, path: str = "snapshot") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower().replace("-", "_") in _SENSITIVE:
                raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"sensitive snapshot field: {path}.{key}")
            _assert_safe(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe(child, f"{path}[{index}]")


class RiskInputSnapshotStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def save_bundle(self, bundle: InstrumentRiskInputBundle) -> str:
        return self._save("instrument", bundle.instrument_id, bundle.as_of, bundle.to_dict())

    def save_group(self, group: RiskInputGroup) -> str:
        return self._save("group", group.group_name, group.as_of, group.to_dict())

    def _save(self, kind: str, identity: str, as_of: str, payload: dict[str, Any]) -> str:
        _assert_safe(payload)
        fragment = _SAFE.sub("_", identity).strip("._") or "unknown"
        stamp = as_of.replace(":", "").replace("+", "p").replace("-", "")
        directory = self.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}__{fragment}__{uuid4().hex[:8]}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self.root.mkdir(parents=True, exist_ok=True)
            with self.manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"kind": kind, "identity": identity, "as_of": as_of, "path": str(path)}, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"unable to save Risk Input snapshot: {path}") from exc
        return str(path)

    def load(self, path: str | Path) -> dict[str, Any]:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "snapshot path is outside configured root")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, f"invalid Risk Input snapshot: {resolved}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unsupported Risk Input snapshot schema")
        _assert_safe(payload)
        return payload
