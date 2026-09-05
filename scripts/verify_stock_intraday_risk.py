#!/usr/bin/env python3
"""TASK_010 offline-current and 2x80 historical stock risk verification."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
from pathlib import Path
from statistics import median
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.market_internal import (  # noqa: E402
    Historical15mRiskInputBuilder,
    Market15mInternalEngine,
    Market15mInternalRules,
    run_internal_replay,
)
from trend_monitor.market_risk import (  # noqa: E402
    HistoricalRiskInputBuilder,
    Market60mRiskEngine,
    Market60mRiskRules,
    run_replay as run_market_replay,
)
from trend_monitor.providers.longbridge import LongbridgeMarketDataAdapter, LongbridgeProvider  # noqa: E402
from trend_monitor.quality import RiskFeatureContract  # noqa: E402
from trend_monitor.registry import InstrumentRegistry  # noqa: E402
from trend_monitor.risk_input import RiskInputAssembler, RiskInputSnapshotStore, risk_input_from_dict  # noqa: E402
from trend_monitor.schemas import (  # noqa: E402
    DataType,
    ProviderDataResult,
    StockIntradayMonitorResult,
)
from trend_monitor.services import MarketDataService  # noqa: E402
from trend_monitor.stock_risk import (  # noqa: E402
    HistoricalStockRiskInputBuilder,
    Stock15mInternalEngine,
    Stock60mRiskEngine,
    StockInputPeriod,
    StockIntradayOutputStore,
    StockIntradayRiskRules,
    StockRiskInputStore,
    build_reference_observations,
    render_stock_intraday_report,
    run_stock_replay,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _frozen_snapshot_source_results(
    cache: RawCache,
    market_data: MarketDataService,
    snapshot_store: RiskInputSnapshotStore,
    snapshot_paths: dict[str, str],
    *,
    risk_field: str,
    data_type: DataType,
) -> dict[str, ProviderDataResult]:
    entries_by_path = {
        str(Path(entry.path).resolve()): entry
        for entry in cache.entries()
        if entry.data_type is data_type
    }
    results = {}
    for instrument_id, snapshot_path in snapshot_paths.items():
        payload = snapshot_store.load(snapshot_path)
        raw_path = payload[risk_field]["source_trace"].get("raw_path")
        entry = entries_by_path.get(str(Path(raw_path).resolve())) if raw_path else None
        if entry is None:
            raise ValueError(
                f"frozen {risk_field} Raw member is not in cache: {instrument_id}"
            )
        results[instrument_id] = market_data.load_cached(entry)
    return results


def _market_reference_periods(periods, source_replay_id: str):
    previous = {}
    output = []
    for period in periods:
        returns = []
        for instrument_id, risk_input in period.inputs.items():
            close = risk_input.system_bars[-1].close
            prior = previous.get(instrument_id)
            if prior is not None and prior > 0:
                returns.append(close / prior - 1)
            previous[instrument_id] = close
        if len(returns) == 8:
            output.append(
                {
                    "period_end": period.as_of.isoformat(),
                    "market_median_return": float(median(returns)),
                    "source_id": f"{source_replay_id}#reference={period.as_of.isoformat()}",
                }
            )
    return output


def main() -> int:
    rules = StockIntradayRiskRules.load(PROJECT_ROOT / "config" / "stock_intraday_risk_rules.json")
    stock_engine = Stock60mRiskEngine(rules)
    internal_engine = Stock15mInternalEngine(rules)
    task8_path = PROJECT_ROOT / "data" / "reports" / "market_60m_replay_latest.json"
    task9_path = PROJECT_ROOT / "data" / "reports" / "market_15m_internal_latest.json"
    task8 = json.loads(task8_path.read_text(encoding="utf-8"))
    task9 = json.loads(task9_path.read_text(encoding="utf-8"))
    if task8.get("periods") != 80 or task9.get("periods") != 80:
        print("HISTORICAL REPLAY\nFAIL — frozen TASK_008/TASK_009 replay is unavailable")
        return 1
    frozen_market60_results = [dict(item) for item in task8["results"]]
    first_end = datetime.fromisoformat(frozen_market60_results[0]["last_completed_bar_end"])
    last_end = datetime.fromisoformat(frozen_market60_results[-1]["last_completed_bar_end"])

    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    contract = RiskFeatureContract.load(PROJECT_ROOT / "config" / "risk_feature_contract.json")
    assembler = RiskInputAssembler(contract)
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    adapter = LongbridgeMarketDataAdapter(LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env"))
    market_data = MarketDataService(registry, (adapter,), cache)

    coverage = json.loads(
        (PROJECT_ROOT / "data" / "reports" / "market_index_coverage_latest.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot_store = RiskInputSnapshotStore(PROJECT_ROOT / "data" / "risk_inputs")
    latest_risk_input = json.loads(
        (PROJECT_ROOT / "data" / "reports" / "risk_input_latest.json").read_text(
            encoding="utf-8"
        )
    )
    coverage_cycle = coverage.get("cycle_snapshot", {})
    input_cycle = latest_risk_input.get("cycle_snapshot", {})
    cycle_path = coverage_cycle.get("snapshot_path")
    if not cycle_path or coverage_cycle.get("cycle_raw_snapshot_id") != input_cycle.get(
        "cycle_raw_snapshot_id"
    ):
        print("HISTORICAL REPLAY\nFAIL — Market/Stock cycle Raw snapshot differs")
        return 1
    cycle = snapshot_store.load_cycle(cycle_path)
    market_snapshot_paths = {
        str(item["instrument_id"]): str(item["snapshot_path"])
        for item in coverage.get("market_bundle", {}).get("entries", [])
        if item.get("snapshot_path")
    }
    stock_snapshot_paths = {
        instrument_id: str(latest_risk_input["instruments"][instrument_id]["snapshot_path"])
        for instrument_id in rules.instrument_ids
    }
    cycle_instrument_paths = {**market_snapshot_paths, **stock_snapshot_paths}
    snapshot_store.require_cycle_members(cycle, cycle_instrument_paths)
    cycle_contract_ok = (
        cycle.get("cycle_raw_snapshot_id")
        == task8.get("cycle_snapshot", {}).get("cycle_raw_snapshot_id")
        == task9.get("cycle_snapshot", {}).get("cycle_raw_snapshot_id")
    )

    # Historical Current/Replay inputs are bounded by the scheduled market
    # period and reload the exact Raw members frozen by this cycle.
    end_date = last_end.astimezone(SHANGHAI).date()
    start_date = end_date - timedelta(days=130)
    source_market_rules = Market60mRiskRules.load(PROJECT_ROOT / "config" / "market_60m_risk_rules.json")
    market_sources = _frozen_snapshot_source_results(
        cache,
        market_data,
        snapshot_store,
        market_snapshot_paths,
        risk_field="risk_60m",
        data_type=DataType.KLINE_60M,
    )
    print(f"MARKET 60M RAW CACHE REUSED {len(market_sources)}/8")
    if len(market_sources) != 8:
        print("HISTORICAL REPLAY\nFAIL — market reference cache incomplete")
        return 1
    market_builder = HistoricalRiskInputBuilder(market_data, assembler, source_market_rules)
    historical_market_periods = market_builder.build(
        start=start_date,
        end=end_date,
        required_days=82,
        source_results=market_sources,
    )
    current_market_sources = market_sources
    current_market_prefix = market_builder.build_intraday_prefix(
        as_of=last_end,
        source_results=current_market_sources,
    )
    market_periods = tuple(
        item for item in historical_market_periods if item.as_of.date() < last_end.date()
    ) + current_market_prefix
    market_references = _market_reference_periods(market_periods, task8["append_only_replay_path"])

    stock60_sources = _frozen_snapshot_source_results(
        cache,
        market_data,
        snapshot_store,
        stock_snapshot_paths,
        risk_field="risk_60m",
        data_type=DataType.KLINE_60M,
    )
    stock15_sources = _frozen_snapshot_source_results(
        cache,
        market_data,
        snapshot_store,
        stock_snapshot_paths,
        risk_field="support_15m",
        data_type=DataType.KLINE_15M,
    )
    print(f"STOCK 60M RAW CACHE REUSED {len(stock60_sources)}/2")
    print(f"STOCK 15M RAW CACHE REUSED {len(stock15_sources)}/2")
    if len(stock60_sources) != 2 or len(stock15_sources) != 2:
        print("HISTORICAL REPLAY\nFAIL — stock history cache incomplete")
        return 1
    stock_builder = HistoricalStockRiskInputBuilder(
        assembler,
        rules,
        {item: registry.get_instrument(item).asset_type for item in rules.instrument_ids},
    )
    stock_periods = stock_builder.build_60m(stock60_sources, required_days=82)
    references = build_reference_observations(stock_periods, market_references, rules=rules)

    # 002463 has no source 15:00 Closing Bucket on 2026-08-17 and
    # 2026-08-21. The formal contract forbids filling those days, so the
    # uniform two-stock study uses the most recent 20 *common complete* stock
    # days and extends two days earlier. Frozen market rules are evaluated
    # read-only for that exact window; overlapping TASK_008 results must match.
    stock_period_counts = {}
    for item in stock_periods:
        day = item.as_of.date().isoformat()
        stock_period_counts[day] = stock_period_counts.get(day, 0) + 1
    common_days = sorted(day for day, count in stock_period_counts.items() if count == 4)[-20:]
    expanded_market = run_market_replay(
        Market60mRiskEngine(source_market_rules), market_periods, replay_days=22
    )
    selected_market60 = [
        item.to_dict() for item in expanded_market.results if item.trading_date in set(common_days)
    ]
    if len(selected_market60) != 80:
        print("HISTORICAL REPLAY\nFAIL — common-complete market window is not 80 periods")
        return 1
    frozen_by_end = {item["last_completed_bar_end"]: item for item in frozen_market60_results}
    overlap_matches = all(
        item["risk_score"] == frozen_by_end[item["last_completed_bar_end"]]["risk_score"]
        and item["risk_light"] == frozen_by_end[item["last_completed_bar_end"]]["risk_light"]
        and [state["close"] for state in item["index_states"]]
        == [state["close"] for state in frozen_by_end[item["last_completed_bar_end"]]["index_states"]]
        for item in selected_market60
        if item["last_completed_bar_end"] in frozen_by_end
    )
    if not overlap_matches:
        print("HISTORICAL REPLAY\nFAIL — frozen TASK_008 overlap changed")
        return 1
    market60_results = [
        dict(item, _source_replay_id=task8["append_only_replay_path"])
        for item in selected_market60
    ]

    market15_rules = Market15mInternalRules.load(
        PROJECT_ROOT / "config" / "market_15m_internal_rules.json"
    )
    market15_sources = _frozen_snapshot_source_results(
        cache,
        market_data,
        snapshot_store,
        market_snapshot_paths,
        risk_field="support_15m",
        data_type=DataType.KLINE_15M,
    )
    print(f"MARKET 15M RAW CACHE REUSED {len(market15_sources)}/8")
    if len(market15_sources) != 8:
        print("HISTORICAL REPLAY\nFAIL — market 15m context cache incomplete")
        return 1
    market15_builder = Historical15mRiskInputBuilder(
        assembler,
        market15_rules,
        source_market_rules,
        {item: registry.get_instrument(item).asset_type for item in source_market_rules.instrument_ids},
    )
    market15_periods = market15_builder.build(
        market15_sources,
        market60_results,
        source_60m_replay_id=f"{task8['append_only_replay_path']}#common_complete_window",
    )
    market15_replay = run_internal_replay(
        Market15mInternalEngine(market15_rules, source_market_rules), market15_periods
    )
    market15_results = [
        dict(item.to_dict(), _source_replay_id=task9["append_only_replay_path"])
        for item in market15_replay.results
    ]

    stock15_by_end = {}
    for market_result in market60_results:
        end = datetime.fromisoformat(str(market_result["last_completed_bar_end"]))
        stock15_by_end[end.isoformat()] = stock_builder.build_15m_at(
            stock15_sources, trading_day=end.date(), as_of=end
        )

    input_store = StockRiskInputStore(PROJECT_ROOT / "data" / "risk_inputs" / "stock_intraday_replay")
    period_by_end = {item.as_of.isoformat(): item for item in stock_periods}
    linked_periods = []
    market_context_source_ids = {}
    for end_text, inputs15 in stock15_by_end.items():
        source_period = period_by_end[end_text]
        path = input_store.save_period(
            as_of=end_text,
            inputs_60m=source_period.inputs,
            inputs_15m=inputs15,
            rules_version=rules.rules_version,
            market_60m_result=next(
                item for item in market60_results if item["last_completed_bar_end"] == end_text
            ),
            market_15m_result=next(
                item for item in market15_results if item["60m_period_end"] == end_text
            ),
        )
        market_context_source_ids[end_text] = path
        linked_periods.append(
            StockInputPeriod(
                source_period.as_of,
                source_period.inputs,
                {item: f"{path}#instrument={item}&period=60m" for item in rules.instrument_ids},
            )
        )
    market60_results = [
        dict(item, _source_context_id=f"{market_context_source_ids[item['last_completed_bar_end']]}#market_60m_result")
        for item in market60_results
    ]
    market15_results = [
        dict(item, _source_context_id=f"{market_context_source_ids[item['60m_period_end']]}#market_15m_result")
        for item in market15_results
    ]
    replay_first_end = datetime.fromisoformat(market60_results[0]["last_completed_bar_end"])
    warmup = tuple(item for item in stock_periods if item.as_of < replay_first_end)
    replay_periods = tuple(warmup) + tuple(sorted(linked_periods, key=lambda item: item.as_of))
    replay = run_stock_replay(
        stock_engine,
        internal_engine,
        stock_60m_periods=replay_periods,
        stock_15m_inputs=stock15_by_end,
        references=references,
        market_60m_results=market60_results,
        market_15m_results=market15_results,
    )
    superseded_incomplete = input_store.mark_incomplete_attempts_superseded()

    source_market60_path = str(task8["current_machine_path"])
    source_market15_path = str(task9["current_machine_path"])
    market60_current = json.loads(Path(source_market60_path).read_text(encoding="utf-8"))
    market15_current = json.loads(Path(source_market15_path).read_text(encoding="utf-8"))
    output_store = StockIntradayOutputStore(PROJECT_ROOT / "data" / "risk_outputs")
    current_monitors = {}
    current_paths = {}
    current_deterministic = True
    pipeline_match = True
    for instrument_id in rules.instrument_ids:
        entry = latest_risk_input["instruments"][instrument_id]
        payload = snapshot_store.load(entry["snapshot_path"])
        current60_input = risk_input_from_dict(payload["risk_60m"])
        current15_input = risk_input_from_dict(payload["support_15m"])
        prior_result = replay.results[instrument_id][-2].stock_60m
        historical = tuple(item for item in references[instrument_id] if item.period_end < last_end.isoformat())
        kwargs = {
            "history": historical,
            "market_60m_result": market60_current,
            "market_15m_result": market15_current,
            "source_stock_risk_input_id": entry["snapshot_path"],
            "source_market_60m_result_id": source_market60_path,
            "source_market_15m_result_id": source_market15_path,
            "previous_result": prior_result,
        }
        current60 = stock_engine.evaluate(current60_input, **kwargs)
        repeated60 = stock_engine.evaluate(current60_input, **kwargs)
        current15 = internal_engine.evaluate(
            current15_input,
            as_of=last_end,
            period_start=last_end - timedelta(hours=1),
            period_end=last_end,
            source_stock_risk_input_id=entry["snapshot_path"],
            market_15m_result=market15_current,
            source_market_15m_result_id=source_market15_path,
        )
        repeated15 = internal_engine.evaluate(
            current15_input,
            as_of=last_end,
            period_start=last_end - timedelta(hours=1),
            period_end=last_end,
            source_stock_risk_input_id=entry["snapshot_path"],
            market_15m_result=market15_current,
            source_market_15m_result_id=source_market15_path,
        )
        current_deterministic = current_deterministic and current60.to_dict() == repeated60.to_dict()
        current_deterministic = current_deterministic and current15.to_dict() == repeated15.to_dict()
        replay_last = replay.results[instrument_id][-1].stock_60m
        pipeline_match = pipeline_match and (
            current60.period_end == replay_last.period_end
            and current60.current_close == replay_last.current_close
            and current60.risk_score == replay_last.risk_score
            and current60.risk_light == replay_last.risk_light
        )
        monitor = StockIntradayMonitorResult(
            instrument_id=instrument_id,
            symbol=current60.symbol,
            stock_60m_risk=current60,
            stock_15m_internal=current15,
            market_60m_context=current60.market_context,
            market_15m_context={
                "market_internal_state": market15_current["market_internal_state"],
                "source_result_id": source_market15_path,
            },
        )
        paths = output_store.save_monitor(monitor, render_stock_intraday_report(monitor))
        current_monitors[instrument_id] = monitor
        current_paths[instrument_id] = paths

    early_support = []
    for minutes in (30, 45):
        as_of = datetime.combine(last_end.date(), time(14, 0), tzinfo=SHANGHAI) + timedelta(minutes=minutes)
        inputs = stock_builder.build_15m_at(stock15_sources, trading_day=last_end.date(), as_of=as_of)
        for instrument_id in rules.instrument_ids:
            early = internal_engine.evaluate(
                inputs[instrument_id],
                as_of=as_of,
                period_start=last_end - timedelta(hours=1),
                period_end=last_end,
                source_stock_risk_input_id=f"{inputs[instrument_id].source_trace.raw_path}#as_of={as_of.isoformat()}",
                market_15m_result=None,
                source_market_15m_result_id=None,
            )
            early_support.append(early)

    replay_payload = replay.to_dict()
    replay_payload.update(
        {
            "rules_version": rules.rules_version,
            "internal_rules_version": rules.internal_rules_version,
            "source_market_60m_replay_id": task8["append_only_replay_path"],
            "source_market_15m_replay_id": task9["append_only_replay_path"],
            "replay_common_complete_days": common_days,
            "excluded_incomplete_stock_days": {
                "stock.wus_printed_circuit": ["2026-08-17", "2026-08-21"]
            },
            "frozen_market_overlap_match": overlap_matches,
            "superseded_incomplete_input_snapshots": superseded_incomplete,
            "current_paths": current_paths,
            "current_pipeline_match": pipeline_match,
            "determinism": replay.deterministic and current_deterministic,
            "in_progress_support": [item.to_dict() for item in early_support],
            "cycle_snapshot": coverage_cycle,
            "cycle_snapshot_contract": "PASS" if cycle_contract_ok else "FAIL",
        }
    )
    replay_path = output_store.save_replay(
        replay_payload, period_end=last_end.isoformat(), rules_version=rules.rules_version
    )
    replay_payload["append_only_replay_path"] = replay_path
    latest_path = PROJECT_ROOT / "data" / "reports" / "stock_intraday_risk_latest.json"
    latest_path.write_text(json.dumps(replay_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for instrument_id in rules.instrument_ids:
        value = current_monitors[instrument_id]
        risk, internal = value.stock_60m_risk, value.stock_15m_internal
        print(f"\n{risk.symbol} {risk.name}")
        print(f"60M RISK {risk.risk_light_symbol} {risk.risk_light.value} SCORE {risk.risk_score} {risk.risk_direction.value}")
        print(f"15M INTERNAL {internal.classification.value} {' '.join(internal.direction_sequence)}")
        print(
            f"MARKET CONTEXT {risk.market_context['market_risk_light']} SCORE {risk.market_context['market_risk_score']} "
            f"{risk.market_context['market_internal_state']}"
        )
        print(f"CONFIDENCE {risk.confidence.value}")
    print("\nHISTORICAL REPLAY")
    print("PASS" if replay.observations == 160 else "FAIL")
    print(f"{replay.observations} OBSERVATIONS")
    print("\nRISK DISTRIBUTION")
    for instrument_id, stats in replay.stats.items():
        print(rules.identity(instrument_id)[0], stats["risk_distribution"])
    print("\nMARKET RESONANCE")
    for instrument_id, stats in replay.market_resonance_study.items():
        print(rules.identity(instrument_id)[0], stats)
    print("\n15M PRECURSOR")
    for instrument_id in rules.instrument_ids:
        print(rules.identity(instrument_id)[0], "UP", replay.risk_up_precursors[instrument_id])
        print(rules.identity(instrument_id)[0], "DOWN", replay.risk_down_precursors[instrument_id])
    print("\nIN-PROGRESS SUPPORT")
    early_ok = sorted(item.completed_15m_count for item in early_support) == [2, 2, 3, 3]
    print("PASS" if early_ok else "FAIL")
    print("\nDETERMINISM")
    print("PASS" if replay.deterministic and current_deterministic else "FAIL")
    print("\nLOOKAHEAD")
    print("PASS" if replay.lookahead_safe else "FAIL")
    print("\n60M SCORE IMMUTABILITY")
    print("PASS" if replay.score_immutable else "FAIL")
    print("\nCURRENT PIPELINE/REPLAY MATCH")
    print("PASS" if pipeline_match else "FAIL")
    print(f"APPEND-ONLY REPLAY {replay_path}")
    print(f"SUPERSEDED INCOMPLETE INPUT SNAPSHOTS {superseded_incomplete}")
    print(f"LATEST REPORT {latest_path}")
    success = all(
        (
            replay.observations == 160,
            early_ok,
            replay.deterministic,
            current_deterministic,
            replay.lookahead_safe,
            replay.score_immutable,
            pipeline_match,
            all(item.stock_60m_risk and item.stock_60m_risk.confidence.value == "HIGH" for item in current_monitors.values()),
            cycle_contract_ok,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
