"""Append-only Risk Input JSON snapshots for deterministic replay."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from datetime import datetime
from zoneinfo import ZoneInfo

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

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def save_cycle(
        self,
        *,
        cycle_id: str,
        analysis_as_of: str,
        provider_observed_at: str,
        instrument_snapshot_paths: dict[str, str],
        raw_root: str | Path,
    ) -> tuple[str, dict[str, Any]]:
        """Freeze one immutable Raw-member bundle for an analysis cycle.

        Instrument snapshots already contain the normalized/validated Risk
        Input representation.  This bundle does not copy Raw data; it freezes
        the exact append-only Raw paths and hashes consumed by every member.
        """

        shanghai = ZoneInfo("Asia/Shanghai")
        parsed_as_of = datetime.fromisoformat(analysis_as_of)
        parsed_observed = datetime.fromisoformat(provider_observed_at)
        if parsed_as_of.tzinfo is None or parsed_observed.tzinfo is None:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                "cycle snapshot times must be timezone-aware",
            )
        canonical_as_of = parsed_as_of.astimezone(shanghai).isoformat()
        canonical_observed = parsed_observed.astimezone(shanghai)
        raw_base = Path(raw_root).resolve()
        members: list[dict[str, object]] = []
        instrument_snapshots: list[dict[str, object]] = []
        logical_members: set[tuple[str, str]] = set()
        for instrument_id, snapshot_path in sorted(instrument_snapshot_paths.items()):
            payload = self.load(snapshot_path)
            if payload.get("instrument_id") != instrument_id:
                raise TrendMonitorError(
                    ErrorCategory.CACHE_INVALID,
                    f"cycle instrument snapshot identity mismatch: {instrument_id}",
                )
            if payload.get("as_of") != canonical_as_of:
                raise TrendMonitorError(
                    ErrorCategory.CACHE_INVALID,
                    f"cycle instrument snapshot as_of mismatch: {instrument_id}",
                )
            resolved_snapshot = Path(snapshot_path).resolve()
            instrument_snapshots.append(
                {
                    "instrument_id": instrument_id,
                    "snapshot_path": str(resolved_snapshot),
                    "snapshot_hash": self._file_sha256(resolved_snapshot),
                }
            )
            for field, period in (
                ("daily", "1d"),
                ("risk_60m", "60m"),
                ("support_15m", "15m"),
            ):
                risk_input = payload.get(field)
                trace = risk_input.get("source_trace") if isinstance(risk_input, dict) else None
                raw_path = trace.get("raw_path") if isinstance(trace, dict) else None
                if not isinstance(raw_path, str) or not raw_path:
                    raise TrendMonitorError(
                        ErrorCategory.CACHE_INVALID,
                        f"cycle member has no Raw path: {instrument_id}:{period}",
                    )
                resolved_raw = Path(raw_path).resolve()
                if not resolved_raw.is_relative_to(raw_base) or not resolved_raw.is_file():
                    raise TrendMonitorError(
                        ErrorCategory.CACHE_INVALID,
                        f"cycle Raw member is outside cache or missing: {instrument_id}:{period}",
                    )
                logical_key = (instrument_id, period)
                if logical_key in logical_members:
                    raise TrendMonitorError(
                        ErrorCategory.DATA_CONFLICT,
                        f"cycle has duplicate logical Raw member: {instrument_id}:{period}",
                    )
                logical_members.add(logical_key)
                members.append(
                    {
                        "instrument_id": instrument_id,
                        "period": period,
                        "provider": trace.get("actual_provider"),
                        "provider_symbol": trace.get("provider_symbol"),
                        "raw_path": str(resolved_raw),
                        "raw_hash": self._file_sha256(resolved_raw),
                        "fetched_at": trace.get("fetched_at"),
                        "source_timestamp": trace.get("source_timestamp"),
                    }
                )
        fetched_times = []
        for item in members:
            fetched_at = item.get("fetched_at")
            if isinstance(fetched_at, str) and fetched_at:
                parsed = datetime.fromisoformat(fetched_at)
                if parsed.tzinfo is None:
                    raise TrendMonitorError(
                        ErrorCategory.INVALID_DATA,
                        "cycle member fetched_at must be timezone-aware",
                    )
                fetched_times.append(parsed.astimezone(shanghai))
        if fetched_times:
            canonical_observed = max((*fetched_times, canonical_observed))
        if not members:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "cycle snapshot has no members")
        core = {
            "schema_version": 1,
            "snapshot_kind": "CYCLE_RAW_SNAPSHOT_BUNDLE",
            "cycle_id": cycle_id,
            "analysis_as_of": canonical_as_of,
            "provider_observed_at": canonical_observed.isoformat(),
            "timezone": "Asia/Shanghai",
            "instrument_snapshots": instrument_snapshots,
            "members": sorted(
                members, key=lambda item: (str(item["instrument_id"]), str(item["period"]))
            ),
        }
        snapshot_hash = hashlib.sha256(self._canonical_json(core).encode()).hexdigest()
        payload = {
            **core,
            "cycle_raw_snapshot_id": f"cycle_raw_snapshot_v1:{snapshot_hash}",
            "snapshot_hash": snapshot_hash,
        }
        _assert_safe(payload)
        directory = self.root / "cycle"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = canonical_as_of.replace(":", "").replace("+", "p").replace("-", "")
        path = directory / f"{stamp}__{snapshot_hash[:16]}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise TrendMonitorError(
                    ErrorCategory.DATA_CONFLICT,
                    "cycle snapshot hash collision or immutable payload mismatch",
                )
        else:
            try:
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                with self.manifest.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "kind": "cycle",
                                "identity": cycle_id,
                                "as_of": canonical_as_of,
                                "cycle_raw_snapshot_id": payload["cycle_raw_snapshot_id"],
                                "path": str(path),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except (OSError, TypeError, ValueError) as exc:
                raise TrendMonitorError(
                    ErrorCategory.CACHE_INVALID,
                    f"unable to save cycle snapshot: {path}",
                ) from exc
        return str(path), payload

    def load_cycle(self, path: str | Path, *, verify_files: bool = True) -> dict[str, Any]:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root / "cycle"):
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                "cycle snapshot path is outside configured cycle root",
            )
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                f"invalid cycle snapshot: {resolved}",
            ) from exc
        if not isinstance(payload, dict) or payload.get("snapshot_kind") != "CYCLE_RAW_SNAPSHOT_BUNDLE":
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "unsupported cycle snapshot schema")
        core = {
            key: payload[key]
            for key in (
                "schema_version",
                "snapshot_kind",
                "cycle_id",
                "analysis_as_of",
                "provider_observed_at",
                "timezone",
                "instrument_snapshots",
                "members",
            )
        }
        expected_hash = hashlib.sha256(self._canonical_json(core).encode()).hexdigest()
        if payload.get("snapshot_hash") != expected_hash or payload.get(
            "cycle_raw_snapshot_id"
        ) != f"cycle_raw_snapshot_v1:{expected_hash}":
            raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "cycle snapshot hash mismatch")
        if verify_files:
            for item in (*payload["instrument_snapshots"], *payload["members"]):
                file_path = Path(item.get("snapshot_path") or item.get("raw_path")).resolve()
                expected = item.get("snapshot_hash") or item.get("raw_hash")
                if not file_path.is_file() or self._file_sha256(file_path) != expected:
                    raise TrendMonitorError(
                        ErrorCategory.CACHE_INVALID,
                        f"cycle snapshot member changed or is missing: {file_path}",
                    )
        _assert_safe(payload)
        return payload

    def require_cycle_members(
        self,
        cycle: dict[str, Any],
        instrument_snapshot_paths: dict[str, str],
    ) -> None:
        frozen = {
            str(item["instrument_id"]): str(Path(item["snapshot_path"]).resolve())
            for item in cycle.get("instrument_snapshots", [])
        }
        required = {
            instrument_id: str(Path(path).resolve())
            for instrument_id, path in instrument_snapshot_paths.items()
        }
        missing_or_different = {
            key: {"required": value, "frozen": frozen.get(key)}
            for key, value in required.items()
            if frozen.get(key) != value
        }
        if missing_or_different:
            raise TrendMonitorError(
                ErrorCategory.DATA_CONFLICT,
                "cycle snapshot instrument identity mismatch",
                details={"members": missing_or_different},
            )
