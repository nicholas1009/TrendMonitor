"""Minimal Bark HTTP adapter with finite, secret-safe retries."""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from trend_monitor.schemas.notification import NotificationStatus

from .config import BarkConfig, NotificationPolicyConfig


@dataclass(frozen=True, slots=True)
class BarkHttpResult:
    status_code: int
    body: bytes


@dataclass(frozen=True, slots=True)
class BarkSendResult:
    status: NotificationStatus
    attempts: int
    error_category: str | None = None


class BarkTransportFailure(RuntimeError):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


Transport = Callable[[str, dict[str, str], float], BarkHttpResult]


def _default_transport(endpoint: str, payload: dict[str, str], timeout: float) -> BarkHttpResult:
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return BarkHttpResult(int(response.status), response.read(64_000))
    except HTTPError as exc:
        try:
            body = exc.read(64_000)
        except OSError:
            body = b""
        return BarkHttpResult(int(exc.code), body)
    except (socket.timeout, TimeoutError):
        raise BarkTransportFailure("TIMEOUT") from None
    except URLError as exc:
        category = "TIMEOUT" if isinstance(exc.reason, (socket.timeout, TimeoutError)) else "NETWORK_ERROR"
        raise BarkTransportFailure(category) from None
    except OSError:
        raise BarkTransportFailure("NETWORK_ERROR") from None


class BarkAdapter:
    def __init__(
        self,
        config: BarkConfig,
        policy_config: NotificationPolicyConfig,
        *,
        transport: Transport = _default_transport,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.policy_config = policy_config
        self.transport = transport
        self.sleeper = sleeper

    def send(self, *, title: str, body: str, group: str) -> BarkSendResult:
        error = self.config.validation_error()
        if not self.config.device_key:
            error = "MISSING_DEVICE_KEY"
        if error:
            return BarkSendResult(NotificationStatus.FAILED, 0, error)
        if not title.strip() or not body.strip() or not group.strip():
            return BarkSendResult(NotificationStatus.FAILED, 0, "SCHEMA_ERROR")

        endpoint = f"{self.config.server_url}/{quote(self.config.device_key, safe='')}"
        payload = {"title": title, "body": body, "group": group}
        attempts = 0
        while attempts < self.policy_config.max_attempts:
            attempts += 1
            try:
                response = self.transport(endpoint, payload, self.config.timeout_seconds)
            except BarkTransportFailure as exc:
                if attempts >= self.policy_config.max_attempts:
                    return BarkSendResult(NotificationStatus.FAILED, attempts, exc.category)
                self.sleeper(self.policy_config.backoff_seconds[attempts - 1])
                continue
            except Exception:
                return BarkSendResult(NotificationStatus.FAILED, attempts, "TRANSPORT_ERROR")

            if 500 <= response.status_code <= 599:
                if attempts >= self.policy_config.max_attempts:
                    return BarkSendResult(NotificationStatus.FAILED, attempts, "HTTP_5XX")
                self.sleeper(self.policy_config.backoff_seconds[attempts - 1])
                continue
            if 400 <= response.status_code <= 499:
                return BarkSendResult(NotificationStatus.FAILED, attempts, "HTTP_4XX")
            if not 200 <= response.status_code <= 299:
                return BarkSendResult(NotificationStatus.FAILED, attempts, "HTTP_STATUS_ERROR")

            try:
                value = json.loads(response.body.decode("utf-8")) if response.body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = {}
            if isinstance(value, dict) and value.get("code") not in (None, 200):
                return BarkSendResult(NotificationStatus.FAILED, attempts, "API_REJECTED")
            return BarkSendResult(NotificationStatus.SENT, attempts)

        return BarkSendResult(NotificationStatus.FAILED, attempts, "RETRY_EXHAUSTED")
