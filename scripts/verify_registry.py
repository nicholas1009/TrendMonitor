#!/usr/bin/env python3
"""TASK_002 registry, raw cache, lineage, fallback, and real Hithink checks."""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.cache import RawCache  # noqa: E402
from trend_monitor.errors import ErrorCategory, TrendMonitorError  # noqa: E402
from trend_monitor.providers.hithink import HithinkProvider, HithinkProviderError  # noqa: E402
from trend_monitor.providers.hithink.adapter import HithinkMarketDataAdapter  # noqa: E402
from trend_monitor.registry import InstrumentRegistry, MappingType  # noqa: E402
from trend_monitor.services import MarketDataService  # noqa: E402


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(slots=True)
class Check:
    outcome: Outcome
    name: str
    detail: str = ""


def record(checks: list[Check], outcome: Outcome, name: str, detail: str = "") -> None:
    checks.append(Check(outcome, name, detail))
    suffix = f" — {detail}" if detail else ""
    print(f"[{outcome.value}] {name}{suffix}")


def run(checks: list[Check], name: str, operation: Callable[[], str | None]) -> None:
    try:
        detail = operation() or ""
        record(checks, Outcome.PASS, name, detail)
    except (HithinkProviderError, TrendMonitorError) as exc:
        record(checks, Outcome.FAIL, name, exc.category.value)
    except Exception as exc:  # Verification must report unexpected failures, never ignore them.
        record(checks, Outcome.FAIL, name, f"unexpected:{type(exc).__name__}")


