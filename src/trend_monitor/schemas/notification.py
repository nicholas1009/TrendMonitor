"""Stable schemas for the TASK_014 notification layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
from typing import Any


NOTIFICATION_SCHEMA_VERSION = 1


class NotificationSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    ERROR = "ERROR"


class NotificationStatus(StrEnum):
    SENT = "SENT"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    SKIPPED_POLICY = "SKIPPED_POLICY"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    FAILED = "FAILED"
    WOULD_SEND = "WOULD_SEND"


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    event_type: str
    instrument_id: str
    trading_date: str
    period_end: str
    rules_version: str
    severity: NotificationSeverity
    title: str
    body: str
    execution_mode: str
    source_result_id: str
    group: str = "TrendMonitor"
    schema_version: int = NOTIFICATION_SCHEMA_VERSION

    @property
    def event_key(self) -> str:
        identity = "|".join(
            (
                self.event_type,
                self.instrument_id,
                self.trading_date,
                self.period_end,
                self.rules_version,
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["severity"] = self.severity.value
        result["event_key"] = self.event_key
        return result


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    notification_id: str
    event_key: str
    event_type: str
    instrument_id: str
    trading_date: str
    period_end: str
    rules_version: str
    severity: NotificationSeverity
    status: NotificationStatus
    attempts: int
    created_at: str
    sent_at: str | None
    execution_mode: str
    source_result_id: str
    error_category: str | None = None
    schema_version: int = NOTIFICATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["severity"] = self.severity.value
        result["status"] = self.status.value
        return result
