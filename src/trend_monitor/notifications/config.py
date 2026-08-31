"""Secret-safe Bark and notification policy configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off", ""}


def _dotenv_values(path: str | Path) -> dict[str, str]:
    target = Path(path)
    if not target.is_file():
        return {}
    values: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError("BARK_ENABLED must be true or false")


@dataclass(frozen=True, slots=True, repr=False)
class BarkConfig:
    enabled: bool
    server_url: str
    device_key: str = field(repr=False)
    timeout_seconds: float = 10.0
    config_error: str | None = None

    @classmethod
    def load(
        cls,
        dotenv_path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> "BarkConfig":
        local = _dotenv_values(dotenv_path)
        env = os.environ if environ is None else environ

        def value(key: str, default: str = "") -> str:
            return str(env.get(key, local.get(key, default))).strip()

        enabled_value = value("BARK_ENABLED", "false")
        try:
            enabled = _enabled(enabled_value)
            config_error = None
        except ValueError:
            enabled = False
            config_error = "INVALID_ENABLED_VALUE"
        return cls(
            enabled=enabled,
            server_url=value("BARK_SERVER_URL", "https://api.day.app").rstrip("/"),
            device_key=value("BARK_DEVICE_KEY"),
            timeout_seconds=float(timeout_seconds),
            config_error=config_error,
        )

    def validation_error(self) -> str | None:
        if self.config_error:
            return self.config_error
        parsed = urlsplit(self.server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "INVALID_SERVER_URL"
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return "INVALID_SERVER_URL"
        if self.enabled and not self.device_key:
            return "MISSING_DEVICE_KEY"
        if self.timeout_seconds <= 0:
            return "INVALID_TIMEOUT"
        return None

    def safe_summary(self) -> dict[str, object]:
        parsed = urlsplit(self.server_url)
        return {
            "enabled": self.enabled,
            "server": f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "INVALID",
            "device_key": "PRESENT" if self.device_key else "MISSING",
            "valid": self.validation_error() is None,
            "error": self.validation_error(),
        }


@dataclass(frozen=True, slots=True)
class NotificationPolicyConfig:
    rules_version: str
    group: str
    max_attempts: int
    backoff_seconds: tuple[float, ...]
    catch_up_risk_notifications: bool
    catch_up_error_notifications: bool

    @classmethod
    def load(cls, path: str | Path) -> "NotificationPolicyConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        config = cls(
            rules_version=str(raw["rules_version"]),
            group=str(raw["group"]),
            max_attempts=int(raw["retry"]["max_attempts"]),
            backoff_seconds=tuple(float(value) for value in raw["retry"]["backoff_seconds"]),
            catch_up_risk_notifications=bool(raw["catch_up"]["risk_notifications"]),
            catch_up_error_notifications=bool(raw["catch_up"]["error_notifications"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.rules_version != "notification_policy_v0.1":
            raise ValueError("unexpected notification policy rules version")
        if not self.group:
            raise ValueError("notification group is required")
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("Bark retry attempts must be between 1 and 3")
        if len(self.backoff_seconds) < self.max_attempts - 1:
            raise ValueError("Bark retry backoff list is incomplete")