def main() -> int:
    checks: list[Check] = []
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    cache = RawCache(PROJECT_ROOT / "data" / "raw")
    hithink = HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env"))
    service = MarketDataService(
        registry,
        [HithinkMarketDataAdapter(hithink)],
        cache,
    )

    def registry_count() -> str:
        if len(registry.instruments) != 16:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "unexpected instrument count")
        return f"count={len(registry.instruments)}"

    run(
        checks,
        "registry loads all formal instruments",
        registry_count,
    )

    def all_hithink_mappings() -> str:
        mappings = [
            registry.resolve(instrument.instrument_id, "hithink")
            for instrument in registry.instruments
        ]
        if any(mapping.mapping_type is MappingType.UNMAPPED for mapping in mappings):
            raise TrendMonitorError(ErrorCategory.UNMAPPED, "formal Hithink mapping is missing")
        return f"resolved={len(mappings)}"

    run(checks, "all formal Hithink mappings resolve", all_hithink_mappings)

    def exact_mapping(instrument_id: str, symbol: str) -> str:
        mapping = registry.resolve(instrument_id, "hithink")
        if mapping.provider_symbol != symbol or mapping.mapping_type is not MappingType.EXACT:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"unexpected mapping for {instrument_id}",
            )
        return f"{instrument_id} -> {symbol} [EXACT]"

    run(checks, "resolve 600487", lambda: exact_mapping("stock.hengtong_optic", "600487.SH"))
    run(checks, "resolve 002463", lambda: exact_mapping("stock.wus_printed_circuit", "002463.SZ"))
    run(checks, "resolve csi500", lambda: exact_mapping("index.csi500", "000905.SH"))
    run(checks, "resolve star50", lambda: exact_mapping("index.star50", "000688.SH"))
    run(
        checks,
        "resolve communication equipment",
        lambda: exact_mapping("sector.communication_equipment", "881129.TI"),
    )
    run(
        checks,
        "resolve printed circuit board",
        lambda: exact_mapping("sector.printed_circuit_board", "884092.TI"),
    )

    def coal_check() -> str:
        mapping = registry.resolve("sector.coal", "hithink")
        if mapping.mapping_type is not MappingType.CANDIDATE_PROXY:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                "coal must remain CANDIDATE_PROXY",
            )
        return f"{mapping.provider_symbol} [CANDIDATE_PROXY/{mapping.confidence.value}]"

    run(checks, "coal mapping is not exact", coal_check)

    def unmapped_check() -> str:
        mapping = registry.resolve("sector.bank", "longbridge")
        if mapping.mapping_type is not MappingType.UNMAPPED or mapping.provider_symbol is not None:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "resolver guessed a symbol")
        return "sector.bank/longbridge -> UNMAPPED"

    run(checks, "missing mapping stays unmapped", unmapped_check)

    def visible_failure_check() -> str:
        try:
            service.get_quote("sector.bank", "longbridge")
        except TrendMonitorError as exc:
            if exc.category is not ErrorCategory.DATA_INCOMPLETE:
                raise
            if exc.details.get("failures") != ("longbridge:UNMAPPED",):
                raise TrendMonitorError(ErrorCategory.INVALID_DATA, "fallback cause was hidden")
            return "DATA_INCOMPLETE retains longbridge:UNMAPPED"
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "unmapped request unexpectedly succeeded")

    run(checks, "provider failure is visible", visible_failure_check)

    if not hithink.configured:
        record(checks, Outcome.FAIL, "Hithink authentication", "BLOCKED_BY_API_KEY")
        print("\nPASS: " + str(sum(item.outcome is Outcome.PASS for item in checks)))
        print("FAIL: " + str(sum(item.outcome is Outcome.FAIL for item in checks)))
        return 2

    now = datetime.now(timezone.utc)
    start = int((now - timedelta(days=45)).timestamp() * 1000)
    end = int(now.timestamp() * 1000)
    results = []

    def quote(instrument_id: str) -> str:
        result = service.get_quote(instrument_id, "hithink")
        results.append(result)
        return f"symbol={result.metadata.provider_symbol}; records={len(result.normalized)}"

    def daily(instrument_id: str) -> str:
        result = service.get_daily(
            instrument_id,
            "hithink",
            start=start,
            end=end,
        )
        results.append(result)
        return f"symbol={result.metadata.provider_symbol}; records={len(result.normalized)}"

    run(checks, "600487 internal -> Hithink -> quote", lambda: quote("stock.hengtong_optic"))
    run(checks, "002463 internal -> Hithink -> quote", lambda: quote("stock.wus_printed_circuit"))
    run(checks, "csi500 internal -> Hithink -> quote", lambda: quote("index.csi500"))
    run(checks, "csi500 internal -> Hithink -> daily", lambda: daily("index.csi500"))
    run(checks, "star50 internal -> Hithink -> quote", lambda: quote("index.star50"))
    run(checks, "star50 internal -> Hithink -> daily", lambda: daily("index.star50"))
    run(
        checks,
        "communication equipment registry -> Hithink -> quote",
        lambda: quote("sector.communication_equipment"),
    )

    def real_fallback() -> str:
        result = service.get_quote(
            "index.csi500",
            "eastmoney",
            fallback_providers=["hithink"],
        )
        results.append(result)
        if not result.metadata.fallback_used:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "fallback metadata is false")
        if result.metadata.fallback_reason != "eastmoney:UNMAPPED":
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "fallback reason is missing")
        return "requested=eastmoney; actual=hithink; reason=UNMAPPED"

    run(checks, "explicit fallback metadata", real_fallback)

    def trace_check() -> str:
        if not results:
            raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "no successful real result")
        for result in results:
            if not result.normalized:
                raise TrendMonitorError(ErrorCategory.EMPTY_DATA, "normalized result is empty")
            trace = result.normalized[0].source_trace
            if trace is None or trace.raw_path != result.metadata.raw_path:
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "source trace is missing")
            if cache.load(trace.raw_path) != result.raw:
                raise TrendMonitorError(ErrorCategory.CACHE_INVALID, "cached raw differs")
        return f"raw/cache/source trace verified for {len(results)} results"

    run(checks, "raw cache readback and source trace", trace_check)

    totals = Counter(item.outcome for item in checks)
    print()
    print(f"PASS: {totals[Outcome.PASS]}")
    print(f"FAIL: {totals[Outcome.FAIL]}")
    return 0 if totals[Outcome.FAIL] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
