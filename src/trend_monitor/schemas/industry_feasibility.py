"""TASK_012 schemas: benchmark identity and minute data feasibility."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
BOUNDARIES = {"10:30", "11:30", "14:00", "15:00"}


@dataclass(frozen=True, slots=True)
class BenchmarkIdentity:
    provider: str
    taxonomy: str
    provider_symbol: str
    name: str
    mapping_type: str
    confidence: str


@dataclass(frozen=True, slots=True)
class BoundarySnapshotClose:
    requested_boundary: str
    provider_trade_time: str
    fetched_at: str
    close: float
    source_provider: str
    source_raw_path: str
    delay_seconds: float
    source_type: str = "BOUNDARY_SNAPSHOT_CLOSE"

    def validate(self) -> None:
        if self.source_type != "BOUNDARY_SNAPSHOT_CLOSE":
            raise ValueError("boundary snapshot must not masquerade as a direct bar")
        if self.requested_boundary not in BOUNDARIES:
            raise ValueError("unsupported industry boundary")
        trade_time = datetime.fromisoformat(self.provider_trade_time)
        fetched_at = datetime.fromisoformat(self.fetched_at)
        if trade_time.tzinfo is None or fetched_at.tzinfo is None:
            raise ValueError("provider and fetch timestamps must be timezone-aware")
        if trade_time.astimezone(SHANGHAI).utcoffset() != SHANGHAI.utcoffset(trade_time):
            raise ValueError("provider timestamp cannot be normalized to Asia/Shanghai")
        actual_delay = (fetched_at - trade_time).total_seconds()
        if actual_delay < 0 or abs(actual_delay - self.delay_seconds) > 0.001:
            raise ValueError("invalid boundary snapshot delay")
        if not self.source_raw_path or self.close <= 0:
            raise ValueError("boundary snapshot requires positive close and raw provenance")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IndustryMinuteFeasibilityResult:
    schema_version: int
    rules_version: str
    evaluated_at: str
    task_status: str
    final_judgment: str
    exact_ths_source: dict[str, Any]
    canonical_benchmarks: dict[str, Any]
    minute_proxy_candidates: dict[str, Any]
    credential_status: str
    membership: dict[str, Any]
    constituent_overlap: dict[str, Any]
    daily_correlation: dict[str, Any]
    historical_minute: dict[str, Any]
    realtime_capability: dict[str, Any]
    boundary_snapshot_feasibility: dict[str, Any]
    provider_scorecard: list[dict[str, Any]]
    cost_permission: dict[str, Any]
    recommended_data_scheme: dict[str, Any]
    industry_context_readiness: str
    synthetic_benchmark_created: bool
    stock_score_modified: bool
    frozen_stock_rules_sha256: str
    sources: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
