from __future__ import annotations

import os
from pathlib import Path

OFFICIAL_KEY_NAME = "HITHINK_FINANCE_API_KEY"
COMPATIBLE_KEY_NAME = "HITHINK_API_KEY"


def _read_dotenv_value(path: Path, names: tuple[str, ...]) -> str | None:
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() not in names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            return value
    return None


def load_api_key(dotenv_path: str | Path = ".env") -> str | None:
    """Read a key without logging or mutating process environment."""
    for name in (OFFICIAL_KEY_NAME, COMPATIBLE_KEY_NAME):
        value = os.environ.get(name)
        if value:
            return value
    return _read_dotenv_value(
        Path(dotenv_path), (OFFICIAL_KEY_NAME, COMPATIBLE_KEY_NAME)
    )
