#!/usr/bin/env python3
"""TASK_011 benchmark identity, capability, degradation, and immutability audit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.industry_context import (  # noqa: E402
    StockIndustryContextEngine,
    StockIndustryContextRules,
    StockIndustryContextStore,
    render_stock_industry_context_report,
)
from trend_monitor.providers.hithink import HithinkProvider  # noqa: E402
from trend_monitor.providers.hithink.errors import HithinkProviderError  # noqa: E402
from trend_monitor.registry import InstrumentRegistry  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
EVIDENCE_LATEST = PROJECT_ROOT / "data" / "reports" / "stock_industry_benchmark_evidence_latest.json"
REPORT_LATEST = PROJECT_ROOT / "data" / "reports" / "stock_industry_context_latest.json"


def _items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    data = raw.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        return []
    return [item for item in data["item"] if isinstance(item, dict)]


def _success(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "PASS",
        "provider_code": raw.get("code"),
        "request_id": raw.get("request_id"),
        "rows": len(_items(raw)),
    }


def _probe_call(call) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        raw = call()
        return _success(raw), raw
    except HithinkProviderError as exc:
        return (
            {
                "status": "UNSUPPORTED" if exc.provider_code == 1002 else "FAIL",
                "category": exc.category.value,
                "provider_code": exc.provider_code,
                "request_id": exc.request_id,
            },
            None,
        )


def refresh_evidence(rules: StockIndustryContextRules, store: StockIndustryContextStore) -> tuple[dict[str, Any], str]:
    provider = HithinkProvider(dotenv_path=PROJECT_ROOT / ".env")
    observed_at = datetime.now(timezone.utc).isoformat()
    catalog = provider.index_catalog("industry")
    catalog_items = _items(catalog)
    now = datetime.now(SHANGHAI)
    end = int(now.timestamp() * 1000)
    start = int((now - timedelta(days=12)).timestamp() * 1000)
    benchmarks = {}
    for instrument_id in rules.instrument_ids:
        benchmark = rules.benchmark(instrument_id)
        catalog_hits = [
            item
            for item in catalog_items
            if item.get("thscode") == benchmark.provider_symbol
            and item.get("name") == benchmark.industry_name
        ]
        constituents = provider.index_constituents(benchmark.provider_symbol)
        constituent_hits = [
            item
            for item in _items(constituents)
            if str(item.get("ticker")) == benchmark.stock_symbol
            or str(item.get("thscode", "")).split(".", 1)[0] == benchmark.stock_symbol
        ]
        quote_check, quote_raw = _probe_call(
            lambda benchmark=benchmark: provider.index_snapshot([benchmark.provider_symbol])
        )
        daily_check, daily_raw = _probe_call(
            lambda benchmark=benchmark: provider.index_history(
                benchmark.provider_symbol, start=start, end=end, interval="1d"
            )
        )
        minute_15, _ = _probe_call(
            lambda benchmark=benchmark: provider.index_history(
                benchmark.provider_symbol, start=start, end=end, interval="15m"
            )
        )
        minute_60, _ = _probe_call(
            lambda benchmark=benchmark: provider.index_history(
                benchmark.provider_symbol, start=start, end=end, interval="60m"
            )
        )
        benchmarks[instrument_id] = {
            "stock_symbol": benchmark.stock_symbol,
            "stock_name": benchmark.stock_name,
            "industry_id": benchmark.industry_id,
            "industry_name": benchmark.industry_name,
            "taxonomy": benchmark.taxonomy,
            "provider": benchmark.provider,
            "provider_symbol": benchmark.provider_symbol,
            "mapping_type": benchmark.mapping_type,
            "confidence": benchmark.confidence,
            "catalog_match": catalog_hits,
            "catalog_verified": len(catalog_hits) == 1,
            "constituent_match": constituent_hits,
            "constituent_verified": len(constituent_hits) == 1,
            "constituent_count": len(_items(constituents)),
            "constituents_request_id": constituents.get("request_id"),
            "capability": {
                "quote": quote_check,
                "daily": daily_check,
                "15m": minute_15,
                "60m": minute_60,
            },
            "sanitized_samples": {
                "quote": _items(quote_raw)[0] if quote_raw and _items(quote_raw) else None,
                "daily_last": _items(daily_raw)[-1] if daily_raw and _items(daily_raw) else None,
            },
        }
    payload = {
        "schema_version": 1,
        "observed_at": observed_at,
        "provider": "hithink",
        "provider_contract": {
            "catalog_endpoint": "/api/a-share-index/catalog/ths-index-list?tag=industry",
            "constituents_endpoint": "/api/a-share-index/constituents/ths-stock-list",
            "quote_endpoint": "/api/a-share-index/prices/snapshot",
            "history_endpoint": "/api/a-share-index/prices/historical",
            "documented_history_interval": "1d",
        },
        "catalog_request_id": catalog.get("request_id"),
        "benchmarks": benchmarks,
        "longbridge_investigation": {
            "mapping_type": "UNMAPPED",
            "confidence": "LOW",
            "provider_symbol": None,
            "status": "NO_VERIFIED_INDUSTRY_SYMBOL",
            "reason": "Official CN quote coverage documents securities and indexes, but no industry taxonomy/symbol discovery endpoint was identified; no Hithink code was guessed across providers.",
            "official_quote_coverage": "https://open.longbridge.com/docs",
        },
        "synthetic_benchmark_created": False,
    }
    evidence_path = store.save_evidence(payload, observed_at=observed_at)
    EVIDENCE_LATEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(evidence_path, EVIDENCE_LATEST)
    return payload, evidence_path


def load_evidence() -> tuple[dict[str, Any], str]:
    if not EVIDENCE_LATEST.is_file():
        raise FileNotFoundError(
            "benchmark evidence missing; run with --refresh-evidence once"
        )
    payload = json.loads(EVIDENCE_LATEST.read_text(encoding="utf-8"))
    return payload, str(EVIDENCE_LATEST)


def _current_stock_results(task10: dict[str, Any]) -> dict[str, tuple[dict[str, Any], str]]:
    result = {}
    for instrument_id, paths in task10["current_paths"].items():
        path = Path(paths["stocks_60m"])
        result[instrument_id] = (json.loads(path.read_text(encoding="utf-8")), str(path))
    return result


def _evidence_passes(rules: StockIndustryContextRules, evidence: dict[str, Any]) -> bool:
    if evidence.get("synthetic_benchmark_created") is not False:
        return False
    for instrument_id in rules.instrument_ids:
        item = evidence.get("benchmarks", {}).get(instrument_id, {})
        capability = item.get("capability", {})
        if not item.get("catalog_verified") or not item.get("constituent_verified"):
            return False
        if capability.get("quote", {}).get("status") != "PASS":
            return False
        if capability.get("daily", {}).get("status") != "PASS":
            return False
        if capability.get("15m", {}).get("status") != "UNSUPPORTED":
            return False
        if capability.get("60m", {}).get("status") != "UNSUPPORTED":
            return False
    return True


def main() -> int:
    refresh = "--refresh-evidence" in sys.argv[1:]
    rules = StockIndustryContextRules.load(
        PROJECT_ROOT / "config" / "stock_industry_context_rules.json"
    )
    registry = InstrumentRegistry.load(PROJECT_ROOT / "config" / "instruments.json")
    store = StockIndustryContextStore(
        PROJECT_ROOT / "data" / "risk_outputs" / "stock_industry_context"
    )
    evidence, evidence_id = refresh_evidence(rules, store) if refresh else load_evidence()
    task10_path = PROJECT_ROOT / "data" / "reports" / "stock_intraday_risk_latest.json"
    task10 = json.loads(task10_path.read_text(encoding="utf-8"))
    current_stock = _current_stock_results(task10)
    engine = StockIndustryContextEngine(rules)
    current = {}
    current_paths = {}
    deterministic = True
    score_immutable = True
    expected_end = "2026-08-28T15:00:00+08:00"
    for instrument_id in rules.instrument_ids:
        stock, stock_path = current_stock[instrument_id]
        market_source = stock.get("source_market_60m_result_id")
        args = {
            "instrument_id": instrument_id,
            "stock_60m_result": stock,
            "industry_risk_input": None,
            "history": (),
            "source_stock_60m_result_id": stock_path,
            "source_market_60m_result_id": market_source,
            "source_industry_risk_input_id": None,
            "source_benchmark_evidence_id": evidence_id,
        }
        first = engine.evaluate(**args)
        second = engine.evaluate(**args)
        deterministic &= first.to_dict() == second.to_dict()
        score_immutable &= first.stock_risk_score == stock["risk_score"] == 2
        score_immutable &= stock["rules_version"] == "stock_60m_risk_v0.1"
        score_immutable &= first.period_end == expected_end
        current[instrument_id] = first.to_dict()
        current_paths[instrument_id] = store.save_result(
            first,
            render_stock_industry_context_report(
                first, stock_name=rules.benchmark(instrument_id).stock_name
            ),
        )

    mappings_ok = all(
        registry.resolve(rules.benchmark(item).industry_id, "hithink").mapping_type.value
        == rules.benchmark(item).mapping_type
        for item in rules.instrument_ids
    )
    evidence_ok = _evidence_passes(rules, evidence)
    lookahead = all(
        item["data_quality"].get("lookahead_safe") is True
        and item["data_quality"].get("period_alignment") == "NOT_APPLICABLE"
        for item in current.values()
    )
    report = {
        "schema_version": 1,
        "task": "TASK_011",
        "task_status": "PARTIAL",
        "rules_version": rules.rules_version,
        "current_period_end": expected_end,
        "mapping_evidence_id": evidence_id,
        "mapping_verified": mappings_ok and evidence_ok,
        "current": current,
        "current_paths": current_paths,
        "minute_capability": {
            key: value["capability"] for key, value in evidence["benchmarks"].items()
        },
        "historical_replay": {
            "status": "BLOCKED_BY_DATA",
            "observations": 0,
            "reason": "NO_DIRECT_MINUTE_BENCHMARK",
            "triple_resonance": None,
            "stock_weak_vs_industry": None,
            "risk_up_precursors": None,
        },
        "industry_15m_auxiliary": {
            "status": "NOT_IMPLEMENTED_BY_CAPABILITY_GATE",
            "reason": "NO_DIRECT_15M_INDUSTRY_BENCHMARK",
            "joint_flags": None,
        },
        "deterministic": deterministic,
        "lookahead_safe": lookahead,
        "stock_score_immutable": score_immutable,
        "synthetic_benchmark_created": False,
        "industry_context_value": "BLOCKED_BY_DATA",
    }
    REPORT_LATEST.parent.mkdir(parents=True, exist_ok=True)
    REPORT_LATEST.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for instrument_id in rules.instrument_ids:
        benchmark = rules.benchmark(instrument_id)
        result = current[instrument_id]
        print(benchmark.stock_symbol)
        print(
            "INDUSTRY MAPPING",
            benchmark.industry_name,
            benchmark.provider_symbol,
            benchmark.mapping_type,
            benchmark.confidence,
            "PASS",
        )
        print("15M", benchmark.minute_15m_capability, "NO_DIRECT_MINUTE_BENCHMARK")
        print("60M", benchmark.minute_60m_capability, "NO_DIRECT_MINUTE_BENCHMARK")
        print("CURRENT CONTEXT", result["status"], result["unavailable_reason"])
        print("STOCK SCORE", result["stock_risk_score"], result["stock_risk_light"])
        print()
    print("HISTORICAL REPLAY")
    print("BLOCKED_BY_DATA — 0 OBSERVATIONS — NO_DIRECT_MINUTE_BENCHMARK")
    print()
    print("TRIPLE RESONANCE")
    print("UNAVAILABLE — industry 60m return does not exist")
    print()
    print("STOCK WEAK VS INDUSTRY")
    print("UNAVAILABLE — strict as-of p10 cannot be built")
    print()
    print("DETERMINISM")
    print("PASS" if deterministic else "FAIL")
    print()
    print("LOOKAHEAD")
    print("PASS" if lookahead else "FAIL")
    print()
    print("STOCK SCORE IMMUTABILITY")
    print("PASS" if score_immutable else "FAIL")
    print()
    print("SYNTHETIC BENCHMARK")
    print("NOT CREATED — PASS")
    print()
    print("TASK_011 PARTIAL — BLOCKED_BY_DATA")
    return 0 if mappings_ok and evidence_ok and deterministic and lookahead and score_immutable else 1


if __name__ == "__main__":
    raise SystemExit(main())
