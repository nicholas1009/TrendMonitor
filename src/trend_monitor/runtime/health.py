"""Read-only unattended runtime health checks."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .lock import ProcessLock
from .security import audit_dotenv


LAUNCH_AGENT_LABEL = "com.trendmonitor.local.intraday"


def _launchctl(args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, type(exc).__name__


def _launchd_value(output: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)} = (?P<value>.+)$", output, re.MULTILINE)
    return match.group("value").strip() if match else None


def _heartbeat(root: Path, now: datetime) -> dict[str, Any]:
    path = root / "data" / "runtime" / "launchd_heartbeat.json"
    if not path.is_file():
        return {"status": "MISSING", "path": str(path), "observed_at": None, "age_seconds": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(payload["observed_at"]))
        if observed.tzinfo is None:
            raise ValueError("heartbeat timestamp must be timezone-aware")
        return {
            "status": "OBSERVED",
            "path": str(path),
            "observed_at": observed.isoformat(),
            "age_seconds": max(0.0, (now - observed.astimezone(now.tzinfo)).total_seconds()),
            "label": payload.get("label"),
            "pid": payload.get("pid"),
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"status": "INVALID", "path": str(path), "observed_at": None, "age_seconds": None}


def _latest_launch_observation(store: Any) -> dict[str, Any]:
    try:
        records = [
            item
            for item in store.entries()
            if (item.get("extra") or {}).get("trigger_source") == "LAUNCHD"
        ]
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        records = []
    if not records:
        return {"status": "NONE"}
    latest = max(records, key=lambda item: str(item.get("started_at") or ""))
    return {
        "status": "OBSERVED",
        "started_at": latest.get("started_at"),
        "run_status": latest.get("status"),
        "run_id": latest.get("run_id"),
        "execution_mode": latest.get("execution_mode"),
    }


def _restart_recovery_status(root: Path) -> str:
    path = root / "data" / "runtime" / "acceptance" / "runtime_live_acceptance_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (payload.get("restart_recovery") or {}).get("status") == "PASS":
            return "VERIFIED"
    except (OSError, TypeError, json.JSONDecodeError):
        pass
    return "IMPLEMENTED_PENDING_RESTART_TEST"


def inspect_launch_agent(
    project_root: str | Path,
    store: Any,
    *,
    now: datetime,
    installed_path: str | Path | None = None,
    uid: int | None = None,
    expected_interval: int = 60,
) -> dict[str, Any]:
    """Inspect the current GUI-domain lifecycle without changing launchd state."""

    root = Path(project_root).resolve()
    installed = Path(installed_path) if installed_path else (
        Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    )
    domain = f"gui/{os.getuid() if uid is None else uid}"
    print_code, print_output = _launchctl(
        ["print", f"{domain}/{LAUNCH_AGENT_LABEL}"]
    )
    disabled_code, disabled_output = _launchctl(["print-disabled", domain])
    disabled_match = re.search(
        rf'"{re.escape(LAUNCH_AGENT_LABEL)}"\s*=>\s*(enabled|disabled)',
        disabled_output,
    )
    disabled_state = (
        disabled_match.group(1).upper()
        if disabled_match
        else "DEFAULT_ENABLED"
        if disabled_code == 0
        else "UNKNOWN"
    )
    program = _launchd_value(print_output, "program")
    working_directory = _launchd_value(print_output, "working directory")
    interval = _launchd_value(print_output, "run interval")
    interval_match = re.match(r"(\d+) seconds", interval or "")
    run_interval = int(interval_match.group(1)) if interval_match else None
    last_exit = _launchd_value(print_output, "last exit code")
    runs = _launchd_value(print_output, "runs")
    runtime_log = root / "logs" / "runtime" / "intraday_monitor.log"
    log_writable = (
        os.access(runtime_log, os.W_OK)
        if runtime_log.exists()
        else runtime_log.parent.is_dir() and os.access(runtime_log.parent, os.W_OK)
    )
    loaded = print_code == 0
    disabled = disabled_state == "DISABLED"
    program_exists = bool(program and Path(program).exists())
    working_directory_matches = working_directory == str(root)
    interval_matches = run_interval == expected_interval
    reason = None
    if not installed.is_file():
        reason = "LAUNCH_AGENT_PLIST_MISSING"
    elif disabled:
        reason = "LAUNCH_AGENT_DISABLED"
    elif disabled_state == "UNKNOWN":
        reason = "LAUNCH_AGENT_DISABLED_STATE_UNKNOWN"
    elif not loaded:
        reason = "LAUNCH_AGENT_NOT_LOADED"
    elif not program_exists:
        reason = "LAUNCH_AGENT_PROGRAM_MISSING"
    elif not working_directory_matches:
        reason = "LAUNCH_AGENT_WORKING_DIRECTORY_MISMATCH"
    elif not interval_matches:
        reason = "LAUNCH_AGENT_INTERVAL_MISMATCH"
    elif not log_writable:
        reason = "RUNTIME_LOG_NOT_WRITABLE"
    return {
        "status": "PASS" if reason is None else "FAIL",
        "reason": reason,
        "label": LAUNCH_AGENT_LABEL,
        "domain": domain,
        "plist_exists": installed.is_file(),
        "plist_path": str(installed),
        "loaded": loaded,
        "disabled": disabled if disabled_state != "UNKNOWN" else None,
        "disabled_state": disabled_state,
        "state": _launchd_value(print_output, "state"),
        "last_exit_code": int(last_exit) if last_exit and last_exit.lstrip("-").isdigit() else None,
        "runs": int(runs) if runs and runs.isdigit() else None,
        "run_interval_seconds": run_interval,
        "run_interval_matches": interval_matches,
        "working_directory": working_directory,
        "working_directory_matches": working_directory_matches,
        "program": program,
        "program_exists": program_exists,
        "runtime_log": str(runtime_log),
        "runtime_log_writable": log_writable,
        "last_runner_heartbeat": _heartbeat(root, now),
        "last_launch_observation": _latest_launch_observation(store),
        "restart_recovery": _restart_recovery_status(root),
    }


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
    checks["launchd"] = inspect_launch_agent(
        root,
        store,
        now=now,
        expected_interval=int(config.raw["launchd_poll_seconds"]),
    )
    checks["launchd"]["template"] = "PASS" if template.is_file() else "FAIL"
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
        checks["launchd"]["status"] == "PASS",
        checks["disk"]["status"] == "PASS",
    ]
    return {
        "status": "PASS" if all(critical) else "FAIL",
        "checked_at": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "checks": checks,
        "secrets_exposed": False,
    }
