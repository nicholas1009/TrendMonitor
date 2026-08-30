#!/usr/bin/env python3
"""Unified TASK_013 production runner."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.providers.hithink import HithinkProvider  # noqa: E402
from trend_monitor.runtime import (  # noqa: E402
    RuntimeConfig,
    RuntimeRunner,
    RuntimeSnapshotReader,
    RuntimeStore,
    SubprocessMonitorPipeline,
    TradingCalendarStore,
)
from trend_monitor.runtime.logging import dotenv_secret_values, runtime_logger  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(SHANGHAI)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def null_logger() -> logging.Logger:
    logger = logging.getLogger("trend_monitor.runtime.dry_run")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="ISO timestamp; naive values are interpreted as Asia/Shanghai")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    as_of = parse_as_of(args.as_of)
    config = RuntimeConfig.load(
        PROJECT_ROOT / "config" / "runtime_schedule.json", project_root=PROJECT_ROOT
    )
    secrets = dotenv_secret_values(PROJECT_ROOT / ".env", config.raw["secret_keys"])
    logger = null_logger() if args.dry_run else runtime_logger(
        PROJECT_ROOT / "logs" / "runtime" / "intraday_monitor.log", secrets=secrets
    )
    calendar = TradingCalendarStore(
        PROJECT_ROOT / "data" / "runtime" / "a_share_calendar.json",
        provider_factory=lambda: HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env")),
    )
    store = RuntimeStore(PROJECT_ROOT / "data" / "runtime")
    reader = RuntimeSnapshotReader(PROJECT_ROOT)
    pipeline = SubprocessMonitorPipeline(PROJECT_ROOT, config, logger, secrets=secrets)
    launched_by_launchd = os.environ.get("TREND_MONITOR_LAUNCHD") == "1"
    runner = RuntimeRunner(
        project_root=PROJECT_ROOT,
        config=config,
        calendar=calendar,
        store=store,
        reader=reader,
        pipeline=pipeline,
        logger=logger,
        invocation_metadata={
            "trigger_source": "LAUNCHD" if launched_by_launchd else "MANUAL",
            "launchd_label": (
                "com.trendmonitor.local.intraday" if launched_by_launchd else None
            ),
            "process_pid": os.getpid(),
            "parent_pid": os.getppid(),
            "as_of_override": args.as_of is not None,
            "no_network": args.no_network,
            "force": args.force,
        },
    )
    result = (
        runner.dry_run(as_of=as_of, no_network=args.no_network)
        if args.dry_run
        else runner.run(as_of=as_of, no_network=args.no_network, force=args.force)
    )
    if not launched_by_launchd:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = result.get("status") == "FAILED" or any(
        item.get("status") == "FAILED" for item in result.get("results", [])
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
