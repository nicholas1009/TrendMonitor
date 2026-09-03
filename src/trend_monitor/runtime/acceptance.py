"""Read-only TASK_013A production acceptance evidence analysis.

The evaluator never invokes the risk runner.  Its only writes are append-only
acceptance observations and a convenience copy of the latest observation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
LOCAL = datetime.now().astimezone().tzinfo
SUCCESS = {"SUCCESS", "SUCCESS_WITH_DEGRADATION"}
SENSITIVE_KEY = re.compile(
    r"(?i)(password|credential_value|(^|_)(app_secret|access_token|api_key|token)$)"
)
POWER_LINE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}) "
    r"(?P<kind>Sleep|Wake)\s+(?P<message>.*)$"
)


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timezone required: {value}")
    return parsed


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    rows = []
    for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"invalid manifest row {number}")
        rows.append(value)
    return rows


def trigger_source(record: dict[str, Any]) -> str:
    return str(record.get("extra", {}).get("trigger_source") or "UNRECORDED")


def trigger_delay_seconds(record: dict[str, Any]) -> float | None:
    scheduled = (record.get("scheduled_period") or {}).get("scheduled_at")
    started = record.get("started_at")
    if not scheduled or not started:
        return None
    return (parse_timestamp(started) - parse_timestamp(scheduled)).total_seconds()


def is_unmodified_launchd_invocation(record: dict[str, Any]) -> bool:
    extra = record.get("extra") or {}
    return (
        trigger_source(record) == "LAUNCHD"
        and extra.get("launchd_label") == "com.trendmonitor.local.intraday"
        and extra.get("as_of_override") is False
        and extra.get("no_network") is False
        and extra.get("force") is False
    )


def _reference_path(reference: str | None) -> Path | None:
    if not reference:
        return None
    return Path(reference.split("#", 1)[0])


def _reference_exists(reference: str | None) -> bool:
    path = _reference_path(reference)
    return bool(path and path.is_file())


def _load_reference(reference: str) -> tuple[dict[str, Any], dict[str, list[str]]]:
    path = _reference_path(reference)
    if path is None or not path.is_file():
        raise FileNotFoundError(reference)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON object: {path}")
    fragment = reference.split("#", 1)[1] if "#" in reference else ""
    return value, parse_qs(fragment, keep_blank_values=True)


def _select_market_result(reference: str, period_end: str) -> dict[str, Any]:
    value, _ = _load_reference(reference)
    if value.get("last_completed_bar_end") == period_end:
        return value
    return next(
        item for item in value.get("results", [])
        if item.get("last_completed_bar_end") == period_end
    )


def _select_stock_result(reference: str, period_end: str) -> dict[str, Any]:
    value, fragment = _load_reference(reference)
    instrument = (fragment.get("instrument") or [None])[0]
    if instrument and isinstance(value.get("results"), dict):
        candidates = value["results"].get(instrument, [])
    elif value.get("stock_60m"):
        candidates = [value]
    else:
        candidates = []
    return next(
        item for item in candidates
        if item.get("stock_60m", {}).get("period_end") == period_end
    )


def _snapshot_trace(snapshot_reference: str, instrument_id: str) -> dict[str, Any]:
    value, _ = _load_reference(snapshot_reference)
    if value.get("risk_60m"):
        risk = value["risk_60m"]
    elif isinstance(value.get("inputs_60m"), dict):
        risk = value["inputs_60m"].get(instrument_id) or {}
    elif value.get("analysis_period") == "60M":
        risk = value
    else:
        risk = {}
    trace = risk.get("source_trace") or {}
    bars = risk.get("system_bars") or []
    last_bar = bars[-1] if bars else {}
    timestamp = trace.get("source_timestamp")
    source_time = None
    if isinstance(timestamp, (int, float)):
        source_time = datetime.fromtimestamp(timestamp / 1000, tz=ZoneInfo("UTC")).astimezone(SHANGHAI).isoformat()
    return {
        "instrument_id": instrument_id,
        "requested_provider": trace.get("requested_provider"),
        "actual_provider": trace.get("actual_provider"),
        "fallback_used": bool(trace.get("fallback_used")),
        "fallback_reason": trace.get("fallback_reason"),
        "fetched_at": trace.get("fetched_at"),
        "source_timestamp": timestamp,
        "source_time": source_time,
        "last_completed_bar_end": risk.get("last_completed_bar_end"),
        "last_bar_transformation": last_bar.get("transformation"),
        "source_snapshot_id": snapshot_reference,
    }


def provider_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Resolve provider evidence through immutable result/source references."""
    period_end = record.get("period_end")
    traces: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        market = _select_market_result(str(record["market_result_id"]), str(period_end))
        for state in market.get("index_states", []):
            traces.append(_snapshot_trace(state["source_snapshot_id"], state["instrument_id"]))
    except (KeyError, StopIteration, OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"market:{type(exc).__name__}")
    for instrument_id, reference in (record.get("stock_result_ids") or {}).items():
        try:
            stock = _select_stock_result(str(reference), str(period_end))["stock_60m"]
            source_input = stock.get("source_risk_input_id") or stock.get("source_stock_risk_input_id")
            if not source_input:
                raise KeyError("source_risk_input_id")
            traces.append(_snapshot_trace(source_input, instrument_id))
        except (KeyError, StopIteration, OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{instrument_id}:{type(exc).__name__}")
    complete = len(traces) == 10 and all(
        item.get("last_completed_bar_end") == period_end for item in traces
    )
    source_times = [item["source_time"] for item in traces if item.get("source_time")]
    return {
        "status": "PASS" if complete else "PENDING",
        "trace_count": len(traces),
        "requested_providers": sorted({item["requested_provider"] for item in traces if item.get("requested_provider")}),
        "actual_providers": sorted({item["actual_provider"] for item in traces if item.get("actual_provider")}),
        "fallback_used": any(item["fallback_used"] for item in traces),
        "latest_source_timestamp": max(source_times) if source_times else None,
        "last_completed_bar_end": period_end if complete else None,
        "traces": traces,
        "errors": errors,
    }


def validate_combined_result(record: dict[str, Any]) -> dict[str, Any]:
    reference = record.get("combined_result_id")
    path = _reference_path(reference)
    if path is None or not path.is_file():
        return {"status": "FAIL", "reason": "COMBINED_RESULT_MISSING"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "FAIL", "reason": "COMBINED_RESULT_INVALID"}
    stock_ids = record.get("stock_result_ids") or {}
    source_paths = [record.get("market_result_id"), record.get("market_15m_result_id"), *stock_ids.values()]
    checks = {
        "period_match": value.get("period_end") == record.get("period_end"),
        "execution_mode_match": value.get("execution_mode") == record.get("execution_mode"),
        "lookahead_safe": value.get("data", {}).get("lookahead_safe") is True,
        "market_present": isinstance(value.get("market"), dict),
        "market_15m_present": bool(value.get("market", {}).get("15m_internal")),
        "two_stocks_present": len(value.get("stocks", {})) == 2,
        "source_results_present": len(source_paths) == 4 and all(_reference_exists(item) for item in source_paths),
        "no_trading_advice": value.get("contains_trading_advice") is False,
        "rules_frozen": value.get("rules_versions") == {
            "market_60m": "market_60m_risk_v0.1",
            "market_15m": "market_15m_internal_v0.1",
            "stock_60m": "stock_60m_risk_v0.1",
            "stock_15m": "stock_15m_internal_v0.1",
        },
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "path": str(path),
    }


def _period_time(record: dict[str, Any]) -> str | None:
    value = record.get("period_end")
    return parse_timestamp(value).astimezone(SHANGHAI).strftime("%H:%M") if value else None


def evaluate_live_slot(
    entries: Iterable[dict[str, Any]],
    *,
    period_time: str,
    system: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relevant = [
        item for item in entries
        if item.get("execution_mode") == "LIVE_SCHEDULED" and _period_time(item) == period_time
    ]
    failures = [
        item for item in relevant
        if is_unmodified_launchd_invocation(item) and item.get("status") == "FAILED"
    ]
    successes = [
        item for item in relevant
        if is_unmodified_launchd_invocation(item) and item.get("status") in SUCCESS
    ]
    if not successes:
        if failures:
            failure = failures[-1]
            return {
                "status": "FAIL",
                "reason": "LIVE_LAUNCHD_RUN_FAILED",
                "run_id": failure.get("run_id"),
                "error": failure.get("error_summary"),
            }
        return {
            "status": "PENDING",
            "reason": "NO_QUALIFYING_LIVE_LAUNCHD_EVIDENCE",
            "manual_or_unrecorded_candidates": len(relevant),
        }
    record = successes[-1]
    report = validate_combined_result(record)
    providers = provider_evidence(record)
    delay = trigger_delay_seconds(record)
    if report["status"] != "PASS":
        status, reason = "FAIL", report.get("reason", "COMBINED_RESULT_VALIDATION_FAILED")
    elif providers["status"] != "PASS":
        status, reason = "PENDING", "PROVIDER_LINEAGE_INCOMPLETE"
    elif delay is None or delay < 0:
        status, reason = "FAIL", "INVALID_TRIGGER_DELAY"
    else:
        status, reason = "PASS", None
    launchd = (system or {}).get("launchd", {})
    return {
        "status": status,
        "reason": reason,
        "run_id": record.get("run_id"),
        "trading_date": record.get("trading_date"),
        "period_end": record.get("period_end"),
        "scheduled_at": (record.get("scheduled_period") or {}).get("scheduled_at"),
        "started_at": record.get("started_at"),
        "trigger_delay_seconds": delay,
        "trigger_delay_assessment": "ACCEPTABLE" if delay is not None and delay <= 120 else "REVIEW_REQUIRED",
        "execution_mode": record.get("execution_mode"),
        "trigger_source": trigger_source(record),
        "launchd_exit_code_observed": launchd.get("last_exit_code"),
        "network_attempts": record.get("network_attempts"),
        "combined_result": report,
        "provider_evidence": providers,
    }


def parse_power_events(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        match = POWER_LINE.match(line)
        if not match:
            continue
        message = match.group("message").strip()
        if match.group("kind") == "Sleep" and "Entering Sleep state" in message:
            kind = "SLEEP"
            operator_candidate = not any(
                token in message for token in (
                    "Maintenance Sleep", "Notification Wake Back to Sleep", "Thermal Emergency"
                )
            )
        elif match.group("kind") == "Wake" and ("Wake from" in message or "to FullWake" in message):
            kind = "WAKE"
            operator_candidate = not any(
                token in message for token in ("Notification", "Maintenance", "RTC")
            )
        else:
            continue
        stamp = datetime.strptime(match.group("stamp"), "%Y-%m-%d %H:%M:%S %z")
        events.append({
            "event": kind,
            "timestamp": stamp.isoformat(),
            "operator_candidate": operator_candidate,
            "message": message,
        })
    return events


def _sleep_interval(boundary: datetime, events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    sleeps = [
        item for item in events
        if item["event"] == "SLEEP" and item["operator_candidate"]
        and parse_timestamp(item["timestamp"]) <= boundary
    ]
    if not sleeps:
        return None
    sleep = sleeps[-1]
    wakes = [
        item for item in events
        if item["event"] == "WAKE" and item["operator_candidate"]
        and parse_timestamp(item["timestamp"]) > boundary
        and parse_timestamp(item["timestamp"]) > parse_timestamp(sleep["timestamp"])
    ]
    return (sleep, wakes[0]) if wakes else None


def evaluate_sleep_wake(entries: Iterable[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    values = list(entries)
    catches = [
        item for item in values
        if item.get("execution_mode") == "CATCH_UP"
        and item.get("status") in SUCCESS
        and item.get("extra", {}).get("missed_completed_period") is True
        and is_unmodified_launchd_invocation(item)
    ]
    for record in reversed(catches):
        boundary = parse_timestamp((record.get("scheduled_period") or {})["scheduled_at"])
        interval = _sleep_interval(boundary, events)
        if not interval:
            continue
        sleep, wake = interval
        started = parse_timestamp(record["started_at"])
        if started < parse_timestamp(wake["timestamp"]):
            continue
        same_period_live = any(
            item.get("period_end") == record.get("period_end")
            and item.get("execution_mode") == "LIVE_SCHEDULED"
            and item.get("status") in SUCCESS
            for item in values
        )
        if same_period_live:
            return {"status": "FAIL", "reason": "LIVE_AND_CATCH_UP_CONFLICT", "run_id": record.get("run_id")}
        report = validate_combined_result(record)
        if report["status"] != "PASS":
            return {"status": "FAIL", "reason": "CATCH_UP_RESULT_INVALID", "run_id": record.get("run_id"), "combined_result": report}
        period_end = parse_timestamp(record["period_end"])
        return {
            "status": "PASS",
            "run_id": record.get("run_id"),
            "period_end": record.get("period_end"),
            "missed_scheduled_boundary": boundary.isoformat(),
            "sleep_time": sleep["timestamp"],
            "wake_time": wake["timestamp"],
            "first_runner_time_after_wake": record.get("started_at"),
            "execution_mode": record.get("execution_mode"),
            "trigger_source": trigger_source(record),
            "staleness_seconds": (started - period_end).total_seconds(),
            "lookahead_safe": report["checks"]["lookahead_safe"],
            "combined_result": report,
            "closing_bucket": closing_bucket_evidence(record),
        }
    failures = []
    for item in values:
        if not (
            item.get("execution_mode") == "CATCH_UP"
            and item.get("status") == "FAILED"
            and is_unmodified_launchd_invocation(item)
        ):
            continue
        scheduled_at = (item.get("scheduled_period") or {}).get("scheduled_at")
        started_at = item.get("started_at")
        if not scheduled_at or not started_at:
            continue
        interval = _sleep_interval(parse_timestamp(scheduled_at), events)
        if interval and parse_timestamp(started_at) >= parse_timestamp(interval[1]["timestamp"]):
            failures.append(item)
    if failures:
        return {"status": "FAIL", "reason": "LAUNCHD_CATCH_UP_FAILED", "run_id": failures[-1].get("run_id")}
    return {"status": "PENDING", "reason": "NO_SLEEP_BOUNDARY_CATCH_UP_EVIDENCE"}


def closing_bucket_evidence(record: dict[str, Any]) -> dict[str, Any]:
    if _period_time(record) != "15:00":
        return {"status": "NOT_APPLICABLE", "reason": "EVIDENCE_PERIOD_IS_NOT_15_00"}
    provider = provider_evidence(record)
    market_traces = [item for item in provider.get("traces", []) if item["instrument_id"].startswith("index.")]
    merged = sum(item.get("last_bar_transformation") == "MERGE_CLOSING_BUCKET" for item in market_traces)
    return {
        "status": "PASS" if len(market_traces) == 8 and merged == 8 else "FAIL",
        "market_indices_checked": len(market_traces),
        "merged_closing_bucket_count": merged,
    }


def evaluate_restart(
    entries: Iterable[dict[str, Any]],
    *,
    baseline: dict[str, Any] | None,
    system: dict[str, Any],
) -> dict[str, Any]:
    if not baseline:
        return {"status": "PENDING", "reason": "PRE_RESTART_BASELINE_ESTABLISHED_THIS_RUN"}
    before = baseline.get("system", {})
    before_boot = before.get("boot_time")
    current_boot = system.get("boot_time")
    if not before_boot or not current_boot or parse_timestamp(current_boot) <= parse_timestamp(before_boot):
        return {"status": "PENDING", "reason": "NO_POST_BASELINE_RESTART_OBSERVED", "baseline_boot_time": before_boot, "current_boot_time": current_boot}
    old_plist = before.get("launchd", {}).get("plist", {})
    new_plist = system.get("launchd", {}).get("plist", {})
    if not old_plist.get("sha256") or old_plist.get("sha256") != new_plist.get("sha256") or old_plist.get("mtime") != new_plist.get("mtime"):
        return {"status": "FAIL", "reason": "LAUNCHAGENT_INSTALL_IDENTITY_CHANGED_AFTER_BASELINE"}
    post_boot = [
        item for item in entries
        if item.get("started_at")
        and parse_timestamp(item["started_at"]) >= parse_timestamp(current_boot)
        and is_unmodified_launchd_invocation(item)
        and item.get("scheduled_period")
        and item.get("status") in SUCCESS.union({"FAILED"})
    ]
    if system.get("launchd", {}).get("loaded") is not True or not post_boot:
        return {"status": "PENDING", "reason": "WAITING_FOR_POST_RESTART_LAUNCHD_RUNTIME", "current_boot_time": current_boot}
    record = post_boot[0]
    return {
        "status": "PASS",
        "baseline_boot_time": before_boot,
        "system_boot_time": current_boot,
        "console_login_time": system.get("console_login_time"),
        "launchagent_loaded": True,
        "plist_identity_unchanged": True,
        "first_post_restart_run_id": record.get("run_id"),
        "first_post_restart_run_started_at": record.get("started_at"),
        "first_post_restart_run_status": record.get("status"),
        "launchd_last_exit_code": system.get("launchd", {}).get("last_exit_code"),
    }


def acceptance_status(dimensions: Iterable[dict[str, Any]]) -> str:
    statuses = [item.get("status") for item in dimensions]
    if "FAIL" in statuses:
        return "FAIL"
    if statuses and all(value == "PASS" for value in statuses):
        return "PASS"
    return "PENDING"


def redact_payload(value: Any, secrets: Iterable[str], *, key: str | None = None) -> Any:
    if key and SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact_payload(v, secrets, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_payload(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item, secrets) for item in value]
    if isinstance(value, str):
        output = value
        for secret in secrets:
            if secret:
                output = output.replace(secret, "[REDACTED]")
        return output
    return value


def _command(args: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, type(exc).__name__


def _partial_date(value: str, now: datetime) -> datetime | None:
    for pattern in ("%a %b %d %H:%M", "%b %d %H:%M"):
        try:
            parsed = datetime.strptime(value.strip(), pattern).replace(year=now.year, tzinfo=LOCAL)
            if parsed > now + timedelta(days=2):
                parsed = parsed.replace(year=now.year - 1)
            return parsed
        except ValueError:
            continue
    return None


def system_evidence(project_root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observed = now or datetime.now().astimezone()
    code, output = _command(["last", "reboot"])
    boot = None
    if code == 0:
        match = re.search(r"^reboot time\s+(?P<value>.+)$", output, flags=re.MULTILINE)
        if match:
            boot = _partial_date(match.group("value"), observed)
    code, output = _command(["who"])
    login = None
    if code == 0:
        match = re.search(r"^\S+\s+console\s+(?P<value>[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2})", output, flags=re.MULTILINE)
        if match:
            login = _partial_date(match.group("value"), observed)
    label = "com.trendmonitor.local.intraday"
    code, output = _command(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
    runs = re.search(r"\bruns = (\d+)", output)
    exit_code = re.search(r"last exit code = (-?\d+)", output)
    state = re.search(r"\bstate = ([^\n]+)", output)
    installed = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist = {
        "path": str(installed),
        "exists": installed.is_file(),
        "mtime": datetime.fromtimestamp(installed.stat().st_mtime, tz=LOCAL).isoformat() if installed.is_file() else None,
        "sha256": hashlib.sha256(installed.read_bytes()).hexdigest() if installed.is_file() else None,
    }
    _, power_output = _command(["pmset", "-g", "log"], timeout=40)
    power = parse_power_events(power_output)
    cutoff = observed - timedelta(days=14)
    power = [
        item for item in power
        if item["operator_candidate"] and parse_timestamp(item["timestamp"]) >= cutoff
    ]
    return {
        "observed_at": observed.isoformat(),
        "boot_time": boot.isoformat() if boot else None,
        "console_login_time": login.isoformat() if login else None,
        "launchd": {
            "label": label,
            "loaded": code == 0,
            "state": state.group(1).strip() if state else None,
            "runs": int(runs.group(1)) if runs else None,
            "last_exit_code": int(exit_code.group(1)) if exit_code else None,
            "plist": plist,
        },
        "power_events": power,
        "business_timezone": "Asia/Shanghai",
        "host_timezone": str(LOCAL),
        "project_root": str(root),
    }


def earliest_baseline(acceptance_root: str | Path) -> dict[str, Any] | None:
    paths = sorted((Path(acceptance_root) / "baseline").glob("*.json"))
    return json.loads(paths[0].read_text(encoding="utf-8")) if paths else None


def save_baseline(acceptance_root: str | Path, system: dict[str, Any]) -> Path:
    root = Path(acceptance_root) / "baseline"
    root.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "recorded_at": system["observed_at"], "system": system}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    stamp = system["observed_at"].replace(":", "").replace("+", "p")
    path = root / f"{stamp}__{digest[:12]}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def save_observation(acceptance_root: str | Path, payload: dict[str, Any]) -> dict[str, str]:
    root = Path(acceptance_root)
    evidence = root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    stamp = payload["observed_at"].replace(":", "").replace("+", "p")
    path = evidence / f"{stamp}__{digest[:12]}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    manifest = root / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"observed_at": payload["observed_at"], "path": str(path), "sha256": digest}, ensure_ascii=False) + "\n")
    latest = root / "runtime_live_acceptance_latest.json"
    temporary = root / ".runtime_live_acceptance_latest.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(latest)
    return {"evidence_path": str(path), "latest_path": str(latest), "sha256": digest}


def build_acceptance(
    entries: list[dict[str, Any]],
    *,
    system: dict[str, Any],
    baseline: dict[str, Any] | None,
    security_status: str,
    health_status: str,
    rules_unchanged: bool,
) -> dict[str, Any]:
    morning = evaluate_live_slot(entries, period_time="10:30", system=system)
    afternoon = evaluate_live_slot(entries, period_time="14:00", system=system)
    restart = evaluate_restart(entries, baseline=baseline, system=system)
    sleep = evaluate_sleep_wake(entries, system.get("power_events", []))
    dimensions = [morning, afternoon, restart, sleep]
    status = acceptance_status(dimensions)
    guards = {
        "secret_audit": security_status,
        "runtime_health": health_status,
        "lookahead_safety": "PASS" if all(
            item.get("status") != "PASS" or item.get("combined_result", {}).get("checks", {}).get("lookahead_safe", item.get("lookahead_safe")) is True
            for item in (morning, afternoon, sleep)
        ) else "FAIL",
        "rule_mutation": "PASS" if rules_unchanged else "FAIL",
    }
    if "FAIL" in guards.values():
        status = "FAIL"
    trading_dates = sorted({item.get("trading_date") for item in (morning, afternoon) if item.get("trading_date")})
    # Missing operator evidence preserves TASK_013's READY_WITH_LIMITS state;
    # only observed acceptance failure downgrades the runtime to NOT_READY.
    readiness = "NOT_READY" if status == "FAIL" else "READY_WITH_LIMITS"
    return {
        "schema_version": 1,
        "rules_version": "runtime_live_acceptance_v0.1",
        "observed_at": system["observed_at"],
        "acceptance_status": status,
        "task_status": "SUCCESS" if status == "PASS" else "FAILED" if status == "FAIL" else "PARTIAL",
        "unattended_runtime_readiness": readiness,
        "test_trading_dates": trading_dates,
        "morning_live_schedule": morning,
        "afternoon_live_schedule": afternoon,
        "restart_recovery": restart,
        "sleep_wake_recovery": sleep,
        "safeguards": guards,
        "system": system,
        "known_limits": {
            "power_off": "NO_RUNTIME",
            "user_login_dependency": "USER_LAUNCHAGENT_REQUIRES_LOGIN_SESSION",
            "sleep": "NO_ON_TIME_GUARANTEE; WAKE_CATCH_UP_REQUIRED",
            "network": "FINITE_RETRY_AND_PROVIDER_FALLBACK_ONLY",
            "provider": "UPSTREAM_AVAILABILITY_REQUIRED",
        },
    }
