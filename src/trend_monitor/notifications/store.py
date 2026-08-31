"""Append-only notification records without transport secrets."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any

from trend_monitor.schemas.notification import NotificationRecord, NotificationStatus


FORBIDDEN_KEYS = {"device_key", "bark_device_key", "authorization", "endpoint", "url"}


class NotificationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.manifest = self.root / "manifest.jsonl"

    def entries(self) -> list[dict[str, Any]]:
        if not self.manifest.is_file():
            return []
        return [
            json.loads(line)
            for line in self.manifest.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def sent(self, event_key: str) -> bool:
        return any(
            item.get("event_key") == event_key
            and item.get("status") == NotificationStatus.SENT.value
            for item in self.entries()
        )

    def append(self, record: NotificationRecord) -> str:
        payload = record.to_dict()
        if FORBIDDEN_KEYS.intersection(key.lower() for key in payload):
            raise ValueError("notification record contains forbidden transport fields")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return str(self.manifest)

    def latest_test_status(self) -> str | None:
        return next(
            (
                str(item.get("status"))
                for item in reversed(self.entries())
                if item.get("event_type") == "TEST"
            ),
            None,
        )
