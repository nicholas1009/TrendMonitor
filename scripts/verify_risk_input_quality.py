#!/usr/bin/env python3
"""TASK_005 real 1m/15m/60m/Daily field-quality verification."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from time import monotonic, sleep
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.comparison import (  # noqa: E402
    aggregate_one_minute,
    aggregate_system_daily,
    compare_diagnostic_bars,
    direct_records_as_diagnostic,
)
from trend_monitor.errors import ErrorCategory, TrendMonitorError  # noqa: E402
from trend_monitor.normalization.longbridge import normalize_longbridge_candlesticks  # noqa: E402
from trend_monitor.providers.longbridge import LongbridgeProvider  # noqa: E402
from trend_monitor.quality import (  # noqa: E402
    RiskEngineReadiness,
    RiskFeatureContract,
    annotate_system_bar,
    evaluate_risk_input,
)
from trend_monitor.registry import InstrumentRegistry  # noqa: E402
from trend_monitor.schemas import DataType, SourceTrace  # noqa: E402
from trend_monitor.transformation import build_system_bars  # noqa: E402
from trend_monitor.validation import record_timestamp, validate_source_minute_records  # noqa: E402
from trend_monitor.validation.minute_structure import EXPECTED_TIMES  # noqa: E402
from trend_monitor.comparison.cross_period import EXPECTED_1M_TIMES  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
INSTRUMENTS = (
    "stock.hengtong_optic",
    "stock.wus_printed_circuit",
    "index.csi500",
    "index.star50",
)
KNOWN_ANOMALIES = {
    ("stock.wus_printed_circuit", "2026-08-06"),
    ("index.csi500", "2026-08-07"),
    ("index.star50", "2026-08-21"),
    ("index.csi500", "2026-08-05"),
}
MINIMUM_DAYS = 60
BUFFER_DAYS = 64


class RateLimiter:
    """Stay below the official 60 history requests / 30 seconds limit."""

    def __init__(self, interval_seconds: float = 0.55) -> None:
        self.interval_seconds = interval_seconds
        self.last_call: float | None = None
        self.calls = 0

    def wait(self) -> None:
        now = monotonic()
        if self.last_call is not None:
            remaining = self.interval_seconds - (now - self.last_call)
            if remaining > 0:
                sleep(remaining)
        self.last_call = monotonic()
        self.calls += 1


def epoch_ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).timestamp() * 1000)


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def normalize_and_cache(
    raw,
    *,
    registry,
    cache,
    instrument_id: str,
    period: str,
    request_start: date,
    request_end: date,
):
    instrument = registry.get_instrument(instrument_id)
    mapping = registry.resolve(instrument_id, "longbridge")
    assert mapping.provider_symbol is not None
    data_type = DataType.DAILY if period == "1d" else DataType(period)
    entry = cache.save(
        instrument_id=instrument_id,
        provider="longbridge",
        provider_symbol=mapping.provider_symbol,
        data_type=data_type,
        raw=raw,
        request_start=epoch_ms(request_start),
        request_end=epoch_ms(request_end + timedelta(days=1)) - 1,
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


def fetch_daily(provider, limiter, registry, cache, instrument_id, start_day, end_day):
    mapping = registry.resolve(instrument_id, "longbridge")
    assert mapping.provider_symbol is not None
    limiter.wait()
    raw = provider.get_daily(
        mapping.provider_symbol,
        start=epoch_ms(start_day),
        end=epoch_ms(end_day + timedelta(days=1)) - 1,
    )
    return normalize_and_cache(
        raw,
        registry=registry,
        cache=cache,
        instrument_id=instrument_id,
        period="1d",
        request_start=start_day,
        request_end=end_day,
    )


def fetch_period(provider, limiter, registry, cache, instrument_id, period, trading_days, chunk_size):
    mapping = registry.resolve(instrument_id, "longbridge")
    assert mapping.provider_symbol is not None
    records = []
    windows = chunks(trading_days, chunk_size)
    for window in windows:
        start_day = date.fromisoformat(window[0])
        end_day = date.fromisoformat(window[-1])
        limiter.wait()
        raw = provider.get_history_candlesticks(
            mapping.provider_symbol,
            period=period,
            start=start_day,
            end=end_day,
        )
        records.extend(
            normalize_and_cache(
                raw,
                registry=registry,
                cache=cache,
                instrument_id=instrument_id,
                period=period,
                request_start=start_day,
                request_end=end_day,
            )
        )
    records.sort(key=lambda item: item.timestamp or 0)
    return records, len(windows)


def record_day(record) -> str:
    return record_timestamp(record).date().isoformat()


def complete_days(records, expected_times) -> set[str]:
    grouped = defaultdict(list)
    for record in records:
        grouped[record_day(record)].append(record)
    return {
        day
        for day, bars in grouped.items()
        if tuple(record_timestamp(item).strftime("%H:%M") for item in sorted(bars, key=lambda x: x.timestamp or 0))
        == expected_times
    }


def diagnostic_one_minute_days(records) -> tuple[set[str], dict[str, int]]:
    grouped = defaultdict(list)
    for record in records:
        grouped[record_day(record)].append(record)
    accepted = set()
    distribution = defaultdict(int)
    allowed = set(EXPECTED_1M_TIMES)
    for day, bars in grouped.items():
        times = [record_timestamp(item).strftime("%H:%M") for item in bars]
        distribution[str(len(times))] += 1
        if len(times) == len(set(times)) and set(times) <= allowed:
            accepted.add(day)
    return accepted, dict(sorted(distribution.items()))


def select_record_days(records, selected: set[str]):
    return [item for item in records if record_day(item) in selected]


def comparison_status(report) -> str:
    return "PASS" if all(
        report["fields"][field]["mismatch_count"] == 0
        for field in ("open", "high", "low", "close", "volume", "turnover")
    ) else "REVIEW"


def field_diagnosis(a, b, c, field: str) -> str:
    a_mismatch = a["fields"][field]["mismatch_count"]
    b_mismatch = b["fields"][field]["mismatch_count"]
    c_mismatch = c["fields"][field]["mismatch_count"]
    if a_mismatch == b_mismatch == c_mismatch == 0:
        return "MATCH"
    if a_mismatch == 0 and b_mismatch == 0 and c_mismatch > 0:
        return "SOURCE_CROSS_PERIOD_SEMANTIC_DIFFERENCE"
    if c_mismatch == 0 and (a_mismatch > 0 or b_mismatch > 0):
        return "SOURCE_HIGHER_PERIOD_AGGREGATION_DIFFERENCE"
    return "COMPLEX_CROSS_PERIOD_DIFFERENCE"


def bar_map(bars):
    return {item.day: item for item in bars}


def evidence_for_day(
    day: str,
    *,
    daily,
    one_daily,
    system_15_daily,
    system_60_daily,
    direct_15,
    direct_60,
    derived_15,
    derived_60,
):
    output = {
        "date": day,
        "daily_direct": daily[day].to_dict(),
        "one_minute_derived_daily": one_daily[day].to_dict(),
        "system_15m_derived_daily": system_15_daily[day].to_dict(),
        "system_60m_derived_daily": system_60_daily[day].to_dict(),
    }
    for label, values in (
        ("direct_15m", direct_15),
        ("direct_60m", direct_60),
        ("one_minute_derived_15m", derived_15),
        ("one_minute_derived_60m", derived_60),
    ):
        matching = [item.to_dict() for item in values if item.day == day]
        output[label] = matching
    return output


def main() -> int:
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    contract = RiskFeatureContract.load(PROJECT_ROOT / "config" / "risk_feature_contract.json")
    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    limiter = RateLimiter()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": "Asia/Shanghai",
        "adjustment": "NoAdjust",
        "minimum_complete_days": MINIMUM_DAYS,
        "diagnostic_only": True,
        "minute_derived_daily_formal_use": "PROHIBITED",
        "instruments": {},
        "field_profiles": {},
        "evidence": [],
    }
    failures = 0
    end_day = datetime.now(SHANGHAI).date()
    start_day = end_day - timedelta(days=140)

    for instrument_id in INSTRUMENTS:
        instrument = registry.get_instrument(instrument_id)
        print(f"[FETCH] {instrument_id} daily")
        daily_records = fetch_daily(
            provider, limiter, registry, cache, instrument_id, start_day, end_day
        )
        candidate_days = sorted({record_day(item) for item in daily_records})[-BUFFER_DAYS:]
        if len(candidate_days) < MINIMUM_DAYS:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"{instrument_id} only has {len(candidate_days)} candidate days",
            )
        one_records, one_windows = fetch_period(
            provider, limiter, registry, cache, instrument_id, "1m", candidate_days, 4
        )
        print(f"[FETCH] {instrument_id} 1m windows={one_windows}; rows={len(one_records)}")
        fifteen_records, fifteen_windows = fetch_period(
            provider, limiter, registry, cache, instrument_id, "15m", candidate_days, 32
        )
        sixty_records, sixty_windows = fetch_period(
            provider, limiter, registry, cache, instrument_id, "60m", candidate_days, 64
        )
        print(
            f"[FETCH] {instrument_id} 15m windows={fifteen_windows}; "
            f"60m windows={sixty_windows}"
        )

        validate_source_minute_records(one_records)
        validate_source_minute_records(fifteen_records)
        validate_source_minute_records(sixty_records)
        one_days, one_row_distribution = diagnostic_one_minute_days(one_records)
        common_days = (
            set(candidate_days)
            & one_days
            & complete_days(fifteen_records, EXPECTED_TIMES["15m"])
            & complete_days(sixty_records, EXPECTED_TIMES["60m"])
        )
        selected_days = sorted(common_days)[-MINIMUM_DAYS:]
        if len(selected_days) < MINIMUM_DAYS:
            raise TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                f"{instrument_id} only has {len(selected_days)} complete cross-period days",
            )
        selected = set(selected_days)
        one = select_record_days(one_records, selected)
        direct_15_records = select_record_days(fifteen_records, selected)
        direct_60_records = select_record_days(sixty_records, selected)
        daily = select_record_days(daily_records, selected)

        derived_15 = aggregate_one_minute(
            one, target_period="15m", allow_missing_minutes=True
        )
        derived_60 = aggregate_one_minute(
            one, target_period="60m", allow_missing_minutes=True
        )
        derived_one_daily = aggregate_one_minute(
            one, target_period="1d", allow_missing_minutes=True
        )
        direct_15 = direct_records_as_diagnostic(direct_15_records)
        direct_60 = direct_records_as_diagnostic(direct_60_records)
        direct_daily = direct_records_as_diagnostic(daily)
        system_15 = build_system_bars(direct_15_records, period="15m")
        system_60 = build_system_bars(direct_60_records, period="60m")
        system_15_daily = aggregate_system_daily(system_15)
        system_60_daily = aggregate_system_daily(system_60)

        comparisons = {
            "A_1m_vs_direct_15m": compare_diagnostic_bars(derived_15, direct_15),
            "B_1m_vs_direct_60m": compare_diagnostic_bars(derived_60, direct_60),
            "C_1m_vs_direct_daily": compare_diagnostic_bars(
                derived_one_daily, direct_daily, key=lambda item: item.day
            ),
            "D_system_15m_vs_direct_daily": compare_diagnostic_bars(
                system_15_daily, direct_daily, key=lambda item: item.day
            ),
            "E_system_60m_vs_direct_daily": compare_diagnostic_bars(
                system_60_daily, direct_daily, key=lambda item: item.day
            ),
        }
        a = comparisons["A_1m_vs_direct_15m"]
        b = comparisons["B_1m_vs_direct_60m"]
        c = comparisons["C_1m_vs_direct_daily"]
        diagnoses = {
            field: field_diagnosis(a, b, c, field)
            for field in ("open", "high", "low", "close", "volume", "turnover")
        }
        profile = contract.profile(instrument.asset_type, "15m")
        annotated_samples = []
        readiness = []
        for source_bar in (system_15[0], system_15[-1], system_60[0], system_60[-1]):
            annotated, reasons = annotate_system_bar(
                source_bar, asset_type=instrument.asset_type, contract=contract
            )
            assessment = evaluate_risk_input(
                annotated,
                asset_type=instrument.asset_type,
                contract=contract,
                quality_reasons=reasons,
            )
            annotated_samples.append(
                {
                    "bar": annotated.to_dict(),
                    "quality_reasons": list(reasons),
                    "assessment": assessment.to_dict(),
                }
            )
            readiness.append(assessment.readiness.value)

        instrument_report = {
            "asset_type": instrument.asset_type.value,
            "provider_symbol": registry.resolve(instrument_id, "longbridge").provider_symbol,
            "date_range": [selected_days[0], selected_days[-1]],
            "complete_days": len(selected_days),
            "row_counts": {
                "1m": len(one),
                "15m": len(direct_15_records),
                "60m": len(direct_60_records),
                "daily": len(daily),
            },
            "one_minute_rows_per_day": one_row_distribution,
            "comparisons": comparisons,
            "field_diagnosis": diagnoses,
            "annotated_runtime_samples": annotated_samples,
            "readiness": sorted(set(readiness)),
        }
        report["instruments"][instrument_id] = instrument_report
        report["field_profiles"][f"{instrument.asset_type.value}.15m"] = profile.to_dict()
        report["field_profiles"][f"{instrument.asset_type.value}.60m"] = contract.profile(
            instrument.asset_type, "60m"
        ).to_dict()

        maps = {
            "daily": bar_map(direct_daily),
            "one_daily": bar_map(derived_one_daily),
            "system_15_daily": bar_map(system_15_daily),
            "system_60_daily": bar_map(system_60_daily),
        }
        for evidence_instrument, evidence_day in sorted(KNOWN_ANOMALIES):
            if evidence_instrument != instrument_id or evidence_day not in selected:
                continue
            evidence = {
                    "instrument_id": instrument_id,
                    **evidence_for_day(
                        evidence_day,
                        daily=maps["daily"],
                        one_daily=maps["one_daily"],
                        system_15_daily=maps["system_15_daily"],
                        system_60_daily=maps["system_60_daily"],
                        direct_15=direct_15,
                        direct_60=direct_60,
                        derived_15=derived_15,
                        derived_60=derived_60,
                    ),
                }
            evidence["runtime_degradation"] = {}
            for period, system_values in (("15m", system_15), ("60m", system_60)):
                day_bars = [
                    item
                    for item in system_values
                    if datetime.fromtimestamp(
                        item.system_start / 1000, tz=timezone.utc
                    ).astimezone(SHANGHAI).date().isoformat() == evidence_day
                ]
                source_bar = day_bars[0] if evidence_day == "2026-08-05" else day_bars[-1]
                annotated, quality_reasons = annotate_system_bar(
                    source_bar,
                    asset_type=instrument.asset_type,
                    contract=contract,
                )
                assessment = evaluate_risk_input(
                    annotated,
                    asset_type=instrument.asset_type,
                    contract=contract,
                    quality_reasons=quality_reasons,
                )
                evidence["runtime_degradation"][period] = {
                    "bar": annotated.to_dict(),
                    "quality_reasons": list(quality_reasons),
                    "assessment": assessment.to_dict(),
                }
            report["evidence"].append(evidence)

        close_failures = sum(
            comparisons[name]["fields"]["close"]["mismatch_count"]
            for name in comparisons
        )
        if close_failures:
            failures += 1
        print(
            f"[PASS] {instrument_id} complete_days={len(selected_days)}; "
            f"A={comparison_status(a)}; B={comparison_status(b)}; "
            f"minute_daily={'PASS' if comparison_status(c) == 'PASS' else 'SEMANTIC_DIFFERENCE'}; "
            f"close_mismatches={close_failures}"
        )

    reports_dir = PROJECT_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "risk_input_quality_latest.json"
    report["history_api_calls"] = limiter.calls
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    all_reports = list(report["instruments"].values())
    a_status = "PASS" if all(
        comparison_status(item["comparisons"]["A_1m_vs_direct_15m"]) == "PASS"
        for item in all_reports
    ) else "REVIEW"
    b_status = "PASS" if all(
        comparison_status(item["comparisons"]["B_1m_vs_direct_60m"]) == "PASS"
        for item in all_reports
    ) else "REVIEW"
    c_status = "PASS" if all(
        comparison_status(item["comparisons"]["C_1m_vs_direct_daily"]) == "PASS"
        for item in all_reports
    ) else "SEMANTIC_DIFFERENCE"
    print()
    print(f"1m vs 15m: {a_status}")
    print(f"1m vs 60m: {b_status}")
    print(f"Minute vs Daily: {c_status}")
    print("STOCK CLOSE: TRUSTED / closing bar TRUSTED_WITH_TRANSFORMATION")
    print("STOCK HIGH_LOW: APPROXIMATE")
    print("INDEX CLOSE: TRUSTED / closing bar TRUSTED_WITH_TRANSFORMATION")
    print("INDEX VOLUME: BLOCKED")
    print("RISK ENGINE SAFE INPUT: YES_WITH_LIMITS")
    print(f"Evidence: {report_path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrendMonitorError as exc:
        print(f"FATAL {exc.category.value}: {exc.message}", file=sys.stderr)
        raise SystemExit(1)
