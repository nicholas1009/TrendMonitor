#!/usr/bin/env python3
"""TASK_007 real six-index mapping, minute, and Risk Input verification."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.normalization.longbridge import normalize_longbridge_candlesticks  # noqa: E402
from trend_monitor.providers.hithink import HithinkProvider  # noqa: E402
from trend_monitor.providers.hithink.adapter import HithinkMarketDataAdapter  # noqa: E402
from trend_monitor.providers.longbridge import (  # noqa: E402
    LongbridgeMarketDataAdapter,
    LongbridgeProvider,
)
from trend_monitor.quality import RiskFeatureContract  # noqa: E402
from trend_monitor.registry import (  # noqa: E402
    InstrumentRegistry,
    MappingConfidence,
    MappingStatus,
    MappingType,
)
from trend_monitor.risk_input import (  # noqa: E402
    MARKET_INDEXES,
    RiskInputService,
    RiskInputSnapshotStore,
    build_market_risk_group,
    market_coverage_status,
)
from trend_monitor.schemas import DataType, PreflightStatus, SourceTrace  # noqa: E402
from trend_monitor.services import MarketDataService  # noqa: E402
from trend_monitor.transformation import build_system_bars  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_INDEXES = (
    ("index.sse_composite", "上证指数", "000001.SH"),
    ("index.sse50", "上证50", "000016.SH"),
    ("index.csi300", "沪深300", "000300.SH"),
    ("index.csi_free_float", "中证流通", "000902.SH"),
    ("index.chinext", "创业板指", "399006.SZ"),
    ("index.csi1000", "中证1000", "000852.SH"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--as-of",
        help="Timezone-aware ISO timestamp used to cap the live Risk Input snapshot.",
    )
    return parser.parse_args()


def expected_completed_bars(as_of: datetime) -> tuple[int, int]:
    """Return completed 15m/60m System Bar counts at a scheduled boundary."""

    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    clock = as_of.astimezone(SHANGHAI).timetz().replace(tzinfo=None)
    if clock >= time(15, 0):
        return 16, 4
    if clock >= time(14, 0):
        return 12, 3
    if clock >= time(11, 30):
        return 8, 2
    if clock >= time(10, 30):
        return 4, 1
    raise ValueError("no completed 60m monitoring period at as_of")


def within_live_readiness_window(as_of: datetime) -> bool:
    """Match the existing scheduled+10 minute live grace, without adding a loop."""

    clock = as_of.astimezone(SHANGHAI).timetz().replace(tzinfo=None)
    for boundary in (time(10, 30), time(11, 30), time(14, 0), time(15, 0)):
        end = datetime.combine(as_of.date(), boundary, tzinfo=SHANGHAI)
        scheduled = end + timedelta(minutes=3)
        if scheduled.time() <= clock <= (scheduled + timedelta(minutes=10)).time():
            return True
    return False


def source_day(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(SHANGHAI).date().isoformat()


def feature_contract_ok(bundle) -> bool:
    for item in (bundle.risk_60m, bundle.support_15m):
        enabled = {feature.feature_name for feature in item.feature_inputs}
        disabled = {feature.feature_name for feature in item.disabled_features}
        if "current_period_close" not in enabled or "index_volume_signal" not in disabled:
            return False
    return True


def provenance_ok(bundle) -> bool:
    for item in (bundle.daily, bundle.risk_60m, bundle.support_15m):
        if not item.source_trace.raw_path or not item.system_bars:
            return False
        if any(not bar.source_raw_paths or not bar.source_bar_ids for bar in item.system_bars):
            return False
        features = (*item.feature_inputs, *item.degraded_features, *item.disabled_features)
        if any(not feature.lineage for feature in features):
            return False
    return True


def main() -> int:
    args = parse_args()
    as_of = (
        datetime.fromisoformat(args.as_of).astimezone(SHANGHAI)
        if args.as_of
        else datetime.now(SHANGHAI)
    )
    expected_15m, expected_60m = expected_completed_bars(as_of)
    today = as_of.date()
    history_start = today - timedelta(days=40)
    history_end = today
    daily_start = int((as_of - timedelta(days=160)).timestamp() * 1000)
    daily_end = int(as_of.timestamp() * 1000)
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    contract = RiskFeatureContract.load(PROJECT_ROOT / "config" / "risk_feature_contract.json")
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    adapter = LongbridgeMarketDataAdapter(provider)
    market_data = MarketDataService(
        registry,
        (
            adapter,
            HithinkMarketDataAdapter(HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env"))),
        ),
        cache,
    )
    service = RiskInputService(market_data, contract)
    store = RiskInputSnapshotStore(PROJECT_ROOT / "data" / "risk_inputs")
    report: dict[str, object] = {
        "generated_at": as_of.isoformat(),
        "timezone": "Asia/Shanghai",
        "history_window": {"start": history_start.isoformat(), "end": history_end.isoformat()},
        "new_indexes": {},
    }
    bundles = {}
    snapshot_paths = {}
    failures = 0
    current_bar_shortfalls: list[str] = []

    for instrument_id, expected_name, expected_symbol in NEW_INDEXES:
        print(expected_name.upper())
        item_report: dict[str, object] = {"provider_symbol": expected_symbol}
        report["new_indexes"][instrument_id] = item_report  # type: ignore[index]
        try:
            static_raw = provider.get_static_info([expected_symbol])
            static_item = static_raw["data"]["item"][0]
            static_entry = cache.save(
                instrument_id=instrument_id,
                provider="longbridge",
                provider_symbol=expected_symbol,
                data_type=DataType.STATIC_INFO,
                raw=static_raw,
            )
            mapping = registry.resolve(instrument_id, "longbridge")
            identity_ok = (
                static_item["symbol"] == expected_symbol
                and static_item["name_cn"] == expected_name
                and mapping.provider_symbol == expected_symbol
                and mapping.mapping_type is MappingType.EXACT
                and mapping.confidence is MappingConfidence.HIGH
                and mapping.status is MappingStatus.VERIFIED
            )
            if not identity_ok:
                raise ValueError("static identity or verified Registry mapping mismatch")
            item_report["mapping"] = {
                "status": "EXACT/HIGH/VERIFIED",
                "name_cn": static_item["name_cn"],
                "name_en": static_item["name_en"],
                "exchange": static_item["exchange"],
                "currency": static_item["currency"],
                "board": static_item["board"],
                "raw_path": static_entry.path,
            }
            print(
                "  MAPPING EXACT/HIGH/VERIFIED — "
                f"{expected_symbol}; {static_item['name_cn']}; board={static_item['board']}"
            )

            quote = market_data.get_quote(instrument_id, "longbridge")
            daily = market_data.get_daily(
                instrument_id,
                "longbridge",
                start=daily_start,
                end=daily_end,
            )
            item_report["quote"] = {
                "status": "PASS",
                "rows": len(quote.normalized),
                "raw_path": quote.metadata.raw_path,
            }
            item_report["daily"] = {
                "status": "PASS",
                "rows": len(daily.normalized),
                "raw_path": daily.metadata.raw_path,
            }
            print(f"  QUOTE PASS — rows={len(quote.normalized)}")
            print(f"  DAILY PASS — rows={len(daily.normalized)}")

            for period, expected_system_count in (("15m", 16), ("60m", 4)):
                raw = provider.get_history_candlesticks(
                    expected_symbol,
                    period=period,
                    start=history_start,
                    end=history_end,
                )
                data_type = DataType(period)
                entry = cache.save(
                    instrument_id=instrument_id,
                    provider="longbridge",
                    provider_symbol=expected_symbol,
                    data_type=data_type,
                    raw=raw,
                    request_start=int(datetime.combine(history_start, datetime.min.time(), tzinfo=SHANGHAI).timestamp() * 1000),
                    request_end=int(datetime.combine(history_end, datetime.max.time(), tzinfo=SHANGHAI).timestamp() * 1000),
                )
                trace = SourceTrace(
                    provider="longbridge",
                    provider_symbol=expected_symbol,
                    raw_path=entry.path,
                    fetched_at=entry.fetched_at,
                    source_timestamp=entry.source_timestamp,
                )
                records = normalize_longbridge_candlesticks(
                    raw,
                    instrument_id=instrument_id,
                    symbol=expected_symbol,
                    name=mapping.provider_name,
                    asset_type=registry.get_instrument(instrument_id).asset_type,
                    period=period,
                    source_trace=trace,
                )
                source_counts = Counter(source_day(int(record.timestamp / 1000)) for record in records if record.timestamp is not None)
                records_by_day = {
                    day: tuple(record for record in records if record.timestamp is not None and source_day(int(record.timestamp / 1000)) == day)
                    for day in source_counts
                }
                system = []
                invalid_days = []
                for day, day_records in sorted(records_by_day.items()):
                    try:
                        day_system = build_system_bars(day_records, period=period)
                        if len(day_system) != expected_system_count:
                            raise ValueError(
                                f"expected {expected_system_count} System Bars, got {len(day_system)}"
                            )
                        system.extend(day_system)
                    except Exception as exc:
                        invalid_days.append(
                            {"date": day, "class": type(exc).__name__, "message": str(exc)}
                        )
                complete_days = len(source_counts) - len(invalid_days)
                minimum_days_ok = complete_days >= 20
                complete_ok = minimum_days_ok and not invalid_days
                status = "PASS" if complete_ok else "REVIEW_REQUIRED" if minimum_days_ok else "FAIL"
                item_report[period] = {
                    "capability": "DIRECT",
                    "source_rows": len(records),
                    "trading_days": len(source_counts),
                    "complete_system_days": complete_days,
                    "system_bars": len(system),
                    "per_day": expected_system_count,
                    "status": status,
                    "invalid_days": invalid_days,
                    "raw_path": entry.path,
                }
                print(
                    f"  {period.upper()} DIRECT — source={len(records)}; "
                    f"complete_days={complete_days}; system/day={expected_system_count}; "
                    f"{status}"
                )
                if not minimum_days_ok:
                    raise ValueError(f"{period} did not produce >=20 complete System-Bar days")

            bundle = service.build_bundle(
                instrument_id,
                as_of=as_of,
                requested_provider="longbridge",
                fallback_providers=("hithink",),
            )
            snapshot_path = store.save_bundle(bundle)
            replay_ok = store.load(snapshot_path) == bundle.to_dict()
            fields_ok = feature_contract_ok(bundle)
            trace_ok = provenance_ok(bundle)
            ready = (
                bundle.preflight_status is not PreflightStatus.BLOCKED
                and len(bundle.risk_60m.system_bars) == expected_60m
                and len(bundle.support_15m.system_bars) == expected_15m
                and fields_ok
                and trace_ok
                and replay_ok
            )
            item_report["risk_input"] = {
                "status": "DEGRADED" if ready else "BLOCKED",
                "preflight": bundle.preflight_status.value,
                "system_15m": len(bundle.support_15m.system_bars),
                "system_60m": len(bundle.risk_60m.system_bars),
                "field_contract": "PASS" if fields_ok else "FAIL",
                "provenance": "PASS" if trace_ok else "FAIL",
                "snapshot_replay": "PASS" if replay_ok else "FAIL",
                "snapshot_path": snapshot_path,
            }
            print(
                f"  RISK INPUT {'DEGRADED' if ready else 'BLOCKED'} — "
                f"15m={len(bundle.support_15m.system_bars)}; "
                f"60m={len(bundle.risk_60m.system_bars)}; "
                f"preflight={bundle.preflight_status.value}"
            )
            if not ready:
                failures += 1
                if (
                    len(bundle.risk_60m.system_bars) < expected_60m
                    or len(bundle.support_15m.system_bars) < expected_15m
                ):
                    current_bar_shortfalls.append(instrument_id)
            bundles[instrument_id] = bundle
            snapshot_paths[instrument_id] = snapshot_path
        except Exception as exc:
            failures += 1
            item_report["failure"] = {"class": type(exc).__name__, "message": str(exc)}
            print(f"  FAIL — {type(exc).__name__}: {exc}")
        print()

    # Reuse the two TASK_003 index mappings through the same TASK_006 chain so
    # the final group snapshot contains eight current, append-only inputs.
    for instrument_id in ("index.csi500", "index.star50"):
        try:
            mapping = registry.resolve(instrument_id, "longbridge")
            # Keep the rolling historical Raw Cache contract symmetric across
            # all eight indexes.  build_bundle() only fetches the current-day
            # window, which cannot feed the frozen 80-period 15m replay by
            # itself when the replay advances to a new trading day.
            for period in ("15m", "60m"):
                raw = provider.get_history_candlesticks(
                    mapping.provider_symbol,
                    period=period,
                    start=history_start,
                    end=history_end,
                )
                cache.save(
                    instrument_id=instrument_id,
                    provider="longbridge",
                    provider_symbol=mapping.provider_symbol,
                    data_type=DataType(period),
                    raw=raw,
                    request_start=int(
                        datetime.combine(
                            history_start, datetime.min.time(), tzinfo=SHANGHAI
                        ).timestamp()
                        * 1000
                    ),
                    request_end=int(
                        datetime.combine(
                            history_end, datetime.max.time(), tzinfo=SHANGHAI
                        ).timestamp()
                        * 1000
                    ),
                )
            bundle = service.build_bundle(
                instrument_id,
                as_of=as_of,
                requested_provider="longbridge",
                fallback_providers=("hithink",),
            )
            snapshot_path = store.save_bundle(bundle)
            ready = (
                bundle.preflight_status is not PreflightStatus.BLOCKED
                and len(bundle.risk_60m.system_bars) == expected_60m
                and len(bundle.support_15m.system_bars) == expected_15m
                and provenance_ok(bundle)
            )
            if not ready:
                failures += 1
                if (
                    len(bundle.risk_60m.system_bars) < expected_60m
                    or len(bundle.support_15m.system_bars) < expected_15m
                ):
                    current_bar_shortfalls.append(instrument_id)
            bundles[instrument_id] = bundle
            snapshot_paths[instrument_id] = snapshot_path
        except Exception:
            failures += 1

    group = build_market_risk_group(
        as_of=as_of.isoformat(),
        registry=registry,
        bundles=bundles,
        snapshot_paths=snapshot_paths,
    )
    group_path = store.save_group(group)
    group_replay = store.load(group_path) == group.to_dict()
    coverage = market_coverage_status(group)
    report["market_bundle"] = {
        "coverage": coverage,
        "new_ready": sum(
            item in bundles
            and bundles[item].preflight_status is not PreflightStatus.BLOCKED
            for item, _, _ in NEW_INDEXES
        ),
        "total_ready": sum(item.status in {"READY", "DEGRADED"} for item in group.entries),
        "snapshot_path": group_path,
        "snapshot_replay": "PASS" if group_replay else "FAIL",
        "entries": [item.to_dict() for item in group.entries],
    }
    if coverage != "FULL_READY" or not group_replay:
        failures += 1
    report_path = PROJECT_ROOT / "data" / "reports" / "market_index_coverage_latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("FULL MARKET INDEX COVERAGE")
    print(
        f"{sum(item in bundles and bundles[item].preflight_status is not PreflightStatus.BLOCKED for item, _, _ in NEW_INDEXES)}/6 NEW"
    )
    print(f"{sum(item.status in {'READY', 'DEGRADED'} for item in group.entries)}/8 TOTAL")
    print(coverage)
    print(f"SNAPSHOT {'PASS' if group_replay else 'FAIL'} — {group_path}")
    print(f"REPORT {report_path}")
    print(f"EXPECTED COMPLETED BARS — 15m={expected_15m}; 60m={expected_60m}")
    if (
        current_bar_shortfalls
        and as_of.date() == datetime.now(SHANGHAI).date()
        and within_live_readiness_window(as_of)
    ):
        print(
            "TEMPORARY_PROVIDER_ERROR — completed live bars are not ready for "
            + ",".join(sorted(set(current_bar_shortfalls)))
        )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
