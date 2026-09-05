#!/usr/bin/env python3
"""TASK_009 current, in-progress and 80-period 15m internal verification."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.market_internal import (  # noqa: E402
    Historical15mRiskInputBuilder,
    Market15mInternalEngine,
    Market15mInternalRules,
    Market15mInternalStore,
    Market15mRiskInputStore,
    render_market_15m_internal_report,
    run_internal_replay,
)
from trend_monitor.market_risk import Market60mRiskRules  # noqa: E402
from trend_monitor.providers.longbridge import LongbridgeMarketDataAdapter, LongbridgeProvider  # noqa: E402
from trend_monitor.quality import RiskFeatureContract  # noqa: E402
from trend_monitor.registry import InstrumentRegistry  # noqa: E402
from trend_monitor.risk_input import RiskInputAssembler, RiskInputSnapshotStore, risk_input_from_dict  # noqa: E402
from trend_monitor.schemas import DataType, PreflightStatus  # noqa: E402
from trend_monitor.services import MarketDataService  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _frozen_source_results(
    cache: RawCache,
    market_data: MarketDataService,
    snapshot_store: RiskInputSnapshotStore,
    snapshot_paths: dict[str, str],
):
    """Load the exact 15m Raw members frozen by this analysis cycle."""
    entries_by_path = {
        str(Path(entry.path).resolve()): entry
        for entry in cache.entries()
        if entry.data_type is DataType.KLINE_15M
    }
    results = {}
    for instrument_id, snapshot_path in snapshot_paths.items():
        payload = snapshot_store.load(snapshot_path)
        raw_path = payload["support_15m"]["source_trace"].get("raw_path")
        entry = entries_by_path.get(str(Path(raw_path).resolve())) if raw_path else None
        if entry is None:
            raise ValueError(f"frozen 15m Raw member is not in cache: {instrument_id}")
        results[instrument_id] = market_data.load_cached(entry)
    return results


def _source_ids(snapshot_path: str, instrument_ids: tuple[str, ...]) -> dict[str, str]:
    return {item: f"{snapshot_path}#instrument={item}" for item in instrument_ids}


def main() -> int:
    rules = Market15mInternalRules.load(PROJECT_ROOT / "config" / "market_15m_internal_rules.json")
    source_rules = Market60mRiskRules.load(PROJECT_ROOT / "config" / "market_60m_risk_rules.json")
    engine = Market15mInternalEngine(rules, source_rules)
    task8_replay_path = PROJECT_ROOT / "data" / "reports" / "market_60m_replay_latest.json"
    task8_replay = json.loads(task8_replay_path.read_text(encoding="utf-8"))
    if task8_replay.get("periods") != 80 or task8_replay.get("rules_version") != source_rules.rules_version:
        print("HISTORICAL REPLAY\nFAIL — TASK_008 frozen replay is unavailable")
        return 1

    source_60m_path = str(task8_replay["current_machine_path"])
    source_60m_current = json.loads(Path(source_60m_path).read_text(encoding="utf-8"))
    coverage = json.loads(
        (PROJECT_ROOT / "data" / "reports" / "market_index_coverage_latest.json").read_text(encoding="utf-8")
    )
    if coverage.get("market_bundle", {}).get("coverage") != "FULL_READY":
        print("CURRENT INTERNAL STRUCTURE\nFAIL — 8-index Market Bundle is not FULL_READY")
        return 1
    snapshot_store = RiskInputSnapshotStore(PROJECT_ROOT / "data" / "risk_inputs")
    current_inputs = {}
    current_source_ids = {}
    for entry in coverage["market_bundle"]["entries"]:
        payload = snapshot_store.load(entry["snapshot_path"])
        risk_input = risk_input_from_dict(payload["support_15m"])
        current_inputs[entry["instrument_id"]] = risk_input
        current_source_ids[entry["instrument_id"]] = entry["snapshot_path"]
    cycle_reference = coverage.get("cycle_snapshot", {})
    cycle_path = cycle_reference.get("snapshot_path")
    if not cycle_path:
        print("CURRENT INTERNAL STRUCTURE\nFAIL — frozen cycle Raw snapshot is unavailable")
        return 1
    cycle = snapshot_store.load_cycle(cycle_path)
    snapshot_store.require_cycle_members(cycle, current_source_ids)
    cycle_contract_ok = (
        cycle_reference.get("cycle_raw_snapshot_id")
        == cycle.get("cycle_raw_snapshot_id")
        == task8_replay.get("cycle_snapshot", {}).get("cycle_raw_snapshot_id")
    )
    current_bundle_ok = (
        set(current_inputs) == set(source_rules.instrument_ids)
        and all(item.analysis_period.value == "15M" for item in current_inputs.values())
        and all(item.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION for item in current_inputs.values())
    )
    current_end = datetime.fromisoformat(str(source_60m_current["last_completed_bar_end"])).astimezone(SHANGHAI)
    current_start = current_end - timedelta(hours=1)
    frozen_score = deepcopy(source_60m_current["risk_score"])
    current = engine.evaluate(
        current_inputs,
        as_of=current_end,
        period_start=current_start,
        period_end=current_end,
        source_risk_input_ids=current_source_ids,
        source_60m_risk_result=source_60m_current,
        source_60m_risk_result_id=source_60m_path,
    )
    repeated = engine.evaluate(
        current_inputs,
        as_of=current_end,
        period_start=current_start,
        period_end=current_end,
        source_risk_input_ids=current_source_ids,
        source_60m_risk_result=source_60m_current,
        source_60m_risk_result_id=source_60m_path,
    )
    current_deterministic = current.to_dict() == repeated.to_dict()
    current_score_immutable = source_60m_current["risk_score"] == frozen_score == current.linked_60m_risk["risk_score"]

    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    contract = RiskFeatureContract.load(PROJECT_ROOT / "config" / "risk_feature_contract.json")
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    adapter = LongbridgeMarketDataAdapter(LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env"))
    market_data = MarketDataService(registry, (adapter,), cache)
    first_end = datetime.fromisoformat(task8_replay["results"][0]["last_completed_bar_end"])
    last_end = datetime.fromisoformat(task8_replay["results"][-1]["last_completed_bar_end"])
    source_results = _frozen_source_results(
        cache,
        market_data,
        snapshot_store,
        current_source_ids,
    )
    print(f"HISTORICAL 15M RAW CACHE REUSED {len(source_results)}/8")
    if len(source_results) != 8:
        print("HISTORICAL REPLAY\nFAIL — cached history coverage is incomplete")
        return 1

    builder = Historical15mRiskInputBuilder(
        RiskInputAssembler(contract),
        rules,
        source_rules,
        {item: registry.get_instrument(item).asset_type for item in source_rules.instrument_ids},
    )
    periods = builder.build(
        source_results,
        task8_replay["results"],
        source_60m_replay_id=str(task8_replay["append_only_replay_path"]),
    )
    input_store = Market15mRiskInputStore(PROJECT_ROOT / "data" / "risk_inputs" / "market_15m_replay")
    linked_periods = []
    for period in periods:
        input_snapshot = input_store.save_period(
            as_of=period.as_of.isoformat(),
            inputs=period.inputs,
            rules_version=rules.rules_version,
        )
        linked_periods.append(
            replace(
                period,
                source_risk_input_ids=_source_ids(input_snapshot, source_rules.instrument_ids),
            )
        )
    replay = run_internal_replay(engine, tuple(linked_periods))

    last_day = last_end.date()
    prior_60m = next(
        item
        for item in reversed(task8_replay["results"])
        if item["last_completed_bar_end"].startswith(last_day.isoformat())
        and item["last_completed_bar_end"][11:16] == "14:00"
    )
    early_views = []
    for minutes in (30, 45):
        partial_as_of = datetime.combine(last_day, datetime.strptime("14:00", "%H:%M").time(), tzinfo=SHANGHAI) + timedelta(minutes=minutes)
        partial_inputs = builder.build_inputs_at(source_results, trading_day=last_day, as_of=partial_as_of)
        partial_snapshot = input_store.save_period(
            as_of=partial_as_of.isoformat(),
            inputs=partial_inputs,
            rules_version=rules.rules_version,
        )
        view = engine.evaluate(
            partial_inputs,
            as_of=partial_as_of,
            period_start=datetime.combine(last_day, datetime.strptime("14:00", "%H:%M").time(), tzinfo=SHANGHAI),
            period_end=datetime.combine(last_day, datetime.strptime("15:00", "%H:%M").time(), tzinfo=SHANGHAI),
            source_risk_input_ids=_source_ids(partial_snapshot, source_rules.instrument_ids),
            source_60m_risk_result=prior_60m,
            source_60m_risk_result_id=f"{task8_replay['append_only_replay_path']}#period={prior_60m['last_completed_bar_end']}",
        )
        early_views.append(view)

    output_store = Market15mInternalStore(PROJECT_ROOT / "data" / "risk_outputs" / "market_15m_internal")
    human = render_market_15m_internal_report(current)
    machine_path, human_path = output_store.save_result(current, human)
    early_paths = []
    for view in early_views:
        early_paths.append(
            output_store.save_result(
                view,
                render_market_15m_internal_report(view),
                output_type="IN_PROGRESS_INTERNAL_VIEW",
            )[0]
        )
    replay_payload = replay.to_dict()
    replay_payload["rules_version"] = rules.rules_version
    replay_payload["source_60m_rules_version"] = source_rules.rules_version
    replay_payload["current_machine_path"] = machine_path
    replay_payload["current_human_path"] = human_path
    replay_payload["in_progress_machine_paths"] = early_paths
    replay_payload["determinism"] = current_deterministic and replay.deterministic
    replay_payload["cycle_snapshot"] = cycle_reference
    replay_payload["cycle_snapshot_contract"] = "PASS" if cycle_contract_ok else "FAIL"
    replay_path = output_store.save_replay(
        replay_payload,
        last_period_end=current.period_60m_end,
        rules_version=rules.rules_version,
    )
    replay_payload["append_only_replay_path"] = replay_path
    latest_path = PROJECT_ROOT / "data" / "reports" / "market_15m_internal_latest.json"
    latest_path.write_text(json.dumps(replay_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\nCURRENT INTERNAL STRUCTURE")
    print("PASS" if current_bundle_ok and current.status == "READY" else "FAIL")
    print(f"MARKET INTERNAL STATE {current.market_internal_state.value}")
    print(f"60M RISK {current.linked_60m_risk['risk_light']} SCORE {current.linked_60m_risk['risk_score']}")
    print("INDEX STRUCTURES")
    for item in current.index_internal_states:
        print(f"{item.name} {item.classification.value} {' '.join(item.direction_sequence)}")
    print("\nHISTORICAL REPLAY")
    print("PASS" if replay.periods == 80 else "FAIL")
    print(f"PERIODS {replay.periods}")
    print("\nCLASSIFICATION DISTRIBUTION")
    for key, value in replay.classification_distribution.items():
        print(f"{key} {value}")
    print("MARKET STATES", replay.market_state_distribution)
    print("\nORANGE/RED ASSOCIATION", replay.cohort_analysis["ORANGE_RED"])
    print("GREEN ASSOCIATION", replay.cohort_analysis["GREEN"])
    print("\nRISK-UP PRECURSOR")
    print(replay.risk_up_precursors)
    print("\nRISK-DOWN REPAIR PRECURSOR")
    print(replay.risk_down_precursors)
    print("\nIN-PROGRESS SUPPORT")
    print("PASS" if [item.completed_15m_count for item in early_views] == [2, 3] else "FAIL")
    print([(item.completed_15m_count, item.period_status.value) for item in early_views])
    print("\nDETERMINISM")
    print("PASS" if current_deterministic and replay.deterministic else "FAIL")
    print("\n60M SCORE IMMUTABILITY")
    print("PASS" if current_score_immutable and replay.score_immutable else "FAIL")
    print("\nLOOKAHEAD SAFETY")
    print("PASS" if replay.lookahead_safe and current.data_quality["lookahead_safe"] else "FAIL")
    print(f"MACHINE RESULT {machine_path}")
    print(f"HUMAN REPORT {human_path}")
    print(f"APPEND-ONLY REPLAY {replay_path}")
    print(f"LATEST REPORT {latest_path}")
    success = all(
        (
            current_bundle_ok,
            current.status == "READY",
            replay.periods == 80,
            current_deterministic,
            replay.deterministic,
            current_score_immutable,
            replay.score_immutable,
            replay.lookahead_safe,
            current.data_quality["lookahead_safe"],
            [item.completed_15m_count for item in early_views] == [2, 3],
            cycle_contract_ok,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
