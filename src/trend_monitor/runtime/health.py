"""Read-only unattended runtime health checks."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .lock import ProcessLock
from .security import audit_dotenv


def check_runtime_health(project_root: str | Path, config: Any, calendar: Any, store: Any, *, now: datetime) -> dict[str, Any]:
    root = Path(project_root).resolve()
    env = audit_dotenv(root / ".env", config.raw["secret_keys"])
    checks: dict[str, Any] = {
        "project_path": {"status": "PASS" if root.is_dir() else "FAIL", "path": str(root)},
        "env": env,
    }
    try:
        uv = subprocess.run(["/usr/local/bin/uv", "--version"], capture_output=True, text=True, timeout=5)
        checks["uv_python"] = {"status": "PASS" if uv.returncode == 0 else "FAIL", "uv": (uv.stdout or "").strip()}
    except (OSError, subprocess.SubprocessError):
        checks["uv_python"] = {"status": "FAIL", "uv": "UNAVAILABLE"}
    paths = {
        "raw_cache": root / "data" / "raw",
        "risk_snapshots": root / "data" / "risk_outputs",
        "runtime": root / "data" / "runtime",
        "logs": root / "logs" / "runtime",
    }
    checks["writable_paths"] = {
        key: "PASS" if path.parent.exists() and os.access(path.parent, os.W_OK) else "FAIL"
        for key, path in paths.items()
    }
    snapshot = calendar.load()
    checks["calendar"] = {
        "status": "PASS" if snapshot is not None else "MISSING",
        "provider": snapshot.get("provider") if snapshot else None,
        "authoritative_through": snapshot.get("authoritative_through") if snapshot else None,
    }
    template = root / "config" / "launchd" / "com.trendmonitor.local.intraday.plist"
    installed = Path.home() / "Library" / "LaunchAgents" / "com.trendmonitor.local.intraday.plist"
    checks["launchd"] = {
        "template": "PASS" if template.is_file() else "FAIL",
        "installed": "PASS" if installed.is_file() else "MISSING",
    }
    checks["latest_success"] = store.latest_success() or {"status": "NONE"}
    checks["lock"] = ProcessLock.inspect(
        root / "data" / "runtime" / "runner.lock",
        stale_seconds=int(config.raw["lock_stale_seconds"]),
        now=now,
    )
    disk = shutil.disk_usage(root)
    checks["disk"] = {
        "status": "PASS" if disk.free >= 500 * 1024 * 1024 else "LOW",
        "free_bytes": disk.free,
    }
    critical = [
        checks["project_path"]["status"] == "PASS",
        env["status"] == "PASS",
        checks["uv_python"]["status"] == "PASS",
        all(value == "PASS" for value in checks["writable_paths"].values()),
        checks["launchd"]["template"] == "PASS",
        checks["disk"]["status"] == "PASS",
    ]
    return {
        "status": "PASS" if all(critical) else "FAIL",
        "checked_at": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "checks": checks,
        "secrets_exposed": False,
    }
