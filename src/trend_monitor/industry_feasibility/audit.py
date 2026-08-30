"""Deterministic, non-network TASK_012 capability audit."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from trend_monitor.schemas.industry_feasibility import IndustryMinuteFeasibilityResult


SHANGHAI = ZoneInfo("Asia/Shanghai")
ALLOWED_FINAL_JUDGMENTS = {
    "EXACT_SOURCE_AVAILABLE",
    "PROXY_SCHEME_PROMISING",
    "HISTORICAL_ONLY",
    "BLOCKED_BY_PERMISSION",
    "BLOCKED_BY_DATA",
}


class IndustryMinuteFeasibilityRules:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.validate()

    @classmethod
    def load(cls, path: str | Path) -> "IndustryMinuteFeasibilityRules":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        raw = self.raw
        if raw["rules_version"] != "industry_minute_feasibility_v0.1":
            raise ValueError("unexpected feasibility rules version")
        if raw["source_stock_rules_version"] != "stock_60m_risk_v0.1":
            raise ValueError("stock risk rules are not frozen")
        if raw["source_industry_context_rules_version"] != "stock_industry_context_v0.1":
            raise ValueError("industry context linkage changed")
        if raw["scoring_effect"] != "NONE" or raw["synthetic_benchmark_allowed"] is not False:
            raise ValueError("TASK_012 cannot score or construct synthetic benchmarks")
        canonical = raw["canonical_benchmarks"]
        proxies = raw["minute_proxy_candidates"]
        if set(canonical) != set(proxies) or len(canonical) != 2:
            raise ValueError("canonical/proxy instruments must be the same two formal stocks")
        for instrument_id, identity in canonical.items():
            if identity["provider"] != "hithink" or identity["taxonomy"] != "THS":
                raise ValueError(f"canonical identity changed: {instrument_id}")
            if identity["mapping_type"] != "EXACT" or identity["confidence"] != "HIGH":
                raise ValueError(f"canonical mapping changed: {instrument_id}")
            proxy = proxies[instrument_id]
            if proxy["taxonomy"] == identity["taxonomy"]:
                raise ValueError("proxy taxonomy must remain distinct from canonical THS")
            if proxy["mapping_type"] == "EXACT":
                raise ValueError("cross-taxonomy proxy cannot be marked EXACT")
            if proxy["mapping_type"] != "CANDIDATE_PROXY":
                raise ValueError("unvalidated SW benchmark must remain CANDIDATE_PROXY")


def _dotenv_has_value(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].strip().strip("'\"")
            return bool(value)
    return False


def credential_available(
    keys: tuple[str, ...] = ("TUSHARE_TOKEN", "TUSHARE_API_TOKEN"),
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    if any(bool(env.get(key, "").strip()) for key in keys):
        return True
    if dotenv_path is None:
        return False
    path = Path(dotenv_path)
    return any(_dotenv_has_value(path, key) for key in keys)


def redact_sensitive(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """Redact explicit secrets and token-like key/value text without logging credentials."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if "token" in str(key).lower() else redact_sensitive(item, secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item, secrets=secrets) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(
        r"(?i)(tushare(?:_api)?_token|token)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        result,
    )
    return result


