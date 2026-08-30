"""Fail-closed dotenv permission and presence audit."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Iterable


def dotenv_presence(path: str | Path, keys: Iterable[str]) -> dict[str, bool]:
    target = Path(path)
    values: dict[str, bool] = {key: bool(os.environ.get(key)) for key in keys}
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in values and value.strip().strip("'\""):
                values[key] = True
    return values


def audit_dotenv(path: str | Path, keys: Iterable[str]) -> dict[str, object]:
    target = Path(path)
    if not target.is_file():
        return {"status": "FAIL", "reason": "ENV_MISSING", "mode": None, "credentials": {}}
    mode = stat.S_IMODE(target.stat().st_mode)
    presence = dotenv_presence(target, keys)
    longbridge = all(presence.get(key, False) for key in (
        "LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"
    ))
    hithink = presence.get("HITHINK_FINANCE_API_KEY", False) or presence.get("HITHINK_API_KEY", False)
    status = "PASS" if mode == 0o600 and longbridge and hithink else "FAIL"
    reason = None
    if mode != 0o600:
        reason = "ENV_PERMISSION_MUST_BE_0600"
    elif not longbridge or not hithink:
        reason = "REQUIRED_CREDENTIAL_MISSING"
    return {
        "status": status,
        "reason": reason,
        "mode": oct(mode),
        "credentials": {"longbridge": "PRESENT" if longbridge else "MISSING", "hithink": "PRESENT" if hithink else "MISSING"},
    }
