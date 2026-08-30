"""Append-only JSON raw cache with a small JSONL manifest."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import DataType


class CacheStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    instrument_id: str
    provider: str
    provider_symbol: str
    data_type: DataType
    path: str
    fetched_at: str
    source_timestamp: int | None
    data_start: int | None
    data_end: int | None
    request_start: int | None
    request_end: int | None
    status: CacheStatus

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["data_type"] = self.data_type.value
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "CacheEntry":
        return cls(
            instrument_id=str(value["instrument_id"]),
            provider=str(value["provider"]),
            provider_symbol=str(value["provider_symbol"]),
            data_type=DataType(str(value["data_type"])),
            path=str(value["path"]),
            fetched_at=str(value["fetched_at"]),
            source_timestamp=(
                int(value["source_timestamp"])
                if value.get("source_timestamp") is not None
                else None
            ),
            data_start=int(value["data_start"]) if value.get("data_start") is not None else None,
            data_end=int(value["data_end"]) if value.get("data_end") is not None else None,
            request_start=(
                int(value["request_start"]) if value.get("request_start") is not None else None
            ),
            request_end=(
                int(value["request_end"]) if value.get("request_end") is not None else None
            ),
            status=CacheStatus(str(value["status"])),
        )


_SAFE_FRAGMENT = re.compile(r"[^A-Za-z0-9._-]+")
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "app_key",
    "app_secret",
    "access_token",
    "authorization",
    "hithink_api_key",
    "hithink_finance_api_key",
    "longbridge_app_key",
    "longbridge_app_secret",
    "longbridge_access_token",
    "secret",
    "token",
    "x-api-key",
}
_NORMALIZED_SENSITIVE_KEYS = frozenset(item.replace("-", "_") for item in _SENSITIVE_KEYS)


def _safe_fragment(value: str) -> str:
    return _SAFE_FRAGMENT.sub("_", value).strip("._") or "unknown"


def _assert_no_secrets(value: object, path: str = "raw") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _NORMALIZED_SENSITIVE_KEYS:
                raise TrendMonitorError(
                    ErrorCategory.CACHE_INVALID,
                    f"Refusing to cache sensitive field at {path}.{key}",
                )
            _assert_no_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secrets(child, f"{path}[{index}]")


def _source_bounds(raw: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    data = raw.get("data")
    if not isinstance(data, dict):
        return None, None, None
    timestamp = data.get("timestamp")
    source_timestamp = int(timestamp) if isinstance(timestamp, (int, float)) else None
    items = data.get("item")
    dates: list[int] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            value = item.get("date_ms", item.get("timestamp"))
            if isinstance(value, (int, float)):
                epoch = int(value)
                dates.append(epoch * 1000 if epoch < 10_000_000_000 else epoch)
    if dates:
        return max(dates), min(dates), max(dates)
    return source_timestamp, source_timestamp, source_timestamp


class RawCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.manifest_path = self.root / "manifest.jsonl"

    def save(
        self,
        *,
        instrument_id: str,
        provider: str,
        provider_symbol: str,
        data_type: DataType,
        raw: dict[str, Any],
        fetched_at: datetime | None = None,
        source_timestamp: int | None = None,
        data_start: int | None = None,
        data_end: int | None = None,
        request_start: int | None = None,
        request_end: int | None = None,
    ) -> CacheEntry:
        _assert_no_secrets(raw)
        now = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        inferred_timestamp, inferred_start, inferred_end = _source_bounds(raw)
        source_timestamp = source_timestamp if source_timestamp is not None else inferred_timestamp
        data_start = data_start if data_start is not None else inferred_start
        data_end = data_end if data_end is not None else inferred_end

        directory = self.root / _safe_fragment(provider.lower()) / data_type.value / now.strftime(
            "%Y-%m-%d"
        )
        directory.mkdir(parents=True, exist_ok=True)
        request_stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
        range_part = f"{data_start or 'na'}-{data_end or 'na'}"
        filename = "__".join(
            (
                request_stamp,
                _safe_fragment(instrument_id),
                _safe_fragment(provider_symbol),
                data_type.value,
                f"src-{range_part}",
                uuid4().hex[:8],
            )
        ) + ".json"
        path = directory / filename
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(raw, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        except (OSError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                f"Unable to write raw cache file: {path}",
            ) from exc

        entry = CacheEntry(
            instrument_id=instrument_id,
            provider=provider.lower(),
            provider_symbol=provider_symbol,
            data_type=data_type,
            path=str(path),
            fetched_at=now.isoformat(),
            source_timestamp=source_timestamp,
            data_start=data_start,
            data_end=data_end,
            request_start=request_start,
            request_end=request_end,
            status=CacheStatus.FRESH,
        )
        self._append_manifest(entry)
        return entry

    def _append_manifest(self, entry: CacheEntry) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with self.manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                f"Unable to update raw cache manifest: {self.manifest_path}",
            ) from exc

    def load(self, entry_or_path: CacheEntry | str | Path) -> dict[str, Any]:
        path = Path(entry_or_path.path if isinstance(entry_or_path, CacheEntry) else entry_or_path)
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                "Raw cache path is outside the configured cache root",
            )
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                f"Unable to read valid JSON from raw cache: {resolved}",
            ) from exc
        if not isinstance(payload, dict):
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                f"Raw cache root must be a JSON object: {resolved}",
            )
        _assert_no_secrets(payload)
        return payload

    def entries(self) -> tuple[CacheEntry, ...]:
        if not self.manifest_path.exists():
            return ()
        result: list[CacheEntry] = []
        try:
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise TypeError("manifest entry must be an object")
                    result.append(CacheEntry.from_dict(value))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.CACHE_INVALID,
                f"Invalid raw cache manifest: {self.manifest_path}",
            ) from exc
        return tuple(result)

    def latest(
        self, instrument_id: str, provider: str, data_type: DataType
    ) -> CacheEntry | None:
        matching = [
            (index, entry)
            for index, entry in enumerate(self.entries())
            if entry.instrument_id == instrument_id
            and entry.provider == provider.lower()
            and entry.data_type is data_type
        ]
        return max(matching, key=lambda item: (item[1].fetched_at, item[0]))[1] if matching else None

    def record_status(self, entry: CacheEntry, status: CacheStatus) -> CacheEntry:
        """Append a status event without modifying or deleting the raw evidence."""
        updated = replace(entry, status=status)
        self._append_manifest(updated)
        return updated

    def status(
        self,
        instrument_id: str,
        provider: str,
        data_type: DataType,
        *,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> CacheStatus:
        try:
            entry = self.latest(instrument_id, provider, data_type)
        except TrendMonitorError:
            return CacheStatus.INVALID
        if entry is None or not Path(entry.path).exists():
            return CacheStatus.MISSING
        if entry.status is CacheStatus.INVALID:
            return CacheStatus.INVALID
        if entry.status is CacheStatus.STALE and max_age is None:
            return CacheStatus.STALE
        try:
            self.load(entry)
        except TrendMonitorError:
            return CacheStatus.INVALID
        if max_age is not None:
            fetched_at = datetime.fromisoformat(entry.fetched_at)
            current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            if current - fetched_at > max_age:
                return CacheStatus.STALE
        return CacheStatus.FRESH

    @staticmethod
    def with_status(entry: CacheEntry, status: CacheStatus) -> CacheEntry:
        return replace(entry, status=status)
