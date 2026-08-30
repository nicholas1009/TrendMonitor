#!/usr/bin/env python3
"""Install/status/remove the TASK_013 per-user LaunchAgent without secrets."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.trendmonitor.local.intraday"
TEMPLATE = ROOT / "config" / "launchd" / f"{LABEL}.plist"
DESTINATION = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


def render() -> bytes:
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("__PROJECT_ROOT__", str(ROOT)).replace("__UV_PATH__", "/usr/local/bin/uv")
    payload = plistlib.loads(text.encode("utf-8"))
    if payload["Label"] != LABEL or any("SECRET" in str(value).upper() for value in payload.values()):
        raise ValueError("invalid or secret-bearing launchd template")
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def status() -> int:
    result = subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"], capture_output=True, text=True)
    print("INSTALLED", DESTINATION.is_file())
    print("LOADED", result.returncode == 0)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if any(key in line for key in ("state =", "pid =", "last exit code =", "runs =")):
                print(line.strip())
    return 0 if DESTINATION.is_file() and result.returncode == 0 else 1


def install() -> int:
    (ROOT / "logs" / "runtime").mkdir(parents=True, exist_ok=True)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    temporary = ROOT / "data" / "runtime" / f"{LABEL}.rendered.plist"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(render())
    shutil.copyfile(temporary, DESTINATION)
    os.chmod(DESTINATION, 0o644)
    subprocess.run(["launchctl", "bootout", DOMAIN, str(DESTINATION)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", DOMAIN, str(DESTINATION)], capture_output=True, text=True)
    if result.returncode != 0:
        print("BOOTSTRAP FAILED", result.stderr.strip())
        return result.returncode
    subprocess.run(["launchctl", "enable", f"{DOMAIN}/{LABEL}"], check=True)
    return status()


def uninstall() -> int:
    subprocess.run(["launchctl", "bootout", DOMAIN, str(DESTINATION)], capture_output=True)
    if DESTINATION.exists():
        DESTINATION.unlink()
    print("REMOVED", not DESTINATION.exists())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
