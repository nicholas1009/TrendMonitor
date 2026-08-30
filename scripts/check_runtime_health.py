#!/usr/bin/env python3
"""TASK_013 runtime health check; secret values are never printed."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.runtime import RuntimeConfig, RuntimeStore, TradingCalendarStore  # noqa: E402
from trend_monitor.runtime.health import check_runtime_health  # noqa: E402


def main() -> int:
    config = RuntimeConfig.load(PROJECT_ROOT / "config" / "runtime_schedule.json", project_root=PROJECT_ROOT)
    result = check_runtime_health(
        PROJECT_ROOT,
        config,
        TradingCalendarStore(PROJECT_ROOT / "data" / "runtime" / "a_share_calendar.json"),
        RuntimeStore(PROJECT_ROOT / "data" / "runtime"),
        now=datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
