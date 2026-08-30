"""Schemas for TASK_013 unattended runtime records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ScheduledPeriod:
    trading_date: str
    period_start: str
    period_end: str
    scheduled_at: str
    execution_mode: str
    notification_eligibility: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeRunRecord:
    run_id: str
    scheduled_period: dict[str, Any] | None
    started_at: str
    completed_at: str
    duration_seconds: float
    trading_date: str
    period_end: str | None
    status: str
    network_attempts: int
    market_result_id: str | None
    market_15m_result_id: str | None
    stock_result_ids: dict[str, str]
    error_summary: dict[str, Any] | None
    rules_versions: dict[str, str]
    execution_mode: str | None
    notification_eligibility: str | None
    combined_result_id: str | None = None
    industry_context: str = "DEFERRED"
    schema_version: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
