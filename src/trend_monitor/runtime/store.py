"""Append-only runtime reports, failure records, and manifests."""

from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from trend_monitor.schemas.runtime import RuntimeRunRecord


SAFE = re.compile(r"[^A-Za-z0-9._-]+")
COMPLETED = {"SUCCESS", "SUCCESS_WITH_DEGRADATION"}
EVENT_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bark_device_key",
    "device_key",
    "hithink_api_key",
    "hithink_finance_api_key",
    "secret",
    "token",
    "x_api_key",
}


def _assert_event_secret_free(value: object, path: str = "event") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in EVENT_SENSITIVE_KEYS:
                raise ValueError(f"runtime event contains sensitive field at {path}.{key}")
            _assert_event_secret_free(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_event_secret_free(child, f"{path}[{index}]")


class RuntimeStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def entries(self) -> list[dict[str, Any]]:
        if not self.manifest.is_file():
            return []
        return [json.loads(line) for line in self.manifest.read_text(encoding="utf-8").splitlines() if line]

    def completed(self, idempotency_key: str) -> dict[str, Any] | None:
        for item in reversed(self.entries()):
            if item.get("idempotency_key") == idempotency_key and item.get("status") in COMPLETED:
                return item
        return None

    @staticmethod
    def _period_identity(item: dict[str, Any]) -> str | None:
        """Resolve identity for both current and legacy failure records."""

        explicit = item.get("idempotency_key")
        if explicit:
            return str(explicit)
        trading_date = item.get("trading_date")
        period_end = item.get("period_end")
        rules_versions = item.get("rules_versions")
        if not trading_date or not period_end or not isinstance(rules_versions, dict):
            return None
        signature = ",".join(
            f"{key}={rules_versions[key]}" for key in sorted(rules_versions)
        )
        return f"{trading_date}|{period_end}|{signature}"

    def terminal_failure(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the latest matching non-recoverable failure, if one exists."""

        for item in reversed(self.entries()):
            error = item.get("error_summary") or {}
            if (
                item.get("status") == "FAILED"
                and error.get("recoverable") is False
                and self._period_identity(item) == idempotency_key
            ):
                return item
        return None

    def skipped_already_recorded(self, skip_key: str) -> bool:
        return any(item.get("skip_key") == skip_key for item in self.entries())

    def save_report(self, payload: dict[str, Any], human: str, *, idempotency_key: str) -> tuple[str, str, str]:
        business_payload = dict(payload)
        for key in ("generated_at", "execution_mode", "notification_eligibility", "source_ids"):
            business_payload.pop(key, None)
        canonical = json.dumps(business_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self.completed(idempotency_key)
        if existing:
            if existing.get("result_sha256") != digest:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return str(existing["combined_result_id"]), str(existing["human_report_id"]), digest
        stamp = SAFE.sub("_", str(payload["period_end"])).strip("._")
        machine = self.root / "reports" / f"{stamp}__{digest[:12]}.json"
        human_path = self.root / "reports" / f"{stamp}__{digest[:12]}.md"
        machine.parent.mkdir(parents=True, exist_ok=True)
        with machine.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with human_path.open("x", encoding="utf-8") as handle:
            handle.write(human)
        return str(machine), str(human_path), digest

    def append(self, record: RuntimeRunRecord, *, idempotency_key: str | None = None, result_sha256: str | None = None, human_report_id: str | None = None, skip_key: str | None = None) -> str:
        payload = record.to_dict()
        payload.update(
            {
                "idempotency_key": idempotency_key,
                "result_sha256": result_sha256,
                "human_report_id": human_report_id,
                "skip_key": skip_key,
            }
        )
        self.root.mkdir(parents=True, exist_ok=True)
        run_path = self.root / "runs" / f"{record.run_id}.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        with run_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with self.manifest.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return str(run_path)

    def latest_success(self) -> dict[str, Any] | None:
        return next((item for item in reversed(self.entries()) if item.get("status") in COMPLETED), None)

    @property
    def event_manifest(self) -> Path:
        return self.root / "events" / "manifest.jsonl"

    def event_entries(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if not self.event_manifest.is_file():
            return []
        values = [
            json.loads(line)
            for line in self.event_manifest.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if event_type is None:
            return values
        return [item for item in values if item.get("event_type") == event_type]

    def event_record(
        self,
        idempotency_key: str,
        *,
        statuses: set[str] | None = None,
    ) -> dict[str, Any] | None:
        for item in reversed(self.event_entries()):
            if item.get("idempotency_key") != idempotency_key:
                continue
            if statuses is None or item.get("status") in statuses:
                return item
        return None

    def append_event(self, payload: dict[str, Any]) -> str:
        """Persist a non-Risk runtime event without changing the 60m manifest."""

        _assert_event_secret_free(payload)
        run_id = str(payload["run_id"])
        self.event_manifest.parent.mkdir(parents=True, exist_ok=True)
        run_path = self.root / "events" / "runs" / f"{SAFE.sub('_', run_id)}.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        with run_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with self.event_manifest.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return str(run_path)
