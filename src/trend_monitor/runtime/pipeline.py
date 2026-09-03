"""Existing deterministic pipeline orchestration and replay-backed reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from .logging import redact_text


class RuntimeStageError(RuntimeError):
    def __init__(
        self,
        category: str,
        stage: str,
        message: str,
        *,
        command: list[str] | None = None,
        exit_code: int | None = None,
        duration_seconds: float | None = None,
        stdout_tail: str | None = None,
        stderr_tail: str | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.attempts = 1
        self.command = tuple(command or ())
        self.exit_code = exit_code
        self.duration_seconds = duration_seconds
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail


def classify_stage_failure(text: str) -> str:
    upper = text.upper()
    if "TEMPORARY_PROVIDER_ERROR" in upper:
        return "TEMPORARY_PROVIDER_ERROR"
    deterministic = {
        "UNMAPPED": "INVALID_MAPPING",
        "UNSUPPORTED": "UNSUPPORTED",
        "PERMISSION": "PERMISSION_REQUIRED",
        "AUTH_ERROR": "PERMISSION_REQUIRED",
        "INVALID_DATA": "SCHEMA_OR_CONTRACT_ERROR",
        "CONTRACT": "SCHEMA_OR_CONTRACT_ERROR",
    }
    for token, category in deterministic.items():
        if token in upper:
            return category
    if "TIMEOUT" in upper or "TIMED OUT" in upper:
        return "TIMEOUT"
    if "NETWORK_ERROR" in upper or "CONNECTION" in upper or "TEMPORARY" in upper:
        return "NETWORK_ERROR"
    if "RATE_LIMIT" in upper or "429" in upper:
        return "RATE_LIMIT"
    return "PIPELINE_FAILED"


def retry_action(
    action: Callable[[], Any],
    *,
    max_attempts: int,
    backoff_seconds: list[float],
    retryable_categories: set[str],
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[Any, int]:
    attempts = 0
    while True:
        attempts += 1
        try:
            return action(), attempts
        except RuntimeStageError as exc:
            if exc.category not in retryable_categories or attempts >= max_attempts:
                exc.attempts = attempts
                raise
            sleeper(backoff_seconds[attempts - 1])


@dataclass(frozen=True, slots=True)
class PipelineRefreshResult:
    attempts: int
    stages: tuple[dict[str, Any], ...]


class SubprocessMonitorPipeline:
    def __init__(self, project_root: str | Path, config: Any, logger: logging.Logger, *, secrets: tuple[str, ...]):
        self.root = Path(project_root).resolve()
        self.config = config
        self.logger = logger
        self.secrets = secrets

    def refresh(self, *, as_of: datetime) -> PipelineRefreshResult:
        stages = []
        total_attempts = 0
        retry = self.config.raw["retry"]
        for definition in self.config.raw["pipeline_stages"]:
            name = definition["name"]
            script = self.root / definition["script"]

            def run_stage() -> dict[str, Any]:
                self.logger.info("stage=%s status=STARTED as_of=%s", name, as_of.isoformat())
                command = [sys.executable, str(script)]
                if name in {"MARKET_DATA_REFRESH", "RISK_INPUT_ASSEMBLY"}:
                    command.extend(["--as-of", as_of.isoformat()])
                stage_started = time.monotonic()
                try:
                    completed = subprocess.run(
                        command,
                        cwd=self.root,
                        capture_output=True,
                        text=True,
                        timeout=int(self.config.raw["stage_timeout_seconds"]),
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    duration = time.monotonic() - stage_started
                    stdout = redact_text(str(exc.stdout or ""), self.secrets)[-2000:]
                    stderr = redact_text(str(exc.stderr or ""), self.secrets)[-2000:]
                    message = f"stage timeout after {duration:.3f}s: {name}"
                    self.logger.error(
                        "stage=%s status=FAILED category=TIMEOUT recoverable=true "
                        "exit_code=None duration=%.3f error=%s",
                        name,
                        duration,
                        message,
                    )
                    raise RuntimeStageError(
                        "TIMEOUT",
                        name,
                        message,
                        command=command,
                        duration_seconds=duration,
                        stdout_tail=stdout,
                        stderr_tail=stderr,
                    ) from exc
                duration = time.monotonic() - stage_started
                stdout = redact_text(completed.stdout or "", self.secrets)
                stderr = redact_text(completed.stderr or "", self.secrets)
                output = (stdout + "\n" + stderr).strip()
                if completed.returncode != 0:
                    category = classify_stage_failure(output)
                    recoverable = category in set(retry["retryable_categories"])
                    summary = output[-4000:]
                    self.logger.error(
                        "stage=%s status=FAILED category=%s recoverable=%s "
                        "exit_code=%s duration=%.3f error=%s",
                        name,
                        category,
                        str(recoverable).lower(),
                        completed.returncode,
                        duration,
                        summary[-1000:].replace("\n", " | "),
                    )
                    raise RuntimeStageError(
                        category,
                        name,
                        summary,
                        command=command,
                        exit_code=completed.returncode,
                        duration_seconds=duration,
                        stdout_tail=stdout[-2000:],
                        stderr_tail=stderr[-2000:],
                    )
                self.logger.info("stage=%s status=PASS output=%s", name, output[-2000:].replace("\n", " | "))
                return {
                    "stage": name,
                    "status": "PASS",
                    "returncode": 0,
                    "duration_seconds": duration,
                    "command": command,
                }

            result, attempts = retry_action(
                run_stage,
                max_attempts=int(retry["max_attempts"]),
                backoff_seconds=[float(value) for value in retry["backoff_seconds"]],
                retryable_categories=set(retry["retryable_categories"]),
            )
            total_attempts += attempts
            stages.append(dict(result, attempts=attempts))
        return PipelineRefreshResult(total_attempts, tuple(stages))


class RuntimeSnapshotReader:
    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()

    def _load(self, relative: str) -> dict[str, Any]:
        value = json.loads((self.root / relative).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid runtime source report: {relative}")
        return value

    def load_period(self, period_end: str) -> dict[str, Any]:
        market_report = self._load("data/reports/market_60m_replay_latest.json")
        internal_report = self._load("data/reports/market_15m_internal_latest.json")
        stock_report = self._load("data/reports/stock_intraday_risk_latest.json")
        market = next(
            (item for item in market_report.get("results", []) if item.get("last_completed_bar_end") == period_end),
            None,
        )
        market15 = next(
            (item for item in internal_report.get("results", []) if item.get("60m_period_end") == period_end),
            None,
        )
        stocks = {}
        for instrument_id, values in stock_report.get("results", {}).items():
            item = next(
                (value for value in values if value.get("stock_60m", {}).get("period_end") == period_end),
                None,
            )
            if item is not None:
                stocks[instrument_id] = item
        if market is None or market15 is None or len(stocks) != 2:
            raise ValueError(f"RUNTIME_SNAPSHOT_UNAVAILABLE: {period_end}")
        if market.get("rules_version") != "market_60m_risk_v0.1":
            raise ValueError("market rules version changed")
        if market15.get("rules_version") != "market_15m_internal_v0.1":
            raise ValueError("market 15m rules version changed")
        for value in stocks.values():
            if value["stock_60m"].get("rules_version") != "stock_60m_risk_v0.1":
                raise ValueError("stock rules version changed")
            if value["stock_15m"].get("rules_version") != "stock_15m_internal_v0.1":
                raise ValueError("stock 15m rules version changed")
        source_ids = {
            "market_result_id": (
                market_report.get("current_machine_path")
                if market_report.get("results", [])[-1].get("last_completed_bar_end") == period_end
                else f"{market_report.get('append_only_replay_path')}#period={period_end}"
            ),
            "market_15m_result_id": (
                internal_report.get("current_machine_path")
                if internal_report.get("results", [])[-1].get("60m_period_end") == period_end
                else f"{internal_report.get('append_only_replay_path')}#period={period_end}"
            ),
            "stock_result_ids": {
                instrument_id: f"{stock_report.get('append_only_replay_path')}#instrument={instrument_id}&period={period_end}"
                for instrument_id in stocks
            },
        }
        return {
            "market": market,
            "market_15m": market15,
            "stocks": stocks,
            "source_ids": source_ids,
            "source_safety": {
                "market_lookahead": market_report.get("lookahead_safe") is True,
                "market_15m_lookahead": internal_report.get("lookahead_safe") is True,
                "stock_lookahead": stock_report.get("lookahead_safe") is True,
                "stock_score_immutable": stock_report.get("score_immutable") is True,
            },
        }

    def load_previous_period(self, period_end: str) -> dict[str, Any] | None:
        market_report = self._load("data/reports/market_60m_replay_latest.json")
        candidates = sorted(
            str(item["last_completed_bar_end"])
            for item in market_report.get("results", [])
            if item.get("last_completed_bar_end") and str(item["last_completed_bar_end"]) < period_end
        )
        return self.load_period(candidates[-1]) if candidates else None


def build_combined_result(source: dict[str, Any], *, scheduled_period: Any, generated_at: datetime) -> dict[str, Any]:
    market = source["market"]
    market15 = source["market_15m"]
    available = int(market.get("data_quality", {}).get("valid_index_count", 0))
    stocks = {}
    for instrument_id, item in source["stocks"].items():
        risk, internal = item["stock_60m"], item["stock_15m"]
        stocks[instrument_id] = {
            "symbol": risk.get("symbol"),
            "name": risk.get("name"),
            "risk_score": risk.get("risk_score"),
            "risk_light": risk.get("risk_light"),
            "risk_direction": risk.get("risk_direction"),
            "confidence": risk.get("confidence"),
            "current_return": risk.get("current_return"),
            "previous_return": risk.get("previous_return"),
            "two_period_return": risk.get("two_period_return"),
            "relative_return": risk.get("relative_return"),
            "market_relationship": risk.get("market_relationship"),
            "persistent_weakness": risk.get("persistent_weakness"),
            "downside_shock": risk.get("downside_shock"),
            "relative_weakness": risk.get("relative_weakness"),
            "market_resonance": risk.get("market_resonance"),
            "repair_state": risk.get("repair_state"),
            "score_components": risk.get("score_components"),
            "15m_classification": internal.get("classification"),
            "15m_direction_sequence": internal.get("direction_sequence"),
            "15m_joint_market_flags": internal.get("joint_market_flags"),
        }
    lookahead = all(source["source_safety"].values())
    lookahead = lookahead and bool(market15.get("data_quality", {}).get("lookahead_safe"))
    lookahead = lookahead and all(
        bool(item["stock_60m"].get("data_quality", {}).get("lookahead_safe"))
        and bool(item["stock_15m"].get("data_quality", {}).get("lookahead_safe"))
        for item in source["stocks"].values()
    )
    if not lookahead:
        raise ValueError("LOOKAHEAD_SAFETY_FAILED")
    if market.get("last_completed_bar_end") != scheduled_period.period_end:
        raise ValueError("market period mismatch")
    status = "SUCCESS" if available == 8 else "SUCCESS_WITH_DEGRADATION" if available >= 6 else "DATA_INCOMPLETE"
    if any(value != "PASS" for value in market.get("data_quality", {}).get("preflight", {}).values()):
        status = "SUCCESS_WITH_DEGRADATION" if status == "SUCCESS" else status
    if market.get("signal_confidence") != "HIGH" or any(v.get("confidence") != "HIGH" for v in stocks.values()):
        status = "SUCCESS_WITH_DEGRADATION" if status == "SUCCESS" else status
    return {
        "schema_version": 1,
        "runtime_rules_version": "intraday_runtime_v0.1",
        "generated_at": generated_at.isoformat(),
        "trading_date": scheduled_period.trading_date,
        "period_start": scheduled_period.period_start,
        "period_end": scheduled_period.period_end,
        "execution_mode": scheduled_period.execution_mode,
        "notification_eligibility": scheduled_period.notification_eligibility,
        "status": status,
        "market": {
            "risk_score": market.get("risk_score"),
            "risk_light": market.get("risk_light"),
            "risk_direction": market.get("risk_direction"),
            "confidence": market.get("signal_confidence"),
            "breadth": market.get("breadth"),
            "persistent_weakness": market.get("persistent_weakness"),
            "downside_shocks": market.get("downside_shocks"),
            "weighted_support_distortion": market.get("weighted_support_distortion"),
            "small_cap_stress": market.get("small_cap_stress"),
            "broad_selloff_resonance": market.get("broad_selloff_resonance"),
            "strong_broad_weakness": market.get("strong_broad_weakness"),
            "broad_repair": market.get("broad_repair"),
            "repair_count": market.get("repair_count"),
            "score_components": market.get("score_components"),
            "15m_internal": market15.get("market_internal_state"),
        },
        "stocks": stocks,
        "data": {
            "market_index_coverage": f"{available}/8",
            "industry_context": "DEFERRED",
            "lookahead_safe": lookahead,
        },
        "source_ids": source["source_ids"],
        "rules_versions": {
            "market_60m": "market_60m_risk_v0.1",
            "market_15m": "market_15m_internal_v0.1",
            "stock_60m": "stock_60m_risk_v0.1",
            "stock_15m": "stock_15m_internal_v0.1",
        },
        "contains_trading_advice": False,
    }


def render_combined_report(value: dict[str, Any]) -> str:
    market = value["market"]
    lines = [
        "# A股60分钟监控",
        "",
        f"周期：{value['period_end']}",
        f"执行模式：{value['execution_mode']}",
        "",
        "## 大盘",
        "",
        f"Risk Light：{market['risk_light']} / Score {market['risk_score']} / {market['risk_direction']}",
        f"Score Components：{market['score_components']}",
        f"Persistent / Shock：{market['persistent_weakness']} / {market['downside_shocks']}",
        f"Breadth Selloff / Broad Repair：{market['broad_selloff_resonance']} / {market['broad_repair']}",
        f"15m Internal：{market['15m_internal']}",
    ]
    for instrument_id, stock in value["stocks"].items():
        lines.extend(
            [
                "",
                f"## {stock['name'] or instrument_id}",
                "",
                f"Risk Light：{stock['risk_light']} / Score {stock['risk_score']}",
                f"Score Components：{stock['score_components']}",
                f"Return / Relative：{stock['current_return']} / {stock['relative_return']}",
                f"Market Relationship / Resonance：{stock['market_relationship']} / {stock['market_resonance']}",
                f"15m：{stock['15m_classification']}",
                f"15m Joint Flags：{stock['15m_joint_market_flags']}",
            ]
        )
    lines.extend(
        [
            "",
            "## 数据",
            "",
            f"Market Coverage：{value['data']['market_index_coverage']}",
            "Industry：DEFERRED",
            "",
            "本结果仅用于盘中风险监控，不构成交易建议。",
            "",
        ]
    )
    return "\n".join(lines)
