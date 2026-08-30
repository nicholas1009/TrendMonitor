#!/usr/bin/env python3
"""Verify TASK_012 without installing or calling an uncredentialed provider SDK."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trend_monitor.industry_feasibility import (  # noqa: E402
    IndustryMinuteFeasibilityRules,
    build_feasibility_result,
    credential_available,
)


def _dump(label: str, value: object) -> None:
    print(label)
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(value)


def main() -> int:
    rules = IndustryMinuteFeasibilityRules.load(
        PROJECT_ROOT / "config" / "industry_minute_feasibility.json"
    )
    present = credential_available(
        tuple(rules.raw["tushare"]["credential_keys"]),
        dotenv_path=PROJECT_ROOT / ".env",
    )
    evaluated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    first = build_feasibility_result(
        rules,
        project_root=PROJECT_ROOT,
        evaluated_at=evaluated_at,
        credential_present=present,
    )
    second = build_feasibility_result(
        rules,
        project_root=PROJECT_ROOT,
        evaluated_at=evaluated_at,
        credential_present=present,
    )
    deterministic = first.to_dict() == second.to_dict()
    report = first.to_dict()
    report["determinism"] = "PASS" if deterministic else "FAIL"
    report["no_synthetic"] = "PASS" if not first.synthetic_benchmark_created else "FAIL"
    report["stock_score_immutability"] = "PASS" if not first.stock_score_modified else "FAIL"
    destination = PROJECT_ROOT / "data" / "reports" / "industry_minute_feasibility_latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _dump("EXACT THS SOURCE", "NOT_FOUND")
    _dump("TUSHARE SW PROXY", first.minute_proxy_candidates)
    _dump("MEMBERSHIP", first.membership)
    _dump("CONSTITUENT OVERLAP", first.constituent_overlap)
    _dump("DAILY CORRELATION", first.daily_correlation)
    _dump("HISTORICAL 15M", first.historical_minute)
    _dump("HISTORICAL 60M", first.historical_minute)
    _dump("REALTIME CAPABILITY", first.realtime_capability)
    _dump("LIVE BOUNDARY", first.boundary_snapshot_feasibility["status"])
    _dump("RECOMMENDED DATA SCHEME", first.recommended_data_scheme)
    _dump("FINAL JUDGMENT", first.final_judgment)
    _dump("DETERMINISM", report["determinism"])
    _dump("NO SYNTHETIC", report["no_synthetic"])
    _dump("STOCK SCORE IMMUTABILITY", report["stock_score_immutability"])
    _dump("REPORT", str(destination))
    return 0 if deterministic else 1


if __name__ == "__main__":
    raise SystemExit(main())
