#!/usr/bin/env python3
"""TASK_008 real current Market 60m risk and 20-day replay verification."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.market_risk import (  # noqa: E402
    HistoricalRiskInputBuilder,
    Market60mRiskEngine,
    Market60mRiskRules,
    MarketRiskOutputStore,
    render_market_60m_report,
    run_replay,
)
from trend_monitor.providers.longbridge import LongbridgeMarketDataAdapter, LongbridgeProvider  # noqa: E402
from trend_monitor.quality import RiskFeatureContract  # noqa: E402
from trend_monitor.registry import InstrumentRegistry  # noqa: E402
from trend_monitor.risk_input import RiskInputAssembler, RiskInputSnapshotStore, risk_input_from_dict  # noqa: E402
from trend_monitor.schemas import (  # noqa: E402
    DataType,
    PreflightStatus,
    ProviderDataResult,
    ProviderResultMetadata,
    SourceTrace,
)
from trend_monitor.services import MarketDataService  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _semantic_view(result) -> dict[str, object]:
    value = result.to_dict()
    return {
        "trading_date": value["trading_date"],
        "last_completed_bar_end": value["last_completed_bar_end"],
        "risk_score": value["risk_score"],
        "risk_light": value["risk_light"],
        "risk_direction": value["risk_direction"],
        "signal_confidence": value["signal_confidence"],
        "breadth": value["breadth"],
        "persistent_weakness": value["persistent_weakness"],
        "downside_shocks": value["downside_shocks"],
        "weighted_support_distortion": value["weighted_support_distortion"],
        "broad_repair": value["broad_repair"],
        "score_components": value["score_components"],
        "index_states": [
            {
                key: item[key]
                for key in (
                    "instrument_id",
                    "close",
                    "close_change_pct",
                    "one_period_direction",
                    "two_period_direction",
                    "three_period_close_direction",
                    "persistent_weak",
                    "repair_state",
                    "downside_shock",
                    "shock_reference_p95",
                )
            }
            for item in value["index_states"]
        ],
    }


def _first_diff(current: object, replay: object, path: str = "") -> dict[str, object] | None:
    if isinstance(current, dict) and isinstance(replay, dict):
        for key in sorted(set(current) | set(replay)):
            diff = _first_diff(
                current.get(key), replay.get(key), f"{path}.{key}" if path else key
            )
            if diff is not None:
                return diff
        return None
    if isinstance(current, list) and isinstance(replay, list):
        for index, (left, right) in enumerate(zip(current, replay)):
            diff = _first_diff(left, right, f"{path}[{index}]")
            if diff is not None:
                return diff
        if len(current) != len(replay):
            return {"field": f"{path}.length", "current": len(current), "replay": len(replay)}
        return None
    if current != replay:
        return {"field": path, "current": current, "replay": replay}
    return None


def _cached_history_results(
    cache: RawCache,
    registry: InstrumentRegistry,
    adapter: LongbridgeMarketDataAdapter,
    rules: Market60mRiskRules,
    *,
    start,
    end,
) -> dict[str, ProviderDataResult]:
    """Revalidate and reuse append-only Raw history before requesting Provider."""
    request_start = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp() * 1000)
    request_end = int(datetime.combine(end, time.max, tzinfo=timezone.utc).timestamp() * 1000)
    candidates = {}
    for order, entry in enumerate(cache.entries()):
        if (
            entry.instrument_id not in rules.instrument_ids
            or entry.provider != "longbridge"
            or entry.data_type is not DataType.KLINE_60M
            or entry.request_start is None
            or entry.request_end is None
            or entry.request_start > request_start
            or entry.request_end < request_end
            or not Path(entry.path).is_file()
        ):
            continue
        current = candidates.get(entry.instrument_id)
        rank = (entry.fetched_at, order)
        if current is None or rank > current[0]:
            candidates[entry.instrument_id] = (rank, entry)

    results = {}
    for instrument_id in rules.instrument_ids:
        selected = candidates.get(instrument_id)
        if selected is None:
            continue
        entry = selected[1]
        instrument = registry.get_instrument(instrument_id)
        mapping = registry.resolve(instrument_id, "longbridge")
        raw = cache.load(entry)
        trace = SourceTrace(
            provider="longbridge",
            provider_symbol=entry.provider_symbol,
            raw_path=entry.path,
            fetched_at=entry.fetched_at,
            source_timestamp=entry.source_timestamp,
        )
        normalized = adapter.normalize_bars(raw, instrument, mapping, trace, period="60m")
        results[instrument_id] = ProviderDataResult(
            raw=raw,
            normalized=tuple(normalized),
            metadata=ProviderResultMetadata(
                provider="longbridge",
                provider_symbol=entry.provider_symbol,
                instrument_id=instrument_id,
                fetched_at=entry.fetched_at,
                source_timestamp=entry.source_timestamp,
                data_type=DataType.KLINE_60M,
                mapping_type=mapping.mapping_type.value,
                requested_provider="longbridge",
                actual_provider="longbridge",
                fallback_used=False,
                fallback_reason=None,
                raw_path=entry.path,
            ),
        )
    return results


def _current_source_results(
    cache: RawCache,
    market_data: MarketDataService,
    current_inputs: dict[str, object],
) -> dict[str, ProviderDataResult]:
    """Reload the exact Raw entries referenced by the current Risk Inputs."""
    entries_by_path = {
        str(Path(entry.path).resolve()): entry
        for entry in cache.entries()
        if entry.data_type is DataType.KLINE_60M
    }
    results = {}
    for instrument_id, risk_input in current_inputs.items():
        raw_path = getattr(getattr(risk_input, "source_trace"), "raw_path")
        entry = entries_by_path.get(str(Path(raw_path).resolve())) if raw_path else None
        if entry is None:
            raise ValueError(f"current 60m Raw snapshot is not in cache: {instrument_id}")
        results[instrument_id] = market_data.load_cached(entry)
    return results


def main() -> int:
    rules = Market60mRiskRules.load(PROJECT_ROOT / "config" / "market_60m_risk_rules.json")
    engine = Market60mRiskEngine(rules)
    coverage_report_path = PROJECT_ROOT / "data" / "reports" / "market_index_coverage_latest.json"
    coverage = json.loads(coverage_report_path.read_text(encoding="utf-8"))
    if coverage.get("market_bundle", {}).get("coverage") != "FULL_READY":
        print("CURRENT MARKET BUNDLE")
        print("FAIL — TASK_007 coverage is not FULL_READY")
        return 1
    snapshot_store = RiskInputSnapshotStore(PROJECT_ROOT / "data" / "risk_inputs")
    current_inputs = {}
    current_source_ids = {}
    for entry in coverage["market_bundle"]["entries"]:
        instrument_id = entry["instrument_id"]
        snapshot_path = entry["snapshot_path"]
        payload = snapshot_store.load(snapshot_path)
        current_inputs[instrument_id] = risk_input_from_dict(payload["risk_60m"])
        current_source_ids[instrument_id] = snapshot_path
    current_bundle_ok = (
        set(current_inputs) == set(rules.instrument_ids)
        and all(item.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION for item in current_inputs.values())
        and len({item.last_completed_bar_end for item in current_inputs.values()}) == 1
    )
    print("CURRENT MARKET BUNDLE")
    print("PASS" if current_bundle_ok else "FAIL")
    if not current_bundle_ok:
        return 1

    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    contract = RiskFeatureContract.load(PROJECT_ROOT / "config" / "risk_feature_contract.json")
    raw_cache = RawCache(PROJECT_ROOT / "data" / "raw")
    longbridge_adapter = LongbridgeMarketDataAdapter(
        LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    )
    market_data = MarketDataService(
        registry,
        (longbridge_adapter,),
        raw_cache,
    )
    builder = HistoricalRiskInputBuilder(market_data, RiskInputAssembler(contract), rules)
    end = datetime.now(SHANGHAI).date()
    # 130 calendar days currently yields 90 complete A-share trading days,
    # safely above the 60-day baseline + 20-day replay requirement while
    # keeping each official history request well below the 1000-bar limit.
    start = end - timedelta(days=130)
    cached_results = _cached_history_results(
        raw_cache,
        registry,
        longbridge_adapter,
        rules,
        start=start,
        end=end,
    )
    print(f"HISTORICAL RAW CACHE REUSED {len(cached_results)}/8")
    historical_periods = builder.build(
        start=start,
        end=end,
        required_days=80,
        source_results=cached_results,
    )
    current_period_end = datetime.fromisoformat(
        next(iter(current_inputs.values())).last_completed_bar_end or ""
    ).astimezone(SHANGHAI)
    current_source_results = _current_source_results(
        raw_cache, market_data, current_inputs
    )
    intraday_periods = builder.build_intraday_prefix(
        as_of=current_period_end,
        source_results=current_source_results,
    )
    periods = tuple(
        item for item in historical_periods if item.as_of.date() < current_period_end.date()
    ) + intraday_periods
    replay = run_replay(engine, periods, replay_days=20)

    history = defaultdict(list)
    for period in periods[:-1]:
        for instrument_id, risk_input in period.inputs.items():
            history[instrument_id].append(risk_input)
    previous = replay.results[-2] if len(replay.results) >= 2 else None
    current = engine.evaluate(
        current_inputs,
        history_inputs=history,
        source_snapshot_ids=current_source_ids,
        previous_result=previous,
    )
    repeated = engine.evaluate(
        current_inputs,
        history_inputs=history,
        source_snapshot_ids=current_source_ids,
        previous_result=previous,
    )
    determinism = replay.deterministic and current.to_dict() == repeated.to_dict()
    replay_current = replay.results[-1]
    first_diff = _first_diff(_semantic_view(current), _semantic_view(replay_current))
    pipeline_match = first_diff is None
    human = render_market_60m_report(current)
    output_store = MarketRiskOutputStore(PROJECT_ROOT / "data" / "risk_outputs" / "market_60m")
    machine_path, human_path = output_store.save(current, human)
    replay_path = PROJECT_ROOT / "data" / "reports" / "market_60m_replay_latest.json"
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    replay_payload = replay.to_dict()
    replay_payload["schema_version"] = 1
    replay_payload["rules_version"] = rules.rules_version
    replay_payload["current_machine_path"] = machine_path
    replay_payload["current_human_path"] = human_path
    replay_payload["current_pipeline_match"] = pipeline_match
    replay_payload["current_pipeline_first_diff"] = first_diff
    append_only_replay_path = output_store.save_replay(
        replay_payload,
        last_completed_bar_end=current.last_completed_bar_end,
        rules_version=rules.rules_version,
    )
    replay_payload["append_only_replay_path"] = append_only_replay_path
    replay_path.write_text(json.dumps(replay_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print("RISK ENGINE")
    print("PASS" if current.status == "READY" else "FAIL")
    print()
    print("RISK SCORE")
    print(current.risk_score)
    print(current.score_components)
    print()
    print("RISK LIGHT")
    print(f"{current.risk_light_symbol} {current.risk_light.value if current.risk_light else 'BLOCKED'}")
    print()
    print("CONFIDENCE")
    print(current.signal_confidence.value)
    print()
    print("HISTORICAL REPLAY")
    print("PASS" if replay.periods == 80 else "FAIL")
    print(f"PERIODS {replay.periods}")
    for key in ("GREEN", "YELLOW", "ORANGE", "RED"):
        print(f"{key} {replay.stats[key]}")
    print(f"RISK_RISING {replay.stats['RISK_RISING']}")
    print(f"RISK_FALLING {replay.stats['RISK_FALLING']}")
    print(f"WEIGHTED_SUPPORT_DISTORTION {replay.stats['WEIGHTED_SUPPORT_DISTORTION']}")
    print(f"BROAD_SELLOFF_RESONANCE {replay.stats['BROAD_SELLOFF_RESONANCE']}")
    print()
    print("DETERMINISM")
    print("PASS" if determinism else "FAIL")
    print()
    print("LOOKAHEAD CHECK")
    print("PASS" if replay.lookahead_safe else "FAIL")
    print()
    print("CURRENT PIPELINE/REPLAY MATCH")
    print("PASS" if pipeline_match else "FAIL")
    if first_diff is not None:
        print(
            "FIRST DIFF — "
            f"field={first_diff['field']}; current={first_diff['current']}; "
            f"replay={first_diff['replay']}"
        )
    print(f"MACHINE RESULT {machine_path}")
    print(f"HUMAN REPORT {human_path}")
    print(f"APPEND-ONLY REPLAY {append_only_replay_path}")
    print(f"REPLAY REPORT {replay_path}")
    success = all(
        (
            current_bundle_ok,
            current.status == "READY",
            replay.periods == 80,
            determinism,
            replay.lookahead_safe,
            pipeline_match,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
