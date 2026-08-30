#!/usr/bin/env python3
"""TASK_003 real Longbridge capability and cross-provider verification."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.comparison import (  # noqa: E402
    ComparisonStatus,
    compare_daily_records,
    load_comparison_config,
)
from trend_monitor.errors import ErrorCategory, TrendMonitorError  # noqa: E402
from trend_monitor.normalization.longbridge import normalize_longbridge_candlesticks  # noqa: E402
from trend_monitor.providers.hithink import HithinkProvider  # noqa: E402
from trend_monitor.providers.hithink.adapter import HithinkMarketDataAdapter  # noqa: E402
from trend_monitor.providers.longbridge import (  # noqa: E402
    LongbridgeMarketDataAdapter,
    LongbridgeProvider,
)
from trend_monitor.registry import InstrumentRegistry, MappingType  # noqa: E402
from trend_monitor.schemas import SourceTrace  # noqa: E402
from trend_monitor.services import MarketDataService  # noqa: E402
from trend_monitor.utils.raw_samples import save_normalized, save_raw_response  # noqa: E402
from trend_monitor.validation import analyze_close_bar_structure  # noqa: E402


CROSS_INSTRUMENTS = (
    "stock.hengtong_optic",
    "stock.wus_printed_circuit",
    "index.csi500",
    "index.star50",
)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STOCK_INSTRUMENTS = ("stock.hengtong_optic", "stock.wus_printed_circuit")
INDEX_INSTRUMENTS = ("index.csi500", "index.star50")


def section(name: str, result: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(name)
    print(f"{result}{suffix}")
    print()


def capability_from_error(exc: TrendMonitorError) -> str:
    category = exc.category
    details = exc.details.get("failure_details")
    if category is ErrorCategory.DATA_INCOMPLETE and isinstance(details, tuple) and details:
        last = details[-1]
        if isinstance(last, dict) and isinstance(last.get("category"), str):
            try:
                category = ErrorCategory(last["category"])
            except ValueError:
                pass
    if category is ErrorCategory.PERMISSION_ERROR:
        return "PERMISSION_REQUIRED"
    if category is ErrorCategory.UNSUPPORTED:
        return "UNSUPPORTED"
    if category is ErrorCategory.EMPTY_DATA:
        return "EMPTY_DATA"
    if category is ErrorCategory.INVALID_DATA:
        return "INVALID_DATA"
    return "UNKNOWN"


def error_detail(exc: TrendMonitorError) -> str:
    failure_details = exc.details.get("failure_details")
    if failure_details:
        return f"{exc.category.value}; failures={failure_details}"
    return f"{exc.category.value}; {exc.message}"


def save_minute_samples(result, period: str, *, suffix: str = "") -> None:
    symbol = result.metadata.provider_symbol.replace(".", "_")
    save_raw_response(
        PROJECT_ROOT / "data" / "samples" / "longbridge" / f"{symbol}_{period}{suffix}_raw.json",
        result.raw,
    )
    save_normalized(
        PROJECT_ROOT / "data" / "samples" / "normalized" / f"longbridge_{symbol}_{period}{suffix}.json",
        [record.to_dict() for record in result.normalized],
    )


class ControlledHithinkFailure:
    """Dependency-injected failure; real Hithink configuration is untouched."""

    name = "hithink"

    def get_quote(self, provider_symbol, asset_type):
        raise TrendMonitorError(ErrorCategory.NETWORK_ERROR, "CONTROLLED_FAILURE")


def main() -> int:
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    longbridge_provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    section("LONG_BRIDGE SDK", "PASS", f"official longbridge=={version('longbridge')}")

    if not longbridge_provider.configured:
        try:
            longbridge_provider.get_quote("600487.SH")
        except TrendMonitorError as exc:
            detail = exc.message
        else:
            detail = "credential state check failed"
        section("LONG_BRIDGE CONNECTION", "BLOCKED", detail)
        section("STOCK QUOTE", "UNKNOWN", "no authenticated call")
        section("DAILY", "UNKNOWN", "no authenticated call")
        section("15M", "UNKNOWN", "BLOCKED_BY_LONGBRIDGE_CREDENTIALS")
        section("60M", "UNKNOWN", "BLOCKED_BY_LONGBRIDGE_CREDENTIALS")
        section("INDEX", "UNKNOWN", "credentials missing; mappings remain UNMAPPED")
        section("ETF", "UNKNOWN", "no authenticated call")
        section("CROSS PROVIDER", "BLOCKED", "four real Longbridge series unavailable")
        section("REAL FALLBACK", "BLOCKED", "second real Provider unavailable")
        return 2

    adapter = LongbridgeMarketDataAdapter(longbridge_provider)
    hithink = HithinkMarketDataAdapter(
        HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env"))
    )
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    service = MarketDataService(
        registry,
        [hithink, adapter],
        cache,
    )
    section("LONG_BRIDGE CONNECTION", "PASS", "QuoteContext authenticated")

    now = datetime.now(timezone.utc)
    start = int((now - timedelta(days=90)).timestamp() * 1000)
    end = int(now.timestamp() * 1000)
    failures = 0

    stock_results = []
    for instrument_id in STOCK_INSTRUMENTS:
        try:
            result = service.get_quote(instrument_id, "longbridge")
            stock_results.append(result)
            section(
                f"STOCK QUOTE {instrument_id}",
                "PASS",
                f"symbol={result.metadata.provider_symbol}; rows={len(result.normalized)}",
            )
        except TrendMonitorError as exc:
            failures += 1
            section(f"STOCK QUOTE {instrument_id}", "FAIL", error_detail(exc))

    for instrument_id in INDEX_INSTRUMENTS:
        try:
            result = service.get_quote(instrument_id, "longbridge")
            section(
                f"INDEX QUOTE {instrument_id}",
                "PASS",
                f"symbol={result.metadata.provider_symbol}; rows={len(result.normalized)}",
            )
        except TrendMonitorError as exc:
            failures += 1
            section(f"INDEX QUOTE {instrument_id}", "FAIL", error_detail(exc))

    daily_results: dict[str, object] = {}
    for instrument_id in CROSS_INSTRUMENTS:
        mapping = registry.resolve(instrument_id, "longbridge")
        if mapping.mapping_type is MappingType.UNMAPPED:
            failures += 1
            section(f"DAILY {instrument_id}", "UNKNOWN", "Longbridge mapping is UNMAPPED")
            continue
        try:
            result = service.get_daily(
                instrument_id,
                "longbridge",
                start=start,
                end=end,
            )
            daily_results[instrument_id] = result
            section(
                f"DAILY {instrument_id}",
                "PASS",
                f"rows={len(result.normalized)}; adjustment=none",
            )
        except TrendMonitorError as exc:
            failures += 1
            section(f"DAILY {instrument_id}", "FAIL", error_detail(exc))

    minute_status: dict[str, list[str]] = {"15m": [], "60m": []}
    minute_results: dict[tuple[str, str], object] = {}
    for period in ("15m", "60m"):
        for instrument_id in CROSS_INSTRUMENTS:
            try:
                result = service.get_bars(
                    instrument_id,
                    "longbridge",
                    period=period,
                    count=120,
                )
                minute_results[(instrument_id, period)] = result
                save_minute_samples(result, period)
                minute_status[period].append("DIRECT")
                local_times = [
                    datetime.fromtimestamp(item.timestamp / 1000, tz=timezone.utc)
                    .astimezone(SHANGHAI_TZ)
                    .strftime("%Y-%m-%d %H:%M:%S%z")
                    for item in result.normalized[-4:]
                    if item.timestamp is not None
                ]
                section(
                    f"{period.upper()} {instrument_id}",
                    "DIRECT",
                    f"rows={len(result.normalized)}; last_times={','.join(local_times)}",
                )
            except TrendMonitorError as exc:
                failures += 1
                status = capability_from_error(exc)
                minute_status[period].append(status)
                section(f"{period.upper()} {instrument_id}", status, error_detail(exc))

                details = exc.details.get("failure_details")
                last = details[-1] if isinstance(details, tuple) and details else None
                if (
                    isinstance(last, dict)
                    and last.get("stage") == "normalization_or_validation"
                    and isinstance(last.get("raw_path"), str)
                ):
                    raw = cache.load(last["raw_path"])
                    mapping = registry.resolve(instrument_id, "longbridge")
                    instrument = registry.get_instrument(instrument_id)
                    trace = SourceTrace(
                        provider="longbridge",
                        provider_symbol=str(last["provider_symbol"]),
                        raw_path=str(last["raw_path"]),
                        fetched_at=str(last["fetched_at"]),
                        source_timestamp=(
                            int(last["source_timestamp"])
                            if last.get("source_timestamp") is not None
                            else None
                        ),
                    )
                    normalized = normalize_longbridge_candlesticks(
                        raw,
                        instrument_id=instrument_id,
                        symbol=str(mapping.provider_symbol),
                        name=mapping.provider_name or instrument.display_name,
                        asset_type=instrument.asset_type,
                        period=period,
                        source_trace=trace,
                    )
                    symbol = str(mapping.provider_symbol).replace(".", "_")
                    save_raw_response(
                        PROJECT_ROOT / "data" / "samples" / "longbridge"
                        / f"{symbol}_{period}_invalid_raw.json",
                        raw,
                    )
                    save_normalized(
                        PROJECT_ROOT / "data" / "samples" / "normalized"
                        / f"longbridge_{symbol}_{period}_invalid.json",
                        [record.to_dict() for record in normalized],
                    )

    for period in ("15m", "60m"):
        statuses = minute_status[period]
        if statuses and all(status in {"DIRECT", "INVALID_DATA"} for status in statuses):
            summary = "DIRECT"
        else:
            summary = statuses[0] if statuses and len(set(statuses)) == 1 else "UNKNOWN"
        section(
            period.upper(),
            summary,
            f"raw capability; validated stock/index probes={statuses}",
        )

    structure_payload: list[dict[str, object]] = []
    for instrument_id in STOCK_INSTRUMENTS:
        daily = daily_results.get(instrument_id)
        if daily is None:
            continue
        for period in ("15m", "60m"):
            minute = minute_results.get((instrument_id, period))
            if minute is None:
                continue
            report = analyze_close_bar_structure(
                list(minute.normalized),
                list(daily.normalized),
                period=period,
                minimum_days=5,
            )
            report["instrument_id"] = instrument_id
            structure_payload.append(report)
    if len(structure_payload) == 4:
        report_dir = PROJECT_ROOT / "data" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        structure_path = report_dir / "longbridge_minute_structure.json"
        structure_path.write_text(
            json.dumps(structure_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        section(
            "MINUTE STRUCTURE",
            "PASS",
            f"2 stocks x 2 periods x 5 days; report={structure_path}",
        )
    else:
        failures += 1
        section("MINUTE STRUCTURE", "FAIL", f"complete reports={len(structure_payload)}/4")

    try:
        etf_quote = service.get_quote("etf.csi300.example", "longbridge")
        section("ETF", "PASS", f"symbol={etf_quote.metadata.provider_symbol}")
    except TrendMonitorError as exc:
        failures += 1
        section("ETF", "FAIL", error_detail(exc))

    comparison_config = load_comparison_config(PROJECT_ROOT / "config" / "comparison.json")
    comparison_payload: list[dict[str, object]] = []
    for instrument_id in CROSS_INSTRUMENTS:
        longbridge_daily = daily_results.get(instrument_id)
        if longbridge_daily is None:
            continue
        try:
            hithink_daily = service.get_daily(
                instrument_id,
                "hithink",
                start=start,
                end=end,
            )
            report = compare_daily_records(
                list(hithink_daily.normalized),
                list(longbridge_daily.normalized),
                instrument_id=instrument_id,
                left_provider="hithink",
                right_provider="longbridge",
                left_adjustment="none",
                right_adjustment="none",
                config=comparison_config,
                left_volume_unit=None,
                right_volume_unit=None,
            )
            comparison_payload.append(report.to_dict())
            section(
                f"CROSS PROVIDER {instrument_id}",
                report.status.value,
                f"common_days={report.common_days}; volume={report.volume_comparison.value}",
            )
        except TrendMonitorError as exc:
            failures += 1
            section(f"CROSS PROVIDER {instrument_id}", "FAIL", error_detail(exc))

    if comparison_payload:
        report_dir = PROJECT_ROOT / "data" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "cross_provider_latest.json"
        report_path.write_text(
            json.dumps(comparison_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        overall = (
            "FAIL"
            if any(item["status"] == ComparisonStatus.PRICE_CONFLICT.value for item in comparison_payload)
            else "REVIEW_REQUIRED"
            if any(item["status"] == ComparisonStatus.REVIEW_REQUIRED.value for item in comparison_payload)
            else "PASS"
        )
        section("CROSS PROVIDER", overall, f"report={report_path}")
    else:
        failures += 1
        section("CROSS PROVIDER", "FAIL", "no complete dual-source series")

    try:
        fallback_service = MarketDataService(
            registry,
            [ControlledHithinkFailure(), adapter],
            RawCache(PROJECT_ROOT / "data" / "raw"),
        )
        result = fallback_service.get_quote(
            "stock.hengtong_optic",
            "hithink",
            fallback_providers=["longbridge"],
        )
        valid = (
            result.metadata.fallback_used
            and result.metadata.actual_provider == "longbridge"
            and result.metadata.fallback_reason == "hithink:NETWORK_ERROR"
            and "/longbridge/" in result.metadata.raw_path
        )
        if not valid:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "fallback metadata mismatch")
        section(
            "REAL FALLBACK",
            "PASS",
            "requested=hithink; actual=longbridge; reason=NETWORK_ERROR",
        )
    except TrendMonitorError as exc:
        failures += 1
        section("REAL FALLBACK", "FAIL", error_detail(exc))

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
