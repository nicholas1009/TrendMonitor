#!/usr/bin/env python3
"""TASK_006 real Risk Input Assembly and Preflight verification."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.providers.hithink import HithinkProvider  # noqa: E402
from trend_monitor.providers.hithink.adapter import HithinkMarketDataAdapter  # noqa: E402
from trend_monitor.providers.longbridge import LongbridgeMarketDataAdapter, LongbridgeProvider  # noqa: E402
from trend_monitor.quality import RiskFeatureContract  # noqa: E402
from trend_monitor.registry import InstrumentRegistry, MappingType  # noqa: E402
from trend_monitor.risk_input import RiskInputService, RiskInputSnapshotStore  # noqa: E402
from trend_monitor.schemas import DataType, GroupEntry, PreflightStatus, RiskInputGroup  # noqa: E402
from trend_monitor.services import MarketDataService  # noqa: E402
from trend_monitor.transformation import latest_completed_60m_period_end  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
REAL_INSTRUMENTS = (
    "stock.hengtong_optic",
    "stock.wus_printed_circuit",
    "index.csi500",
    "index.star50",
)
MARKET_INDEXES = (
    "index.sse_composite",
    "index.sse50",
    "index.csi300",
    "index.csi500",
    "index.csi_free_float",
    "index.chinext",
    "index.csi1000",
    "index.star50",
)


def label(status: PreflightStatus) -> str:
    return {
        PreflightStatus.PASS: "PASS",
        PreflightStatus.PASS_WITH_DEGRADATION: "DEGRADED",
        PreflightStatus.BLOCKED: "BLOCKED",
    }[status]


def feature_check(bundle) -> bool:
    for risk in (bundle.risk_60m, bundle.support_15m):
        enabled = {item.feature_name for item in risk.feature_inputs}
        disabled = {item.feature_name for item in risk.disabled_features}
        if "current_period_close" not in enabled:
            return False
        if "precise_high_low_break" not in disabled:
            return False
        if bundle.asset_type.value == "index" and "index_volume_signal" not in disabled:
            return False
    return True


def provenance_check(bundle) -> bool:
    for risk in (bundle.daily, bundle.risk_60m, bundle.support_15m):
        if not risk.source_trace.raw_path or not risk.system_bars:
            return False
        for bar in risk.system_bars:
            if not bar.source_raw_paths or not bar.source_bar_ids:
                return False
        # A disabled feature can legitimately have no lineage when its input
        # does not yet exist (notably previous-period features at 10:30).
        for feature in (*risk.feature_inputs, *risk.degraded_features):
            if not feature.lineage:
                return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="Timezone-aware ISO timestamp for the Risk Input snapshot.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider_observed_at = (
        datetime.fromisoformat(args.as_of).astimezone(SHANGHAI)
        if args.as_of
        else datetime.now(SHANGHAI)
    )
    as_of = latest_completed_60m_period_end(provider_observed_at)
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    contract = RiskFeatureContract.load(PROJECT_ROOT / "config" / "risk_feature_contract.json")
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    longbridge_provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    market_data = MarketDataService(
        registry,
        (
            LongbridgeMarketDataAdapter(longbridge_provider),
            HithinkMarketDataAdapter(HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env"))),
        ),
        cache,
    )
    service = RiskInputService(market_data, contract)
    store = RiskInputSnapshotStore(PROJECT_ROOT / "data" / "risk_inputs")
    report = {
        "generated_at": provider_observed_at.isoformat(),
        "analysis_as_of": as_of.isoformat(),
        "provider_observed_at": provider_observed_at.isoformat(),
        "timezone": "Asia/Shanghai",
        "requested_provider": "longbridge",
        "instruments": {},
        "market_bundle": {},
        "stock_bundle": {},
    }
    bundles = {}
    snapshot_paths = {}
    failures = 0

    # The downstream frozen 80-period stock replay needs one rolling cache
    # entry that covers both its historical window and today's completed bars.
    # build_bundle() intentionally fetches only the current-day window.
    history_start = as_of.date() - timedelta(days=130)
    history_end = as_of.date()
    refreshed_stock_results = {}
    for instrument_id in REAL_INSTRUMENTS[:2]:
        mapping = registry.resolve(instrument_id, "longbridge")
        refreshed_stock_results[instrument_id] = {}
        for period in ("15m", "60m"):
            raw = longbridge_provider.get_history_candlesticks(
                mapping.provider_symbol,
                period=period,
                start=history_start,
                end=history_end,
            )
            entry = cache.save(
                instrument_id=instrument_id,
                provider="longbridge",
                provider_symbol=mapping.provider_symbol,
                data_type=DataType(period),
                raw=raw,
                request_start=int(
                    datetime.combine(history_start, datetime.min.time(), tzinfo=SHANGHAI).timestamp()
                    * 1000
                ),
                request_end=int(
                    datetime.combine(history_end, datetime.max.time(), tzinfo=SHANGHAI).timestamp()
                    * 1000
                ),
            )
            refreshed_stock_results[instrument_id][period] = market_data.load_cached(entry)

    for instrument_id in REAL_INSTRUMENTS:
        print(f"[ASSEMBLE] {instrument_id}")
        bundle = service.build_bundle(
            instrument_id,
            as_of=as_of,
            requested_provider="longbridge",
            fallback_providers=("hithink",),
            minute_results=refreshed_stock_results.get(instrument_id),
        )
        bundles[instrument_id] = bundle
        snapshot_path = store.save_bundle(bundle)
        snapshot_paths[instrument_id] = snapshot_path
        field_ok = feature_check(bundle)
        provenance_ok = provenance_check(bundle)
        replay_ok = store.load(snapshot_path) == bundle.to_dict()
        if bundle.preflight_status is PreflightStatus.BLOCKED or not all((field_ok, provenance_ok, replay_ok)):
            failures += 1
            block_reasons = []
            if bundle.preflight_status is PreflightStatus.BLOCKED:
                block_reasons.append("PREFLIGHT_BLOCKED")
            if not field_ok:
                block_reasons.append("FIELD_CONTRACT_FAILED")
            if not provenance_ok:
                block_reasons.append("PROVENANCE_FAILED")
            if not replay_ok:
                block_reasons.append("SNAPSHOT_REPLAY_FAILED")
            print(
                f"  RISK INPUT BLOCKED — instrument={instrument_id}; "
                f"block_reason={','.join(block_reasons)}"
            )
        print(f"  DAILY FORMAL INPUT: {label(bundle.daily.preflight_status)}")
        print(
            f"  60M SYSTEM INPUT: {label(bundle.risk_60m.preflight_status)} — "
            f"bars={len(bundle.risk_60m.system_bars)}; date={bundle.risk_60m.trading_date}"
        )
        print(
            f"  15M SUPPORT INPUT: {label(bundle.support_15m.preflight_status)} — "
            f"bars={len(bundle.support_15m.system_bars)}; date={bundle.support_15m.trading_date}"
        )
        print(f"  FIELD CONTRACT: {'PASS' if field_ok else 'FAIL'}")
        print(f"  FEATURE DEGRADATION: {'PASS' if bundle.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION else 'FAIL'}")
        print(f"  PROVENANCE: {'PASS' if provenance_ok else 'FAIL'}")
        print(f"  SNAPSHOT REPLAY: {'PASS' if replay_ok else 'FAIL'}")
        report["instruments"][instrument_id] = {
            "snapshot_path": snapshot_path,
            "preflight_status": bundle.preflight_status.value,
            "data_status": bundle.data_status.value,
            "daily": bundle.daily.to_dict(),
            "60m": bundle.risk_60m.to_dict(),
            "15m": bundle.support_15m.to_dict(),
            "field_contract": "PASS" if field_ok else "FAIL",
            "provenance": "PASS" if provenance_ok else "FAIL",
            "snapshot_replay": "PASS" if replay_ok else "FAIL",
        }

    market_entries = []
    for instrument_id in MARKET_INDEXES:
        mapping = registry.resolve(instrument_id, "longbridge")
        if instrument_id in snapshot_paths:
            market_entries.append(GroupEntry(instrument_id, "READY", None, snapshot_paths[instrument_id]))
        elif mapping.mapping_type is MappingType.UNMAPPED:
            market_entries.append(GroupEntry(instrument_id, "UNAVAILABLE", "UNMAPPED", None))
        else:
            market_entries.append(GroupEntry(instrument_id, "UNAVAILABLE", "NOT_VERIFIED_FOR_MINUTE", None))
    market_group = RiskInputGroup("market_risk_input", as_of.isoformat(), tuple(market_entries))
    market_path = store.save_group(market_group)
    stock_entries = tuple(
        GroupEntry(instrument_id, "READY", None, snapshot_paths[instrument_id])
        for instrument_id in REAL_INSTRUMENTS[:2]
    )
    stock_group = RiskInputGroup("stock_risk_input", as_of.isoformat(), stock_entries)
    stock_path = store.save_group(stock_group)
    report["market_bundle"] = {"snapshot_path": market_path, **market_group.to_dict()}
    report["stock_bundle"] = {"snapshot_path": stock_path, **stock_group.to_dict()}

    report_path = PROJECT_ROOT / "data" / "reports" / "risk_input_latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"DAILY FORMAL INPUT: {'PASS' if all(item.daily.preflight_status is PreflightStatus.PASS for item in bundles.values()) else 'FAIL'}")
    print(f"60M SYSTEM INPUT: {'DEGRADED' if all(item.risk_60m.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION for item in bundles.values()) else 'BLOCKED'}")
    print(f"15M SUPPORT INPUT: {'DEGRADED' if all(item.support_15m.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION for item in bundles.values()) else 'BLOCKED'}")
    print(f"FIELD CONTRACT: {'PASS' if all(feature_check(item) for item in bundles.values()) else 'FAIL'}")
    print(f"FEATURE DEGRADATION: {'PASS' if all(item.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION for item in bundles.values()) else 'FAIL'}")
    print(f"PROVENANCE: {'PASS' if all(provenance_check(item) for item in bundles.values()) else 'FAIL'}")
    print(f"REPORT: {report_path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
