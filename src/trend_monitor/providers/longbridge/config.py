"""Longbridge credentials loaded without logging or environment mutation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_KEY = "LONGBRIDGE_APP_KEY"
APP_SECRET = "LONGBRIDGE_APP_SECRET"
ACCESS_TOKEN = "LONGBRIDGE_ACCESS_TOKEN"
REQUIRED_NAMES = (APP_KEY, APP_SECRET, ACCESS_TOKEN)


@dataclass(frozen=True, slots=True)
class LongbridgeCredentials:
    app_key: str
    app_secret: str
    access_token: str


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in REQUIRED_NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            result[key] = value
    return result


def load_credentials(dotenv_path: str | Path = ".env") -> LongbridgeCredentials | None:
    dotenv = _dotenv_values(Path(dotenv_path))
    values = {name: os.environ.get(name) or dotenv.get(name) for name in REQUIRED_NAMES}
    if not all(values.values()):
        return None
    return LongbridgeCredentials(
        app_key=str(values[APP_KEY]),
        app_secret=str(values[APP_SECRET]),
        access_token=str(values[ACCESS_TOKEN]),
    )
