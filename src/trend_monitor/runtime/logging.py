"""Rotating runtime logs with credential redaction."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
from typing import Iterable, Mapping


def dotenv_secret_values(path: str | Path, keys: Iterable[str], environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    env = os.environ if environ is None else environ
    wanted = set(keys)
    values = [env[key] for key in wanted if env.get(key)]
    target = Path(path)
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() in wanted:
                value = value.strip().strip("'\"")
                if value:
                    values.append(value)
    return tuple(dict.fromkeys(values))


def redact_text(value: str, secrets: Iterable[str]) -> str:
    output = value
    for secret in secrets:
        if secret:
            output = output.replace(secret, "[REDACTED]")
    return re.sub(
        r"(?i)(app[_-]?secret|access[_-]?token|api[_-]?key|token)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        output,
    )


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str]):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record), self.secrets)


def runtime_logger(path: str | Path, *, secrets: Iterable[str], max_bytes: int = 2_000_000, backups: int = 10) -> logging.Logger:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"trend_monitor.runtime.{target}")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(RedactingFormatter(secrets))
    logger.addHandler(handler)
    return logger
