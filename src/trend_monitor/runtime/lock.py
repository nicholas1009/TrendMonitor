"""fcntl process lock: crash-safe with PID/age stale metadata."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
from typing import Any


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class ProcessLock:
    def __init__(self, path: str | Path, *, stale_seconds: int):
        self.path = Path(path).resolve()
        self.stale_seconds = stale_seconds
        self.handle = None
        self.previous_stale = False
        self.blocking_metadata: dict[str, Any] | None = None

    def acquire(self, *, run_id: str, now: datetime) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.seek(0)
            try:
                self.blocking_metadata = json.loads(self.handle.read() or "{}")
            except json.JSONDecodeError:
                self.blocking_metadata = {"status": "LOCKED_METADATA_INVALID"}
            return False
        self.handle.seek(0)
        try:
            previous = json.loads(self.handle.read() or "{}")
        except json.JSONDecodeError:
            previous = {}
        created = previous.get("created_at")
        age = None
        if created:
            try:
                age = (now - datetime.fromisoformat(str(created))).total_seconds()
            except ValueError:
                age = None
        self.previous_stale = bool(previous) and previous.get("status") != "RELEASED" and (
            not _pid_alive(int(previous.get("pid", -1))) or age is None or age > self.stale_seconds
        )
        metadata = {
            "pid": os.getpid(),
            "run_id": run_id,
            "created_at": now.isoformat(),
            "process_start_marker": datetime.now(timezone.utc).isoformat(),
            "status": "ACTIVE",
        }
        self.handle.seek(0)
        self.handle.truncate()
        json.dump(metadata, self.handle, ensure_ascii=False)
        self.handle.flush()
        os.fsync(self.handle.fileno())
        return True

    def release(self) -> None:
        if self.handle is not None:
            try:
                self.handle.seek(0)
                metadata = json.loads(self.handle.read() or "{}")
                metadata["status"] = "RELEASED"
                metadata["released_at"] = datetime.now(timezone.utc).isoformat()
                self.handle.seek(0)
                self.handle.truncate()
                json.dump(metadata, self.handle, ensure_ascii=False)
                self.handle.flush()
                os.fsync(self.handle.fileno())
            except (OSError, json.JSONDecodeError):
                pass
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "ProcessLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    @classmethod
    def inspect(cls, path: str | Path, *, stale_seconds: int, now: datetime) -> dict[str, Any]:
        target = Path(path)
        if not target.is_file():
            return {"status": "ABSENT", "stale": False}
        try:
            metadata = json.loads(target.read_text(encoding="utf-8") or "{}")
            if metadata.get("status") == "RELEASED":
                return {"status": "RELEASED", "stale": False, "pid_alive": False, "age_seconds": 0.0}
            created = datetime.fromisoformat(str(metadata["created_at"]))
            age = (now - created).total_seconds()
            alive = _pid_alive(int(metadata["pid"]))
            return {
                "status": "ACTIVE" if alive and age <= stale_seconds else "STALE_METADATA",
                "stale": not alive or age > stale_seconds,
                "pid_alive": alive,
                "age_seconds": age,
            }
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return {"status": "INVALID_METADATA", "stale": True}
