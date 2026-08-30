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
