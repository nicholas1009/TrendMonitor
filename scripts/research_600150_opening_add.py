#!/usr/bin/env python3
"""Read-only SHADOW evaluator for the next confirmed 600150 Auction Final."""

from __future__ import annotations

import argparse
from datetime import datetime, time
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
STUDY_PATH = PROJECT_ROOT / "research" / "600150" / "study.py"
SPEC = importlib.util.spec_from_file_location("research_600150_study", STUDY_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load research module: {STUDY_PATH}")
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOL = "600150.SH"
PLAYBOOK = PROJECT_ROOT / "research" / "600150" / "opening_playbook_next_confirmed_trading_day_v0.1.json"
THESIS = PROJECT_ROOT / "research" / "600150" / "position_thesis_20260904.json"
RESEARCH_OUTPUT_ROOT = (PROJECT_ROOT / "research" / "600150" / "derived").resolve()


def _calendar_dates(raw: Mapping[str, object]) -> set[str]:
    data = raw.get("data")
    items = data.get("item") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        raise ValueError("Hithink calendar response is invalid")
    return {
        str(item["date"])
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("date"), str)
    }


def _scenario(gap: float, playbook: Mapping[str, object]) -> str:
    buckets = playbook["gap_buckets"]
    low = float(buckets["LOW_OPEN"]["max_inclusive"])
    high = float(buckets["HIGH_OPEN"]["min_inclusive"])
    if gap <= low:
        return "LOW_OPEN"
    if gap >= high:
        return "HIGH_OPEN"
    return "NEUTRAL_OPEN"


def no_signal(
    *,
    observed_at: datetime,
    auction_status: str,
    reason: str,
    questions: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_SHADOW",
        "symbol": SYMBOL,
        "trade_date": observed_at.date().isoformat(),
        "auction_market_time": datetime.combine(
            observed_at.date(), time(9, 25), tzinfo=SHANGHAI
        ).isoformat(),
        "provider_observed_at": observed_at.isoformat(),
        "auction_status": auction_status,
        "matched_scenario": None,
        "action": "NO_SIGNAL",
        "add_qualification": "UNKNOWN",
        "execute_at_auction": "UNKNOWN",
        "confidence": "LOW",
        "historical_sample_size": 0,
        "validation_sample_size": 0,
        "key_reasons": [reason],
        "risk_reasons": [],
        "questions": list(questions),
        "unknowns": [reason],
        "target_position_size": "UNKNOWN",
        "provisional_experiment_increment_shares": None,
        "proposed_total_shares": None,
        "production_state_written": False,
        "notification_sent": False,
    }


