#!/usr/bin/env python3
"""TASK_004 real Longbridge minute convention and System Bar verification."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.errors import TrendMonitorError  # noqa: E402
from trend_monitor.normalization.longbridge import normalize_longbridge_candlesticks  # noqa: E402
from trend_monitor.providers.longbridge import LongbridgeProvider  # noqa: E402
from trend_monitor.registry import InstrumentRegistry  # noqa: E402
from trend_monitor.schemas import DataType, SourceTrace  # noqa: E402
from trend_monitor.transformation import build_system_bars  # noqa: E402
from trend_monitor.validation import (  # noqa: E402
    analyze_close_bar_structure,
    classify_source_bar,
    ohlc_anomalies,
    reconcile_system_bars,
    record_timestamp,
)
from trend_monitor.validation.minute_structure import EXPECTED_TIMES  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
INSTRUMENTS = (
    "stock.hengtong_optic",
    "stock.wus_printed_circuit",
    "index.csi500",
    "index.star50",
)
PERIODS = ("15m", "60m")
MINIMUM_SCAN_DAYS = 60
MINIMUM_CLOSING_DAYS = 20


def print_result(label: str, status: str, detail: str) -> None:
    print(f"{label}: {status} — {detail}")


def epoch_ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).timestamp() * 1000)


def normalize_window(
    *,
    raw: dict[str, object],
    cache: RawCache,
    registry: InstrumentRegistry,
    instrument_id: str,
    period: str,
    start: date,
    end: date,
):
    instrument = registry.get_instrument(instrument_id)
    mapping = registry.resolve(instrument_id, "longbridge")
    assert mapping.provider_symbol is not None
    entry = cache.save(
        instrument_id=instrument_id,
        provider="longbridge",
        provider_symbol=mapping.provider_symbol,
        data_type=DataType.DAILY if period == "1d" else DataType(period),
        raw=raw,
        request_start=epoch_ms(start),
        request_end=epoch_ms(end + timedelta(days=1)) - 1,
    )
    trace = SourceTrace(
        provider="longbridge",
        provider_symbol=mapping.provider_symbol,
        raw_path=entry.path,
        fetched_at=entry.fetched_at,
        source_timestamp=entry.source_timestamp,
    )
    return normalize_longbridge_candlesticks(
        raw,
        instrument_id=instrument_id,
        symbol=mapping.provider_symbol,
        name=mapping.provider_name or instrument.display_name,
        asset_type=instrument.asset_type,
        period=period,
        source_trace=trace,
    )


def complete_days(records, *, period: str) -> list[str]:
    grouped = defaultdict(list)
    for record in records:
        grouped[record_timestamp(record).date().isoformat()].append(record)
    result = []
    for day, bars in sorted(grouped.items()):
        times = tuple(record_timestamp(item).strftime("%H:%M") for item in sorted(bars, key=lambda x: x.timestamp or 0))
        if times == EXPECTED_TIMES[period]:
            result.append(day)
    return result


def select_days(records, days: set[str]):
    return [item for item in records if record_timestamp(item).date().isoformat() in days]


def anomaly_report(records) -> dict[str, object]:
    anomaly_types: Counter[str] = Counter()
    opening = 0
    non_opening = 0
    quirks = []
    for record in records:
        anomalies = ohlc_anomalies(record)
        if not anomalies:
            continue
        local = record_timestamp(record)
        for anomaly in anomalies:
            anomaly_types[anomaly.value] += 1
        if local.strftime("%H:%M") == "09:30":
            opening += 1
        else:
            non_opening += 1
        assessment = classify_source_bar(record)
        quirks.append(
            {
                "source_bar_id": assessment.source_bar_id,
                "date": local.date().isoformat(),
                "time": local.strftime("%H:%M"),
                "quality_status": assessment.quality_status.value,
                "anomaly_types": [item.value for item in assessment.anomaly_types],
                "open": record.open,
                "high": record.high,
                "low": record.low,
                "close": record.close,
                "raw_path": record.source_trace.raw_path if record.source_trace else None,
            }
        )
    opening_count = sum(record_timestamp(item).strftime("%H:%M") == "09:30" for item in records)
    return {
        "total_bars": len(records),
        "opening_0930_bars": opening_count,
        "strict_anomaly_bars": opening + non_opening,
        "opening_0930_anomaly_bars": opening,
        "non_opening_anomaly_bars": non_opening,
        "anomaly_types": dict(sorted(anomaly_types.items())),
        "anomalies": quirks,
    }


def main() -> int:
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    report: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": "Asia/Shanghai",
        "minimum_scan_days": MINIMUM_SCAN_DAYS,
        "minimum_closing_days": MINIMUM_CLOSING_DAYS,
        "instruments": {},
    }
    failures = 0

    sessions_raw = provider.get_trading_sessions()
    session_entry = cache.save(
        instrument_id="market.cn",
        provider="longbridge",
        provider_symbol="CN",
        data_type=DataType.TRADING_SESSION,
        raw=sessions_raw,
    )
    cn_session = next(
        item for item in sessions_raw["data"]["item"] if item["market"] == "CN"
    )
    report["official_trading_session"] = {
        "raw_path": session_entry.path,
        "response": cn_session,
    }
    print_result("TRADING SESSION", "PASS", str(cn_session["trade_sessions"]))

    end_day = datetime.now(SHANGHAI).date()
    start_day = end_day - timedelta(days=140)
    split_day = start_day + timedelta(days=70)
    windows = ((start_day, split_day - timedelta(days=1)), (split_day, end_day))
    daily_start = epoch_ms(start_day)
    daily_end = epoch_ms(end_day + timedelta(days=1)) - 1

    for instrument_id in INSTRUMENTS:
        instrument_report: dict[str, object] = {}
        report["instruments"][instrument_id] = instrument_report
        mapping = registry.resolve(instrument_id, "longbridge")
        assert mapping.provider_symbol is not None
        daily_raw = provider.get_daily(mapping.provider_symbol, start=daily_start, end=daily_end)
        daily_records = normalize_window(
            raw=daily_raw,
            cache=cache,
            registry=registry,
            instrument_id=instrument_id,
            period="1d",
            start=start_day,
            end=end_day,
        )

        for period in PERIODS:
            records = []
            for window_start, window_end in windows:
                raw = provider.get_history_candlesticks(
                    mapping.provider_symbol,
                    period=period,
                    start=window_start,
                    end=window_end,
                )
                records.extend(
                    normalize_window(
                        raw=raw,
                        cache=cache,
                        registry=registry,
                        instrument_id=instrument_id,
                        period=period,
                        start=window_start,
                        end=window_end,
                    )
                )
            records.sort(key=lambda item: item.timestamp or 0)
            dates = complete_days(records, period=period)
            selected_dates = dates[-MINIMUM_SCAN_DAYS:]
            period_report = anomaly_report(records)
            daily_by_day = {
                record_timestamp(item).date().isoformat(): item for item in daily_records
            }
            for anomaly in period_report["anomalies"]:
                daily_record = daily_by_day.get(anomaly["date"])
                if daily_record is None:
                    continue
                anomaly["daily_open"] = daily_record.open
                anomaly["daily_high"] = daily_record.high
                anomaly["daily_low"] = daily_record.low
                anomaly["source_open_matches_daily_open"] = (
                    anomaly["open"] == daily_record.open
                )
                anomaly["daily_range_includes_source_open"] = (
                    daily_record.low <= anomaly["open"] <= daily_record.high
                    if daily_record.low is not None and daily_record.high is not None
                    else False
                )
            period_report["complete_days"] = len(dates)
            period_report["selected_system_days"] = len(selected_dates)
            instrument_report[period] = period_report
            if len(selected_dates) < MINIMUM_SCAN_DAYS:
                failures += 1
                print_result(
                    f"{instrument_id} {period}",
                    "FAIL",
                    f"complete_days={len(dates)} < {MINIMUM_SCAN_DAYS}",
                )
                continue

            selected = select_days(records, set(selected_dates))
            selected_daily = select_days(daily_records, set(selected_dates))
            try:
                system = build_system_bars(selected, period=period)
                reconciliation = reconcile_system_bars(
                    system,
                    selected_daily,
                    period=period,
                )
                closing = analyze_close_bar_structure(
                    selected,
                    selected_daily,
                    period=period,
                    minimum_days=MINIMUM_CLOSING_DAYS,
                )
                period_report["closing_bucket"] = closing
                period_report["daily_reconciliation"] = reconciliation
                period_report["system_bar_count"] = len(system)
                period_report["system_bars_per_day"] = len(system) // len(selected_dates)
                period_report["system_bar_samples"] = [
                    system[0].to_dict(),
                    system[-1].to_dict(),
                ]
                source_status = (
                    "PASS"
                    if period_report["non_opening_anomaly_bars"] == 0
                    else "REVIEW"
                )
                system_status = (
                    "PASS"
                    if len(system) == len(selected_dates) * (16 if period == "15m" else 4)
                    else "FAIL"
                )
                if system_status == "FAIL":
                    failures += 1
                if reconciliation["status"] == "FAIL":
                    failures += 1
                print_result(
                    f"{instrument_id} {period} SOURCE",
                    source_status,
                    f"bars={len(records)}; complete_days={len(dates)}; "
                    f"09:30_quirks={period_report['opening_0930_anomaly_bars']}; "
                    f"non_09:30_anomalies={period_report['non_opening_anomaly_bars']}",
                )
                print_result(
                    f"{instrument_id} {period} SYSTEM",
                    system_status,
                    f"days={len(selected_dates)}; bars_per_day={period_report['system_bars_per_day']}; "
                    f"daily={reconciliation['status']}",
                )
            except TrendMonitorError as exc:
                failures += 1
                period_report["system_error"] = {
                    "category": exc.category.value,
                    "message": exc.message,
                }
                print_result(f"{instrument_id} {period} SYSTEM", "FAIL", str(exc))

    reports_dir = PROJECT_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "minute_convention_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    period_reports = [
        values[period]
        for values in report["instruments"].values()
        for period in PERIODS
        if period in values
    ]
    source_15 = all(
        report["instruments"][item]["15m"].get("non_opening_anomaly_bars") == 0
        for item in INSTRUMENTS
    )
    source_60 = all(
        report["instruments"][item]["60m"].get("non_opening_anomaly_bars") == 0
        for item in INSTRUMENTS
    )
    closing_pass = all(
        value.get("closing_bucket", {}).get("all_1500_closes_match_daily")
        and value.get("closing_bucket", {}).get("including_1500_always_closer_by_volume")
        and value.get("closing_bucket", {}).get("including_1500_always_closer_by_turnover")
        for value in period_reports
    )
    system_15 = all(
        report["instruments"][item]["15m"].get("system_bars_per_day") == 16
        for item in INSTRUMENTS
    )
    system_60 = all(
        report["instruments"][item]["60m"].get("system_bars_per_day") == 4
        for item in INSTRUMENTS
    )
    reconciliation_states = Counter(
        value.get("daily_reconciliation", {}).get("status", "FAIL")
        for value in period_reports
    )
    print()
    print_result("15m Source", "PASS" if source_15 else "REVIEW", "strict non-boundary anomaly check")
    print_result("60m Source", "PASS" if source_60 else "REVIEW", "strict non-boundary anomaly check")
    print_result(
        "09:30 Quirk",
        "PASS" if source_15 and source_60 else "REVIEW",
        "classified separately; Source OHLC unchanged",
    )
    print_result("Closing Bucket", "PASS" if closing_pass else "REVIEW", f"report={path}")
    print_result("System 15m", "PASS" if system_15 else "FAIL", "expected=16 per complete day")
    print_result("System 60m", "PASS" if system_60 else "FAIL", "expected=4 per complete day")
    daily_status = "FAIL" if reconciliation_states["FAIL"] else "REVIEW" if reconciliation_states["REVIEW"] else "PASS"
    print_result("Daily reconciliation", daily_status, str(dict(reconciliation_states)))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
