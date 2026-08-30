#!/usr/bin/env python3
"""Collect and verify TASK_013A evidence without triggering a risk run."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.runtime import RuntimeConfig, RuntimeStore, TradingCalendarStore  # noqa: E402
from trend_monitor.runtime.acceptance import (  # noqa: E402
    build_acceptance,
    earliest_baseline,
    redact_payload,
    save_baseline,
    save_observation,
    system_evidence,
)
from trend_monitor.runtime.health import check_runtime_health  # noqa: E402
from trend_monitor.runtime.logging import dotenv_secret_values  # noqa: E402
from trend_monitor.runtime.security import audit_dotenv  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> int:
    config = RuntimeConfig.load(
        PROJECT_ROOT / "config" / "runtime_schedule.json", project_root=PROJECT_ROOT
    )
    acceptance_root = PROJECT_ROOT / "data" / "runtime" / "acceptance"
    system = system_evidence(PROJECT_ROOT)
    baseline = earliest_baseline(acceptance_root)
    secrets = dotenv_secret_values(PROJECT_ROOT / ".env", config.raw["secret_keys"])
    if baseline is None:
        safe_baseline = redact_payload(system, secrets)
        save_baseline(acceptance_root, safe_baseline)
    env = audit_dotenv(PROJECT_ROOT / ".env", config.raw["secret_keys"])
    health = check_runtime_health(
        PROJECT_ROOT,
        config,
        TradingCalendarStore(PROJECT_ROOT / "data" / "runtime" / "a_share_calendar.json"),
        RuntimeStore(PROJECT_ROOT / "data" / "runtime"),
        now=datetime.now(SHANGHAI),
    )
    try:
        config.verify_frozen_rules()
        rules_unchanged = True
    except ValueError:
        rules_unchanged = False
    payload = build_acceptance(
        RuntimeStore(PROJECT_ROOT / "data" / "runtime").entries(),
        system=system,
        baseline=baseline,
        security_status=env["status"],
        health_status=health["status"],
        rules_unchanged=rules_unchanged,
    )
    payload = redact_payload(payload, secrets)
    saved = save_observation(acceptance_root, payload)
    payload["evidence"] = saved
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    # PENDING is an honest, successfully completed audit. Only FAIL is non-zero.
    return 1 if payload["acceptance_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