def classify_tushare_error(
    error: BaseException | str,
    *,
    endpoint: str,
    ts_code: str | None = None,
    freq: str | None = None,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    message = str(redact_sensitive(str(error), secrets=secrets))
    permission_terms = ("permission", "权限", "积分", "access denied", "无权")
    status = (
        "BLOCKED_BY_TUSHARE_PERMISSION"
        if any(term in message.lower() for term in permission_terms)
        else "TUSHARE_API_ERROR"
    )
    return {
        "status": status,
        "endpoint": endpoint,
        "ts_code": ts_code,
        "freq": freq,
        "exception_type": type(error).__name__ if isinstance(error, BaseException) else "ProviderError",
        "message": message,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_feasibility_result(
    rules: IndustryMinuteFeasibilityRules,
    *,
    project_root: str | Path,
    evaluated_at: datetime,
    credential_present: bool,
) -> IndustryMinuteFeasibilityResult:
    root = Path(project_root)
    raw = rules.raw
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(SHANGHAI)
    credential_status = (
        "CREDENTIAL_PRESENT_API_VALIDATION_REQUIRED"
        if credential_present
        else "BLOCKED_BY_TUSHARE_CREDENTIALS"
    )
    blocked = {
        key: {
            "status": "NOT_COMPUTED",
            "reason": credential_status,
        }
        for key in raw["canonical_benchmarks"]
    }
    candidates = deepcopy(raw["minute_proxy_candidates"])
    for item in candidates.values():
        item.update(
            {
                "membership": "NOT_VERIFIED",
                "historical_membership": "DOCUMENTED_NOT_API_VERIFIED",
                "proxy_rating": "NOT_EVALUATED",
                "activation": "DISABLED",
            }
        )
    historical_minute = {
        "endpoint": raw["tushare"]["historical_minute_endpoint"],
        "documented_frequencies": raw["tushare"]["historical_frequencies"],
        "status": "NOT_API_VERIFIED",
        "reason": credential_status,
        "raw_evidence_saved": False,
        "daily_reconciliation": "NOT_COMPUTED",
    }
    return IndustryMinuteFeasibilityResult(
        schema_version=1,
        rules_version=raw["rules_version"],
        evaluated_at=evaluated_at.isoformat(),
        task_status="PARTIAL",
        final_judgment="BLOCKED_BY_PERMISSION",
        exact_ths_source={
            "status": "NOT_FOUND",
            "hithink": "QUOTE_DAILY_DIRECT_MINUTE_UNSUPPORTED",
            "tushare": "THS_IDENTITY_DAILY_MEMBERS_ONLY_NO_DOCUMENTED_THS_MINUTE",
            "other_providers": "NO_OFFICIAL_EXACT_THS_MINUTE_SOURCE_CONFIRMED",
        },
        canonical_benchmarks=deepcopy(raw["canonical_benchmarks"]),
        minute_proxy_candidates=candidates,
        credential_status=credential_status,
        membership=deepcopy(blocked),
        constituent_overlap=deepcopy(blocked),
        daily_correlation=deepcopy(blocked),
        historical_minute=historical_minute,
        realtime_capability={
            "endpoint": raw["tushare"]["realtime_endpoint"],
            "documented_type": "REALTIME_INDEX_SNAPSHOT",
            "direct_15m": False,
            "direct_60m": False,
            "api_status": "NOT_API_VERIFIED",
            "reason": credential_status,
        },
        boundary_snapshot_feasibility={
            "scheme": "BOUNDARY_SNAPSHOT_CLOSE",
            "research_only": True,
            "source_type": "BOUNDARY_SNAPSHOT_CLOSE",
            "boundaries": raw["tushare"]["boundary_times"],
            "status": "LIVE_BOUNDARY_VALIDATION_PENDING",
            "market_closed_at_evaluation": evaluated_at.weekday() >= 5,
            "trade_time_precision": "NOT_API_VERIFIED",
            "delay": "NOT_API_VERIFIED",
            "backfill": "NOT_DOCUMENTED_FOR_RT_SW_K",
            "offline_loss_risk": "PERIOD_CLOSE_CAN_BE_PERMANENTLY_MISSED_WITHOUT_A_BACKFILL_SOURCE",
        },
        provider_scorecard=deepcopy(raw["provider_scorecard"]),
        cost_permission={
            "automatic_purchase": False,
            "membership": {"requirement": "2000 points", "documented_price": "CNY 200/year"},
            "daily": {"requirement": "5000 points", "documented_price": "CNY 500/year"},
            "sw_historical_minute": {
                "requirement": "separate permission",
                "documented_price": "CNY 2000/year",
            },
            "sw_realtime_snapshot": {
                "requirement": "separate permission",
                "documented_price": "CNY 200/month",
            },
            "price_scope": "TUSHARE_PERSONAL_DOCUMENTATION; INSTITUTIONAL_TERMS_DIFFER",
        },
        recommended_data_scheme={
            "priority": "B_PENDING_VALIDATION",
            "historical": "TUSHARE_SW_MINS_DIRECT_15M_60M",
            "live": "TUSHARE_RT_SW_K_BOUNDARY_SNAPSHOT_CLOSE",
            "identity_model": "CANONICAL_HITHINK_THS_PLUS_SEPARATE_TUSHARE_SW2021_PROXY",
            "activation": "NOT_APPROVED",
            "required_next_evidence": [
                "membership_and_historical_membership",
                "current_constituent_overlap",
                "120_day_daily_proxy_metrics",
                "20_day_15m_60m_quality_and_daily_reconciliation",
                "live_boundary_capture_and_close_reconciliation",
            ],
        },
        industry_context_readiness="BLOCKED",
        synthetic_benchmark_created=False,
        stock_score_modified=False,
        frozen_stock_rules_sha256=_sha256(root / "config" / "stock_intraday_risk_rules.json"),
        sources=deepcopy(raw["official_sources"]),
    )


def validate_final_judgment(value: str) -> None:
    if value not in ALLOWED_FINAL_JUDGMENTS:
        raise ValueError("invalid TASK_012 final judgment")