def evaluate(
    *,
    auction_raw: Mapping[str, object],
    calendar_raw: Mapping[str, object],
    playbook: Mapping[str, object],
    thesis: Mapping[str, object],
    observed_at: datetime,
) -> dict[str, object]:
    """Pure evaluator; callers own provider I/O and optional research output."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    local = observed_at.astimezone(SHANGHAI)
    if thesis.get("symbol") != SYMBOL or playbook.get("symbol") != SYMBOL:
        raise ValueError("600150 research input identity mismatch")
    target_event_date = datetime.fromisoformat(
        str(thesis["position"]["entry_date"])
    ).date()
    if local.date() <= target_event_date:
        return no_signal(
            observed_at=local,
            auction_status="TARGET_EVENT_NOT_YET_PRIOR_SESSION",
            reason="Opening experiment requires a confirmed trading day after 2026-09-04",
        )
    compact_date = local.strftime("%Y%m%d")
    if compact_date not in _calendar_dates(calendar_raw):
        return no_signal(
            observed_at=local,
            auction_status="NOT_TRADING_DAY_OR_CALENDAR_NOT_YET_AUTHORITATIVE",
            reason="current Shanghai date is absent from Hithink authoritative trading calendar",
            questions=("NEXT_TRADING_DAY remains unconfirmed until Hithink calendar includes it",),
        )

    data = auction_raw.get("data")
    if not isinstance(data, Mapping):
        return no_signal(
            observed_at=local,
            auction_status="DATA_NOT_READY",
            reason="Auction response data is not an object",
        )
    if data.get("auction_phase") != "closed" or data.get("data_status") != "final":
        return no_signal(
            observed_at=local,
            auction_status="DATA_NOT_READY",
            reason="Auction Final is not closed/final",
        )
    items = [
        item for item in data.get("item", [])
        if isinstance(item, Mapping) and item.get("thscode") == SYMBOL
    ]
    if len(items) != 1 or items[0].get("auction_price") is None:
        return no_signal(
            observed_at=local,
            auction_status="DATA_NOT_READY",
            reason="600150 Auction price is unavailable",
        )

    auction_price = float(items[0]["auction_price"])
    target_close = float(playbook["target_event_close"])
    gap = auction_price / target_close - 1
    matched = _scenario(gap, playbook)
    scenario = playbook["scenarios"][matched]
    decision = scenario["decision"]
    action = str(decision["action"])
    calibration_size = int(scenario["calibration"]["sample_size"])
    validation_size = int(scenario["validation"]["sample_size"])
    add = action == "ADD"
    current_shares = int(thesis["position"]["shares"])
    increment = (
        int(playbook["add_execution_policy"]["provisional_experiment_increment_shares"])
        if add
        else None
    )
    return {
        "schema_version": 1,
        "mode": "READ_ONLY_SHADOW",
        "symbol": SYMBOL,
        "trade_date": local.date().isoformat(),
        "auction_market_time": datetime.combine(
            local.date(), time(9, 25), tzinfo=SHANGHAI
        ).isoformat(),
        "provider_observed_at": local.isoformat(),
        "auction_status": "CLOSED_FINAL",
        "auction_price": auction_price,
        "auction_gap_vs_target_close": gap,
        "matched_scenario": matched,
        "action": action,
        "add_qualification": "YES" if add else "NO",
        "execute_at_auction": "NO" if add else "UNKNOWN",
        "confidence": (
            "LOW" if decision["status"] in {"SAMPLE_THIN", "INCONCLUSIVE"} else "MEDIUM"
        ),
        "historical_sample_size": calibration_size + validation_size,
        "validation_sample_size": validation_size,
        "key_reasons": [str(decision["reason"])],
        "risk_reasons": [
            "Auction/Open bridge is provisional",
            "TARGET_EVENT has only 11 same-signature historical events",
        ],
        "questions": [],
        "unknowns": ["TARGET_POSITION_SIZE"],
        "target_position_size": "UNKNOWN",
        "provisional_experiment_increment_shares": increment,
        "proposed_total_shares": current_shares + increment if increment is not None else None,
        "production_state_written": False,
        "notification_sent": False,
    }


def _safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(RESEARCH_OUTPUT_ROOT):
        raise ValueError("output must stay inside research/600150/derived")
    return resolved


def _render_chinese(result: Mapping[str, object]) -> str:
    return "\n".join((
        "中国船舶 Opening Shadow",
        f"交易日：{result['trade_date']}",
        f"竞价状态：{result['auction_status']}",
        f"场景：{result['matched_scenario'] or '未匹配'}",
        f"动作：{result['action']}",
        f"加仓资格：{result['add_qualification']}",
        f"竞价直接执行：{result['execute_at_auction']}",
        f"置信度：{result['confidence']}",
        "模式：只读影子实验；未写生产状态；未发送通知。",
    ))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        help="optional JSON path under research/600150/derived; default is stdout only",
    )
    args = parser.parse_args(argv)
    from trend_monitor.providers.hithink import HithinkProvider

    provider = HithinkProvider(dotenv_path=PROJECT_ROOT / ".env")
    observed_at = datetime.now(SHANGHAI)
    calendar = provider.trading_days()
    if observed_at.strftime("%Y%m%d") in _calendar_dates(calendar):
        auction = provider.auction_snapshot([SYMBOL], stage="final")
    else:
        auction = {"data": None}
    result = evaluate(
        auction_raw=auction,
        calendar_raw=calendar,
        playbook=json.loads(PLAYBOOK.read_text(encoding="utf-8")),
        thesis=json.loads(THESIS.read_text(encoding="utf-8")),
        observed_at=observed_at,
    )
    if args.output:
        output = _safe_output(Path(args.output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print()
    print(_render_chinese(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
