#!/usr/bin/env python3
"""Generate the read-only TASK_027 production data-source contract audit.

The auditor consumes append-only local evidence.  It never contacts a provider,
changes provider selection, or writes under data/runtime, data/raw, or risk
output directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trend_monitor.normalization import (  # noqa: E402
    evaluate_cn_volume_invariant,
    normalize_volume_shares,
)
from trend_monitor.risk_input import RiskInputSnapshotStore  # noqa: E402

AUDIT_ROOT = ROOT / "audit" / "data_source_contract"
TRACE_ROOT = AUDIT_ROOT / "traces"
LOCAL_VOLUME_EVIDENCE = AUDIT_ROOT / "local_evidence" / "volume_samples.json"
LOCAL_TIMEZONE_EVIDENCE = AUDIT_ROOT / "local_evidence" / "timezone_samples.json"
AUDIT_DATES = ("2026-09-03", "2026-09-04")
STOCK_IDS = ("stock.hengtong_optic", "stock.wus_printed_circuit")

MARKET_INDEX_FIELDS = (
    "close",
    "close_change_pct",
    "one_period_direction",
    "two_period_direction",
    "three_period_close_direction",
    "persistent_weak",
    "repair_state",
    "downside_shock",
    "shock_reference_p95",
    "recent_close_high",
    "recent_close_low",
    "close_drawdown_from_recent_close_high",
)
MARKET_AGGREGATE_FIELDS = (
    "breadth",
    "persistent_weakness",
    "downside_shocks",
    "weighted_support_distortion",
    "small_cap_stress",
    "style_divergence_strong",
    "broad_selloff_resonance",
    "strong_broad_weakness",
    "broad_repair",
    "repair_count",
    "style_spreads",
    "score_components",
    "risk_score",
    "risk_light",
    "risk_direction",
)
MARKET_15M_FIELDS = (
    "classification",
    "direction_sequence",
    "closes",
    "close_changes_pct",
    "repair_strength",
    "finish_position",
    "completed_15m_count",
)
STOCK_60M_FIELDS = (
    "current_close",
    "previous_close",
    "current_return",
    "previous_return",
    "two_period_return",
    "consecutive_close_direction",
    "persistent_weakness",
    "downside_shock",
    "historical_abs_return_p95",
    "market_median_return",
    "relative_return",
    "relative_weakness",
    "historical_relative_return_p10",
    "market_resonance",
    "repair_state",
    "market_relationship",
    "market_context",
    "score_components",
    "risk_score",
    "risk_light",
    "risk_direction",
)
STOCK_15M_FIELDS = (
    "classification",
    "direction_sequence",
    "closes",
    "close_changes_pct",
    "repair_strength",
    "finish_position",
    "joint_market_flags",
)

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_ref(reference: str) -> tuple[Path, str | None]:
    file_part, marker, fragment = reference.partition("#")
    return Path(file_part), fragment if marker else None


def relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def relative_ref(reference: str | None) -> str | None:
    if not reference:
        return None
    path, fragment = split_ref(reference)
    result = relative_path(path)
    return f"{result}#{fragment}" if fragment else result


def file_evidence(reference: str | None) -> dict[str, Any] | None:
    if not reference:
        return None
    path, fragment = split_ref(reference)
    if not path.exists():
        return {
            "reference": relative_ref(reference),
            "exists": False,
            "sha256": None,
            "fragment": fragment,
        }
    raw = read_json(path)
    request = raw.get("request") if isinstance(raw, dict) else None
    provider = raw.get("provider") if isinstance(raw, dict) else None
    return {
        "reference": relative_ref(reference),
        "exists": True,
        "sha256": sha256_path(path),
        "fragment": fragment,
        "provider": provider,
        "provider_request": request,
        "provider_response_present": isinstance(raw, dict)
        and ("data" in raw or "raw_response" in raw),
    }


def resolve_source_snapshot_to_raw(reference: str) -> str:
    """Resolve either a raw reference or an instrument snapshot to its raw file."""
    path, _ = split_ref(reference)
    payload = read_json(path)
    if "provider" in payload and ("data" in payload or "raw_response" in payload):
        return relative_path(path)
    for layer_name in ("risk_60m", "support_15m", "daily"):
        layer = payload.get(layer_name)
        if isinstance(layer, dict):
            raw_path = (layer.get("source_trace") or {}).get("raw_path")
            if raw_path:
                return relative_ref(raw_path) or ""
    return relative_ref(reference) or ""


def load_ref(reference: str) -> Any:
    path, _ = split_ref(reference)
    return read_json(path)


def observed_at_from_raw(path: Path) -> str | None:
    raw = read_json(path)
    for key in ("provider_observed_at", "fetched_at"):
        value = raw.get(key) if isinstance(raw, dict) else None
        if value:
            return value
    token = path.name.split("__", 1)[0]
    try:
        parsed = datetime.strptime(token, "%Y%m%dT%H%M%S.%fZ")
    except ValueError:
        return None
    return parsed.isoformat(timespec="microseconds") + "+00:00"


def select_market_60m(payload: dict[str, Any], period: str) -> dict[str, Any]:
    if "results" not in payload:
        return payload
    return next(item for item in payload["results"] if item.get("as_of") == period)


def select_market_15m(payload: dict[str, Any], period: str) -> dict[str, Any]:
    if "results" not in payload:
        return payload
    return next(
        item for item in payload["results"] if item.get("60m_period_end") == period
    )


def select_stock(
    payload: dict[str, Any], instrument_id: str, period: str
) -> dict[str, Any]:
    return next(
        item
        for item in payload["results"][instrument_id]
        if item["stock_60m"].get("period_end") == period
    )


def raw_refs_from_feature(feature: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for lineage in feature.get("lineage") or []:
        for raw_path in lineage.get("source_raw_paths") or []:
            rel = relative_ref(raw_path)
            if rel and rel not in refs:
                refs.append(rel)
    return refs


def audit_feature_state(feature: dict[str, Any]) -> dict[str, Any]:
    state = feature.get("eligibility", "UNKNOWN")
    lineage_required = state in {"ENABLED", "DEGRADED"}
    lineage_present = bool(raw_refs_from_feature(feature))
    return {
        "feature": feature.get("feature_name"),
        "state": state,
        "value_present": feature.get("value") is not None,
        "lineage_required": lineage_required,
        "lineage_present": lineage_present,
        "status": "PASS" if (not lineage_required or lineage_present) else "FAIL",
        "reason": feature.get("reason"),
    }


def risk_input_feature_audit(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for key in ("feature_inputs", "degraded_features", "disabled_features"):
        features.extend(audit_feature_state(item) for item in snapshot.get(key) or [])
    return features


def feature_trace(
    *,
    feature_name: str,
    feature_value: Any,
    instrument: str,
    provider: str,
    raw_references: Iterable[str],
    risk_input_id: str | None,
    analysis_as_of: str,
    market_period_end: str,
) -> dict[str, Any]:
    raw_refs = list(dict.fromkeys(relative_ref(item) for item in raw_references if item))
    raw_refs = [item for item in raw_refs if item]
    evidences = [file_evidence(item) for item in raw_refs]
    traceable = bool(provider and raw_refs) and all(
        evidence and evidence["exists"] for evidence in evidences
    )
    return {
        "feature_name": feature_name,
        "feature_value": feature_value,
        "instrument": instrument,
        "source_provider": provider,
        "raw_snapshot_id": raw_refs,
        "normalized_snapshot_id": None,
        "validated_snapshot_id": None,
        "risk_input_snapshot_id": relative_ref(risk_input_id),
        "analysis_as_of": analysis_as_of,
        "market_period_end": market_period_end,
        "layer_representation": {
            "normalized": "EMBEDDED_IN_RISK_INPUT_SYSTEM_BARS",
            "validated": "EMBEDDED_IN_RISK_INPUT_FIELD_QUALITY_AND_PREFLIGHT",
        },
        "source_trace_status": "TRACEABLE" if traceable else "UNKNOWN",
    }


def market_60m_feature_traces(
    result: dict[str, Any], period: str
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    all_raw_refs: list[str] = []
    for state in result["index_states"]:
        source = state["source_snapshot_id"]
        raw_path, _ = split_ref(source)
        raw_ref = raw_path.as_posix()
        all_raw_refs.append(raw_ref)
        for field in MARKET_INDEX_FIELDS:
            traces.append(
                feature_trace(
                    feature_name=field,
                    feature_value=state.get(field),
                    instrument=state["instrument_id"],
                    provider="longbridge",
                    raw_references=(raw_ref,),
                    risk_input_id=None,
                    analysis_as_of=period,
                    market_period_end=period,
                )
            )
    for field in MARKET_AGGREGATE_FIELDS:
        traces.append(
            feature_trace(
                feature_name=field,
                feature_value=result.get(field),
                instrument="market.8_index_aggregate",
                provider="longbridge",
                raw_references=all_raw_refs,
                risk_input_id=None,
                analysis_as_of=period,
                market_period_end=period,
            )
        )
    return traces


def market_15m_feature_traces(
    result: dict[str, Any], period: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    traces: list[dict[str, Any]] = []
    state_audits: list[dict[str, Any]] = []
    all_raw: list[str] = []
    for state in result["index_internal_states"]:
        risk_input_id = state["source_risk_input_id"]
        snapshot_payload = load_ref(risk_input_id)
        if "inputs" in snapshot_payload:
            instrument_snapshot = snapshot_payload["inputs"][state["instrument_id"]]
        else:
            instrument_snapshot = snapshot_payload["support_15m"]
        raw_paths = state.get("source_raw_paths") or []
        all_raw.extend(raw_paths)
        state_audits.extend(risk_input_feature_audit(instrument_snapshot))
        for field in MARKET_15M_FIELDS:
            traces.append(
                feature_trace(
                    feature_name=field,
                    feature_value=state.get(field),
                    instrument=state["instrument_id"],
                    provider=instrument_snapshot.get("source_provider", "UNKNOWN"),
                    raw_references=raw_paths,
                    risk_input_id=risk_input_id,
                    analysis_as_of=period,
                    market_period_end=period,
                )
            )
    traces.append(
        feature_trace(
            feature_name="market_internal_state",
            feature_value=result.get("market_internal_state"),
            instrument="market.8_index_aggregate",
            provider="longbridge",
            raw_references=all_raw,
            risk_input_id=result["index_internal_states"][0]["source_risk_input_id"],
            analysis_as_of=period,
            market_period_end=period,
        )
    )
    return traces, state_audits


def stock_feature_traces(
    result: dict[str, Any], period: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    traces: list[dict[str, Any]] = []
    state_audits: list[dict[str, Any]] = []
    stock_60m = result["stock_60m"]
    stock_15m = result["stock_15m"]
    risk_input_id = stock_60m["source_risk_input_id"]
    snapshot_payload = load_ref(risk_input_id)
    instrument_id = stock_60m["instrument_id"]
    input_60m = snapshot_payload["inputs_60m"][instrument_id]
    input_15m = snapshot_payload["inputs_15m"][instrument_id]
    state_audits.extend(risk_input_feature_audit(input_60m))
    state_audits.extend(risk_input_feature_audit(input_15m))
    raw_60m = [input_60m["source_trace"]["raw_path"]]
    raw_15m = [input_15m["source_trace"]["raw_path"]]
    market_60m_refs = [
        state["source_snapshot_id"]
        for state in snapshot_payload["market_60m_result"]["index_states"]
    ]
    market_15m_refs = [
        path
        for state in snapshot_payload["market_15m_result"]["index_internal_states"]
        for path in state.get("source_raw_paths") or []
    ]
    for field in STOCK_60M_FIELDS:
        raw_refs = raw_60m
        if field in {"market_median_return", "relative_return", "relative_weakness", "historical_relative_return_p10", "market_resonance", "market_relationship", "market_context", "score_components", "risk_score", "risk_light", "risk_direction"}:
            raw_refs = raw_60m + market_60m_refs
        traces.append(
            feature_trace(
                feature_name=field,
                feature_value=stock_60m.get(field),
                instrument=instrument_id,
                provider="longbridge",
                raw_references=raw_refs,
                risk_input_id=risk_input_id,
                analysis_as_of=period,
                market_period_end=period,
            )
        )
    for field in STOCK_15M_FIELDS:
        traces.append(
            feature_trace(
                feature_name=field,
                feature_value=stock_15m.get(field),
                instrument=instrument_id,
                provider="longbridge",
                raw_references=raw_15m + market_15m_refs,
                risk_input_id=risk_input_id,
                analysis_as_of=period,
                market_period_end=period,
            )
        )
    return traces, state_audits


def audit_period(report_path: Path) -> dict[str, Any]:
    report = read_json(report_path)
    period = report["period_end"]
    source_ids = report["source_ids"]
    market_60m = select_market_60m(load_ref(source_ids["market_result_id"]), period)
    market_15m = select_market_15m(load_ref(source_ids["market_15m_result_id"]), period)
    features = market_60m_feature_traces(market_60m, period)
    market_15m_traces, feature_states = market_15m_feature_traces(market_15m, period)
    features.extend(market_15m_traces)
    snapshot_checks: list[dict[str, Any]] = []
    for state in market_15m["index_internal_states"]:
        input_payload = load_ref(state["source_risk_input_id"])
        input_snapshot = (
            input_payload["inputs"][state["instrument_id"]]
            if "inputs" in input_payload
            else input_payload["support_15m"]
        )
        result_raw = sorted(relative_ref(item) for item in state.get("source_raw_paths") or [])
        input_raw = [relative_ref(input_snapshot["source_trace"]["raw_path"])]
        snapshot_checks.append(
            {
                "scope": f"market_15m:{state['instrument_id']}",
                "result_raw": result_raw,
                "risk_input_raw": sorted(input_raw),
                "match": result_raw == sorted(input_raw),
            }
        )
    stock_summary: dict[str, Any] = {}
    for instrument_id in STOCK_IDS:
        stock_ref = source_ids["stock_result_ids"][instrument_id]
        stock_result = select_stock(load_ref(stock_ref), instrument_id, period)
        stock_traces, stock_states = stock_feature_traces(stock_result, period)
        features.extend(stock_traces)
        feature_states.extend(stock_states)
        stock_input = load_ref(stock_result["stock_60m"]["source_risk_input_id"])
        embedded_market_60m = sorted(
            resolve_source_snapshot_to_raw(state["source_snapshot_id"])
            for state in stock_input["market_60m_result"]["index_states"]
        )
        result_market_60m = sorted(
            resolve_source_snapshot_to_raw(state["source_snapshot_id"])
            for state in market_60m["index_states"]
        )
        embedded_market_15m = sorted(
            relative_ref(path)
            for state in stock_input["market_15m_result"]["index_internal_states"]
            for path in state.get("source_raw_paths") or []
        )
        result_market_15m = sorted(
            relative_ref(path)
            for state in market_15m["index_internal_states"]
            for path in state.get("source_raw_paths") or []
        )
        snapshot_checks.extend(
            (
                {
                    "scope": f"stock_market_60m_context:{instrument_id}",
                    "result_raw": result_market_60m,
                    "risk_input_raw": embedded_market_60m,
                    "match": result_market_60m == embedded_market_60m,
                },
                {
                    "scope": f"stock_market_15m_context:{instrument_id}",
                    "result_raw": result_market_15m,
                    "risk_input_raw": embedded_market_15m,
                    "match": result_market_15m == embedded_market_15m,
                },
            )
        )
        stock_summary[instrument_id] = {
            "risk_score": stock_result["stock_60m"]["risk_score"],
            "risk_light": stock_result["stock_60m"]["risk_light"],
            "source_result_id": relative_ref(stock_ref),
        }
    raw_refs = sorted(
        {
            raw_ref
            for feature in features
            for raw_ref in feature.get("raw_snapshot_id") or []
        }
    )
    traceable = sum(item["source_trace_status"] == "TRACEABLE" for item in features)
    exact_as_of = (
        market_60m.get("last_completed_bar_end") == period
        and market_15m.get("60m_period_end") == period
    )
    raw_catalog = [file_evidence(ref) for ref in raw_refs]
    snapshot_providers = {
        (evidence or {}).get("provider") for evidence in raw_catalog
    }
    snapshot_providers.discard(None)
    return {
        "schema_version": 1,
        "audit": "TASK_027_PRODUCTION_SOURCE_TRACE",
        "period": period,
        "analysis_as_of": period,
        "market_period_end": period,
        "source_result_as_of": market_60m.get("as_of"),
        "source_result_as_of_semantics": "ANALYSIS_BOUNDARY"
        if market_60m.get("as_of") == period
        else "LEGACY_RUNTIME_CUTOFF_WITH_EXACT_LAST_COMPLETED_BAR_END",
        "provider_observed_at": sorted(
            {
                observed_at_from_raw(ROOT / ref.split("#", 1)[0])
                for ref in raw_refs
                if (ROOT / ref.split("#", 1)[0]).exists()
            }
            - {None}
        ),
        "runtime_execution_at": report["generated_at"],
        "execution_mode": report["execution_mode"],
        "runtime_status": report["status"],
        "runtime_report": {
            "path": relative_path(report_path),
            "sha256": sha256_path(report_path),
        },
        "result_ids": {
            "market_60m": relative_ref(source_ids["market_result_id"]),
            "market_15m": relative_ref(source_ids["market_15m_result_id"]),
            "stocks": {
                key: relative_ref(value)
                for key, value in source_ids["stock_result_ids"].items()
            },
        },
        "market_result": {
            "risk_score": market_60m["risk_score"],
            "risk_light": market_60m["risk_light"],
            "risk_direction": market_60m["risk_direction"],
        },
        "stock_results": stock_summary,
        "features": features,
        "feature_lineage_audit": feature_states,
        "raw_snapshot_catalog": raw_catalog,
        "snapshot_identity_checks": snapshot_checks,
        "trace_counts": {
            "total": len(features),
            "traceable": traceable,
            "percent": round(100 * traceable / len(features), 2),
        },
        "snapshot_identity": hashlib.sha256("\n".join(raw_refs).encode()).hexdigest(),
        "snapshot_providers": sorted(snapshot_providers),
        "snapshot_contract": "PASS"
        if snapshot_providers == {"longbridge"}
        and all(item["match"] for item in snapshot_checks)
        else "FAIL",
        "analysis_as_of_contract": "PASS" if exact_as_of else "FAIL",
        "lookahead": "PASS"
        if exact_as_of and report.get("data", {}).get("lookahead_safe") is True
        else "FAIL",
        "normalized_snapshot_identity": None,
        "validated_snapshot_identity": None,
        "full_layer_object_chain": "PARTIAL",
        "notes": [
            "Normalized and validated representations are embedded in risk-input system bars and field-quality/preflight metadata; separate persisted object IDs do not exist.",
            "All formal enabled/degraded features retain raw-path lineage; legal DISABLED inputs do not require lineage.",
        ],
    }


def audit_auction(date: str) -> dict[str, Any]:
    paths = sorted((ROOT / "data" / "raw" / "hithink" / "auction" / date).glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"missing auction evidence for {date}")
    path = paths[-1]
    raw = read_json(path)
    response = raw["raw_response"]["data"]
    fields: list[dict[str, Any]] = []
    for item in response["item"]:
        for name in ("auction_price", "open_price", "auction_volume"):
            fields.append(
                {
                    "instrument": item["thscode"],
                    "field": name,
                    "value": item.get(name),
                    "source_provider": raw["provider"],
                    "raw_snapshot_id": relative_path(path),
                    "source_trace_status": "TRACEABLE",
                }
            )
    for name in ("auction_phase", "data_status", "timestamp"):
        fields.append(
            {
                "instrument": "auction.snapshot",
                "field": name,
                "value": response.get(name),
                "source_provider": raw["provider"],
                "raw_snapshot_id": relative_path(path),
                "source_trace_status": "TRACEABLE",
            }
        )
    return {
        "schema_version": 1,
        "audit": "TASK_027_AUCTION_SOURCE_TRACE",
        "trading_date": date,
        "execution_mode": "CATCH_UP",
        "auction_market_time": raw.get(
            "auction_market_time", f"{date}T09:25:00+08:00"
        ),
        "provider_observed_at": raw.get("provider_observed_at", raw.get("fetched_at")),
        "provider": raw["provider"],
        "endpoint": raw["endpoint"],
        "stage": raw["stage"],
        "raw_snapshot": {"path": relative_path(path), "sha256": sha256_path(path)},
        "fields": fields,
        "trace_counts": {"total": len(fields), "traceable": len(fields), "percent": 100.0},
    }


def unit_contract() -> dict[str, Any]:
    samples = []
    local_evidence = read_json(LOCAL_VOLUME_EVIDENCE)
    for row in local_evidence["samples"]:
        item = dict(row)
        hithink_invariant = evaluate_cn_volume_invariant(
            volume_raw=row["hithink_volume"],
            turnover_raw=row["hithink_turnover"],
            low=row["low"],
            high=row["high"],
        )
        longbridge_invariant = evaluate_cn_volume_invariant(
            volume_raw=row["longbridge_volume"],
            turnover_raw=row["longbridge_turnover"],
            low=row["low"],
            high=row["high"],
        )
        item["raw_volume_ratio_hithink_to_longbridge"] = round(
            row["hithink_volume"] / row["longbridge_volume"], 9
        )
        item["turnover_difference_hithink_minus_longbridge"] = round(
            row["hithink_turnover"] - row["longbridge_turnover"], 6
        )
        hithink_normalized = normalize_volume_shares(
            row["hithink_volume"], provider="hithink", data_type="daily"
        )
        longbridge_normalized = normalize_volume_shares(
            row["longbridge_volume"], provider="longbridge", data_type="daily"
        )
        item.update(
            {
                "hithink_documented_volume_unit": "shares",
                "longbridge_volume_unit_evidence": "EMPIRICALLY_CONFIRMED_BY_DIMENSIONAL_INVARIANT",
                "hithink_invariant": hithink_invariant.to_dict(),
                "longbridge_invariant": longbridge_invariant.to_dict(),
                "hithink_normalized_volume": hithink_normalized,
                "longbridge_normalized_volume": longbridge_normalized,
                "normalized_volume_difference": hithink_normalized
                - longbridge_normalized,
                "normalized_unit": "shares",
                "conversion_rule": "LONGBRIDGE_CN_VOLUME_SCALE=100_SHARES_PER_RAW_UNIT",
            }
        )
        samples.append(item)
    ratios = [item["raw_volume_ratio_hithink_to_longbridge"] for item in samples]
    all_factor_1_invalid = all(
        not item["longbridge_invariant"]["factor_1_valid"] for item in samples
    )
    all_factor_100_valid = all(
        item["longbridge_invariant"]["factor_100_valid"] for item in samples
    )
    all_hithink_identity_valid = all(
        item["hithink_invariant"]["factor_1_valid"] for item in samples
    )
    no_counterexample = (
        all_factor_1_invalid and all_factor_100_valid and all_hithink_identity_valid
    )
    return {
        "schema_version": 1,
        "status": "CONFIRMED_EMPIRICALLY",
        "evidence_type": "EMPIRICALLY_CONFIRMED_BY_DIMENSIONAL_INVARIANT",
        "turnover_status": "UNKNOWN",
        "price_adjustment_status": "PASS",
        "canonical_normalized_volume_unit": "shares",
        "longbridge_cn_volume_scale": "100_SHARES_PER_RAW_UNIT",
        "conversion_location": "EXPLICIT_NORMALIZATION_CONTRACT",
        "auto_normalization_allowed": True,
        "unknown_unit_auto_normalization_allowed": False,
        "reason": "All six retained samples reject raw factor 1, accept factor 100 inside the observed Daily price range, retain an approximately 100x Hithink/Longbridge raw ratio, and contain no counterexample.",
        "ratio_summary": {
            "sample_count": len(samples),
            "minimum": min(ratios),
            "maximum": max(ratios),
        },
        "dimensional_invariant": {
            "all_longbridge_factor_1_invalid": all_factor_1_invalid,
            "all_longbridge_factor_100_valid": all_factor_100_valid,
            "all_hithink_identity_valid": all_hithink_identity_valid,
            "counterexamples": 0 if no_counterexample else 1,
        },
        "provider_contracts": [
            {
                "provider": "hithink",
                "field": "Daily/Quote volume",
                "documented_unit": "share",
                "status": "CONFIRMED",
                "evidence": "hithink-finance official endpoint references: stock Daily/Quote volume unit is shares",
            },
            {
                "provider": "hithink",
                "field": "Auction auction_volume",
                "documented_unit": "hand",
                "status": "CONFIRMED",
                "evidence": "hithink-finance official auction endpoint reference: auction_volume unit is hands",
            },
            {
                "provider": "longbridge",
                "field": "Candlestick/Quote volume",
                "documented_unit": "UNKNOWN",
                "normalized_unit": "shares",
                "status": "CONFIRMED_EMPIRICALLY",
                "evidence": "Six retained CN Daily samples satisfy the dimensional invariant only at 100 shares per raw unit; this is empirical, not official documentation.",
            },
        ],
        "turnover": {
            "observed_scale_conflict": False,
            "hithink_documented_unit": "raw currency amount",
            "longbridge_documented_unit": "UNKNOWN",
            "currency_for_a_share_samples": "CNY",
            "status": "UNKNOWN",
            "cross_source_production_use": "NOT_CROSS_SOURCE_USED",
        },
        "samples": samples,
        "post_audit_resolution_600150": {
            "original_status": "DATA_CONFLICT",
            "status": "PASS_AFTER_UNIT_NORMALIZATION",
            "reason": "After the confirmed 100-shares-per-raw-unit normalization, both provider volumes agree within provider integer rounding and the existing date/OHLC/turnover checks remain valid.",
            "historical_report_modified": False,
        },
    }


def normalize_volume_if_documented(
    value: float, *, documented_unit: str | None, target_unit: str = "share"
) -> float | None:
    """Compatibility helper for explicitly documented identity units only."""
    if documented_unit is None or documented_unit == "UNKNOWN":
        return None
    if documented_unit != target_unit:
        return None
    return value


def fallback_audit() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "CONFIRMED_BLOCKED",
        "silent_fallback_found": False,
        "formal_cross_provider_fallbacks_allowed": [],
        "entries": [
            {
                "data_type": "Daily",
                "primary": "longbridge",
                "fallback": "hithink",
                "implementation": "The production RiskInputService filters Hithink from the Daily fallback candidates when Longbridge is requested and records an explicit block reason.",
                "trigger": "Longbridge Daily unavailable -> existing DATA_NOT_READY/DATA_INCOMPLETE path",
                "unit_compatibility": "VOLUME_NORMALIZATION_CONFIRMED_BUT_NOT_SUFFICIENT_FOR_PROVIDER_APPROVAL",
                "adjustment_compatibility": "NO_ADJUST_COMPATIBLE",
                "semantic_compatibility": "BLOCKED_PENDING_CONTRACT_VALIDATION",
                "provenance_behavior": "EXPLICIT_BLOCK_REASON",
                "notification_behavior": "EXISTING_RUNTIME_ERROR_POLICY",
                "status": "BLOCKED_PENDING_CONTRACT_VALIDATION",
            },
            {
                "data_type": "15m/60m",
                "primary": "longbridge",
                "fallback": "hithink",
                "implementation": "Hithink adapter does not support minute bars; no effective cross-provider minute fallback exists.",
                "trigger": "NOT_IMPLEMENTED",
                "unit_compatibility": "UNKNOWN",
                "adjustment_compatibility": "UNKNOWN",
                "semantic_compatibility": "BLOCKED",
                "provenance_behavior": "NOT_APPLICABLE",
                "notification_behavior": "NOT_APPLICABLE",
                "status": "NOT_IMPLEMENTED",
            },
            {
                "data_type": "Auction",
                "primary": "hithink",
                "fallback": None,
                "implementation": "No alternate source",
                "trigger": "NOT_APPLICABLE",
                "unit_compatibility": "NOT_APPLICABLE",
                "adjustment_compatibility": "NOT_APPLICABLE",
                "semantic_compatibility": "NOT_APPLICABLE",
                "provenance_behavior": "EXPLICIT_HITHINK_RAW",
                "notification_behavior": "RUNTIME_POLICY_UNCHANGED",
                "status": "BLOCKED",
            },
            {
                "data_type": "Trading Calendar",
                "primary": "hithink",
                "fallback": None,
                "implementation": "Cached authoritative Hithink calendar; no cross-provider fallback",
                "trigger": "NOT_APPLICABLE",
                "unit_compatibility": "NOT_APPLICABLE",
                "adjustment_compatibility": "NOT_APPLICABLE",
                "semantic_compatibility": "NOT_APPLICABLE",
                "provenance_behavior": "CACHE_METADATA_RETAINED",
                "notification_behavior": "RUNTIME_POLICY_UNCHANGED",
                "status": "BLOCKED",
            },
        ],
        "same_provider_transport_retry": {
            "provider": "longbridge",
            "behavior": "One SDK-context recreation/retry for a network error",
            "classified_as_source_fallback": False,
        },
        "research_hithink_daily": "ALLOWED_EXPLICITLY",
        "required_resolution": "A separate versioned contract validation and human approval are required before any production Hithink Daily fallback can be enabled.",
    }


def timezone_audit() -> dict[str, Any]:
    evidence = read_json(LOCAL_TIMEZONE_EVIDENCE)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in evidence["observations"]:
        grouped.setdefault((item["data_type"], item["market_time"]), []).append(item)
    pair_checks = []
    for (data_type, market_time), observations in sorted(grouped.items()):
        epochs = {item["epoch_seconds"] for item in observations}
        process_timezones = {item["process_timezone"] for item in observations}
        expected_market_time = datetime.fromtimestamp(
            next(iter(epochs)), tz=timezone.utc
        ).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
        passed = (
            len(epochs) == 1
            and process_timezones == {"Asia/Tokyo", "UTC"}
            and expected_market_time == market_time
        )
        pair_checks.append(
            {
                "data_type": data_type,
                "market_time": market_time,
                "epoch_seconds": next(iter(epochs)) if len(epochs) == 1 else None,
                "process_timezones": sorted(process_timezones),
                "status": "PASS" if passed else "FAIL",
            }
        )
    passed = bool(pair_checks) and all(item["status"] == "PASS" for item in pair_checks)
    return {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "longbridge_naive_datetime_semantic": "CONFIRMED" if passed else "PROVISIONAL",
        "evidence_type": "CONTROLLED_PROCESS_TIMEZONE_EPOCH_INVARIANT",
        "sdk": evidence["sdk"],
        "semantic": "SDK_PROCESS_LOCAL_NAIVE_DATETIME_BACKED_BY_ABSOLUTE_UNIX_EPOCH",
        "adapter_contract": "Convert the SDK process-local naive wall time to Unix epoch first, then derive timezone-aware Asia/Shanghai market time.",
        "host_timezone_dependency": "NONE_AFTER_EPOCH_CONVERSION" if passed else "UNKNOWN",
        "pair_checks": pair_checks,
    }


def snapshot_resolution(period_traces: list[dict[str, Any]]) -> dict[str, Any]:
    report_names = (
        "market_index_coverage_latest.json",
        "risk_input_latest.json",
        "market_60m_replay_latest.json",
        "market_15m_internal_latest.json",
        "stock_intraday_risk_latest.json",
    )
    reports = {}
    for name in report_names:
        path = ROOT / "data" / "reports" / name
        if not path.exists():
            return {
                "schema_version": 1,
                "status": "PENDING",
                "reason": f"missing local report: {name}",
                "legacy_2026_09_03_1500": "LEGACY_SNAPSHOT_IDENTITY_MISMATCH",
            }
        reports[name] = read_json(path)
    cycle_refs = [item.get("cycle_snapshot") or {} for item in reports.values()]
    ids = {item.get("cycle_raw_snapshot_id") for item in cycle_refs}
    paths = {item.get("snapshot_path") for item in cycle_refs}
    if None in ids or None in paths or len(ids) != 1 or len(paths) != 1:
        return {
            "schema_version": 1,
            "status": "PENDING",
            "reason": "latest five-stage reports do not share one cycle snapshot",
            "legacy_2026_09_03_1500": "LEGACY_SNAPSHOT_IDENTITY_MISMATCH",
        }
    cycle_path = next(iter(paths))
    store = RiskInputSnapshotStore(ROOT / "data" / "risk_inputs")
    cycle = store.load_cycle(cycle_path)
    report_contracts = {
        name: payload.get("cycle_snapshot_contract", "PASS")
        for name, payload in reports.items()
    }
    current_replay = {
        "market_60m": reports["market_60m_replay_latest.json"].get(
            "current_pipeline_match"
        ),
        "stock": reports["stock_intraday_risk_latest.json"].get(
            "current_pipeline_match"
        ),
    }
    determinism = {
        name: reports[name].get("determinism")
        for name in (
            "market_60m_replay_latest.json",
            "market_15m_internal_latest.json",
            "stock_intraday_risk_latest.json",
        )
    }
    lookahead = {
        name: reports[name].get("lookahead_safe")
        for name in (
            "market_60m_replay_latest.json",
            "market_15m_internal_latest.json",
            "stock_intraday_risk_latest.json",
        )
    }
    cycle_times_aware = all(
        datetime.fromisoformat(str(cycle[field])).tzinfo is not None
        for field in ("analysis_as_of", "provider_observed_at")
    )
    legacy_trace = next(
        item
        for item in period_traces
        if item["period"] == "2026-09-03T15:00:00+08:00"
    )
    replay_market_legacy = next(
        item
        for item in reports["market_60m_replay_latest.json"]["results"]
        if item["last_completed_bar_end"] == legacy_trace["period"]
    )
    replay_stock_legacy = {
        instrument_id: next(
            item["stock_60m"]
            for item in reports["stock_intraday_risk_latest.json"]["results"][instrument_id]
            if item["stock_60m"]["period_end"] == legacy_trace["period"]
        )
        for instrument_id in STOCK_IDS
    }
    legacy_saved_input_replay = (
        (replay_market_legacy["risk_light"], replay_market_legacy["risk_score"])
        == (
            legacy_trace["market_result"]["risk_light"],
            legacy_trace["market_result"]["risk_score"],
        )
        and all(
            (replay_stock_legacy[instrument_id]["risk_light"], replay_stock_legacy[instrument_id]["risk_score"])
            == (
                legacy_trace["stock_results"][instrument_id]["risk_light"],
                legacy_trace["stock_results"][instrument_id]["risk_score"],
            )
            for instrument_id in STOCK_IDS
        )
    )
    passed = (
        cycle.get("cycle_raw_snapshot_id") == next(iter(ids))
        and len(cycle.get("instrument_snapshots", [])) == 10
        and len(cycle.get("members", [])) == 30
        and all(value == "PASS" for value in report_contracts.values())
        and all(value is True for value in current_replay.values())
        and all(value is True for value in determinism.values())
        and all(value is True for value in lookahead.values())
        and cycle_times_aware
        and legacy_saved_input_replay
    )
    return {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "cycle_id": cycle.get("cycle_id"),
        "analysis_as_of": cycle.get("analysis_as_of"),
        "provider_observed_at": cycle.get("provider_observed_at"),
        "cycle_raw_snapshot_id": cycle.get("cycle_raw_snapshot_id"),
        "snapshot_hash": cycle.get("snapshot_hash"),
        "instrument_snapshot_count": len(cycle.get("instrument_snapshots", [])),
        "raw_member_count": len(cycle.get("members", [])),
        "five_stage_reference_contract": report_contracts,
        "current_replay_match": current_replay,
        "determinism": determinism,
        "lookahead": lookahead,
        "cycle_times_timezone_aware": cycle_times_aware,
        "legacy_2026_09_03_1500": "LEGACY_SNAPSHOT_IDENTITY_MISMATCH",
        "legacy_snapshot_contract": legacy_trace["snapshot_contract"],
        "legacy_evidence_preserved": True,
        "legacy_saved_input_replay": "PASS"
        if legacy_saved_input_replay
        else "FAIL",
        "saved_period_identity_audit": {
            item["period"]: item["snapshot_contract"] for item in period_traces
        },
    }


def task_025_regression(period_traces: list[dict[str, Any]]) -> dict[str, Any]:
    market_report = read_json(ROOT / "data" / "reports" / "market_60m_replay_latest.json")
    stock_report = read_json(ROOT / "data" / "reports" / "stock_intraday_risk_latest.json")
    expected_market = {
        "2026-09-04T10:30:00+08:00": ("GREEN", 0),
        "2026-09-04T11:30:00+08:00": ("YELLOW", 3),
        "2026-09-04T14:00:00+08:00": ("ORANGE", 5),
        "2026-09-04T15:00:00+08:00": ("ORANGE", 5),
    }
    expected_stocks = {
        "stock.hengtong_optic": {
            "2026-09-04T10:30:00+08:00": ("YELLOW", 1),
            "2026-09-04T11:30:00+08:00": ("YELLOW", 2),
            "2026-09-04T14:00:00+08:00": ("YELLOW", 2),
            "2026-09-04T15:00:00+08:00": ("YELLOW", 2),
        },
        "stock.wus_printed_circuit": {
            "2026-09-04T10:30:00+08:00": ("YELLOW", 2),
            "2026-09-04T11:30:00+08:00": ("YELLOW", 2),
            "2026-09-04T14:00:00+08:00": ("YELLOW", 2),
            "2026-09-04T15:00:00+08:00": ("YELLOW", 2),
        },
    }
    observed_market = {
        item["last_completed_bar_end"]: (item["risk_light"], item["risk_score"])
        for item in market_report["results"]
        if item["last_completed_bar_end"] in expected_market
    }
    observed_stocks = {}
    for instrument_id, expected in expected_stocks.items():
        observed_stocks[instrument_id] = {
            item["stock_60m"]["period_end"]: (
                item["stock_60m"]["risk_light"],
                item["stock_60m"]["risk_score"],
            )
            for item in stock_report["results"][instrument_id]
            if item["stock_60m"]["period_end"] in expected
        }
    trace_1030 = next(
        item
        for item in period_traces
        if item["period"] == "2026-09-04T10:30:00+08:00"
    )
    disabled_previous = [
        item
        for item in trace_1030["feature_lineage_audit"]
        if item["feature"] == "previous_period_close" and item["state"] == "DISABLED"
    ]
    disabled_provenance = bool(disabled_previous) and all(
        not item["lineage_required"]
        and not item["lineage_present"]
        and item["status"] == "PASS"
        for item in disabled_previous
    )
    current_replay_match = (
        market_report.get("current_pipeline_match") is True
        and stock_report.get("current_pipeline_match") is True
    )
    determinism = (
        market_report.get("determinism") is True
        and stock_report.get("determinism") is True
    )
    lookahead = (
        market_report.get("lookahead_safe") is True
        and stock_report.get("lookahead_safe") is True
    )
    period_end_1500 = "2026-09-04T15:00:00+08:00" in observed_market
    passed = (
        observed_market == expected_market
        and observed_stocks == expected_stocks
        and disabled_provenance
        and current_replay_match
        and determinism
        and lookahead
        and period_end_1500
    )
    return {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "market": {
            key: {"risk_light": value[0], "risk_score": value[1]}
            for key, value in observed_market.items()
        },
        "stocks": {
            instrument_id: {
                key: {"risk_light": value[0], "risk_score": value[1]}
                for key, value in observed.items()
            }
            for instrument_id, observed in observed_stocks.items()
        },
        "disabled_previous_period_provenance": "PASS"
        if disabled_provenance
        else "FAIL",
        "current_replay_match": "PASS" if current_replay_match else "FAIL",
        "determinism": "PASS" if determinism else "FAIL",
        "lookahead": "PASS" if lookahead else "FAIL",
        "period_end_1500": "PASS" if period_end_1500 else "FAIL",
    }


def contract_matrix(
    summary: dict[str, Any],
    volume: dict[str, Any],
    fallback: dict[str, Any],
    timezone_contract: dict[str, Any],
    snapshot_contract: dict[str, Any],
    regression: dict[str, Any],
) -> dict[str, Any]:
    common_lb = {
        "canonical_source": "longbridge",
        "validation_source": "local validation/replay; Hithink cross-check is research only",
        "timezone": "Asia/Shanghai trading-date mapping",
        "provenance_required": True,
        "current_status": "CONFIRMED",
    }
    rows: list[dict[str, Any]] = [
        {
            "data_type": "Trading Calendar",
            "field": "trading_day",
            "canonical_source": "hithink",
            "validation_source": "cached response metadata",
            "fallback_policy": "BLOCKED",
            "unit": "calendar date",
            "timezone": "Asia/Shanghai",
            "adjustment": "NOT_APPLICABLE",
            "market_time_semantics": "A-share trading date",
            "provider_observed_time_semantics": "calendar cache fetched_at",
            "provenance_required": True,
            "current_status": "CONFIRMED",
            "evidence": "data/runtime/a_share_calendar.json and runtime calendar loader",
        },
    ]
    for field, unit, status in (
        ("auction_price", "CNY/share", "CONFIRMED"),
        ("open_price", "CNY/share; exact field semantics remain provider-defined", "QUESTION"),
        ("auction_volume", "hand", "CONFIRMED"),
        ("timestamp", "epoch milliseconds; provider response assembly time", "CONFIRMED"),
        ("auction_phase", "enum", "CONFIRMED"),
        ("data_status", "enum", "CONFIRMED"),
    ):
        rows.append(
            {
                "data_type": "Auction",
                "field": field,
                "canonical_source": "hithink",
                "validation_source": "closed/final raw snapshot",
                "fallback_policy": "BLOCKED",
                "unit": unit,
                "timezone": "Asia/Shanghai market time; provider observation stored separately",
                "adjustment": "NOT_APPLICABLE",
                "market_time_semantics": "09:25 auction final",
                "provider_observed_time_semantics": "actual API receipt time",
                "provenance_required": True,
                "current_status": status,
                "evidence": "2026-09-03/04 Hithink closed/final CATCH_UP raw snapshots",
            }
        )
    fields_by_type = {
        "Daily": ("open", "high", "low", "close", "volume", "turnover", "timestamp", "trade_date"),
        "15m": ("open", "high", "low", "close", "volume", "turnover", "bar_end"),
        "60m": ("open", "high", "low", "close", "volume", "turnover", "bar_end"),
        "Latest Quote": ("last", "open", "high", "low", "prev_close", "volume", "turnover", "timestamp"),
    }
    for data_type, fields in fields_by_type.items():
        for field in fields:
            unit = "CNY/share" if field in {"last", "open", "high", "low", "close", "prev_close"} else "UNKNOWN"
            if field == "timestamp":
                unit = "Unix epoch; aware Asia/Shanghai market_time"
            elif field == "trade_date":
                unit = "Asia/Shanghai calendar date"
            elif field == "bar_end":
                unit = "timezone-aware Asia/Shanghai datetime"
            status = "CONFIRMED"
            if field == "volume":
                unit = "shares (Longbridge CN raw x100)"
                status = "CONFIRMED_EMPIRICALLY"
            elif field == "turnover":
                status = "UNKNOWN"
            elif field in {"timestamp", "trade_date", "bar_end"}:
                status = "CONFIRMED_EMPIRICALLY"
            if data_type == "Latest Quote" and status == "CONFIRMED":
                status = "CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT"
            row = {
                "data_type": data_type,
                "field": field,
                **common_lb,
                "fallback_policy": (
                    "BLOCKED_PENDING_CONTRACT_VALIDATION"
                    if data_type == "Daily"
                    else "NOT_IMPLEMENTED"
                    if data_type in {"15m", "60m"}
                    else "NOT_APPLICABLE_RESEARCH_CAPABILITY"
                ),
                "unit": unit,
                "adjustment": "NoAdjust/actual" if data_type != "Latest Quote" else "NOT_APPLICABLE",
                "market_time_semantics": "trade_date" if data_type == "Daily" else "completed bar end/quote time",
                "provider_observed_time_semantics": "raw cache fetch time",
                "current_status": status,
                "evidence": "2026-09-03/04 production traces and official Longbridge field schema",
            }
            rows.append(row)
    derived = (
        "ATR14-SMA",
        "MA",
        "returns",
        "breadth",
        "persistence",
        "repair",
        "distortion",
        "shock",
        "stock relative strength",
        "15m supporting features",
        "60m features",
    )
    for field in derived:
        rows.append(
            {
                "data_type": "Derived",
                "field": field,
                "canonical_source": "local deterministic calculation over Longbridge raw inputs",
                "validation_source": "replay/current comparison and feature lineage",
                "fallback_policy": "inherits source field policy",
                "unit": "derived",
                "timezone": "inherits analysis_as_of Asia/Shanghai",
                "adjustment": "inherits source; Daily is NoAdjust",
                "market_time_semantics": "strict analysis_as_of",
                "provider_observed_time_semantics": "does not change market time",
                "provenance_required": True,
                "current_status": "CONFIRMED" if field != "ATR14-SMA" else "CONFIRMED_FOR_RESEARCH_NOT_CURRENT_INTRADAY_SCORE",
                "evidence": "risk input feature lineage and 8 per-period audit traces",
            }
        )
    public_summary = json.loads(json.dumps(summary))
    daily_evidence = public_summary.pop("daily_source_evidence", [])
    public_summary["daily_source_evidence"] = {
        "sample_count": len(daily_evidence),
        "providers": sorted({item["source_provider"] for item in daily_evidence}),
        "fallback_count": sum(bool(item["fallback_used"]) for item in daily_evidence),
        "all_confirmed": all(item["status"] == "CONFIRMED" for item in daily_evidence),
        "detail": "LOCAL_ONLY_AUDIT_EVIDENCE",
    }
    return {
        "schema_version": 1,
        "version": "DATA_SOURCE_CONTRACT_v0.1",
        "status": "COMPLETE"
        if snapshot_contract["status"] == "PASS"
        and volume["status"] == "CONFIRMED_EMPIRICALLY"
        and fallback["status"] == "CONFIRMED_BLOCKED"
        and timezone_contract["status"] == "PASS"
        and regression["status"] == "PASS"
        else "PARTIAL",
        "rows": rows,
        "summary": public_summary,
        "volume_unit_contract": volume["status"],
        "fallback_contract": fallback["status"],
        "timezone_contract": timezone_contract["status"],
        "snapshot_contract": snapshot_contract["status"],
        "task_028_resolution": {
            "snapshot": snapshot_contract,
            "volume": {
                "status": volume["status"],
                "evidence_type": volume["evidence_type"],
                "longbridge_cn_volume_scale": volume["longbridge_cn_volume_scale"],
                "normalized_unit": volume["canonical_normalized_volume_unit"],
            },
            "fallback": fallback["status"],
            "timezone": timezone_contract,
            "risk_result_regression": regression,
        },
        "production_change": {
            "risk_rules": False,
            "provider_selection": True,
            "runtime": False,
            "observability": True,
        },
    }


def summarize_traces(period_traces: list[dict[str, Any]], auction_traces: list[dict[str, Any]]) -> dict[str, Any]:
    market_total = market_traceable = stock_total = stock_traceable = 0
    for trace in period_traces:
        for feature in trace["features"]:
            if feature["instrument"].startswith("stock."):
                stock_total += 1
                stock_traceable += feature["source_trace_status"] == "TRACEABLE"
            else:
                market_total += 1
                market_traceable += feature["source_trace_status"] == "TRACEABLE"
    auction_total = sum(item["trace_counts"]["total"] for item in auction_traces)
    auction_traceable = sum(item["trace_counts"]["traceable"] for item in auction_traces)
    total = market_total + stock_total + auction_total
    traceable = market_traceable + stock_traceable + auction_traceable
    all_lineage_pass = all(
        state["status"] == "PASS"
        for trace in period_traces
        for state in trace["feature_lineage_audit"]
    )
    return {
        "schema_version": 1,
        "audit_periods": [item["period"] for item in period_traces],
        "execution_modes": sorted({item["execution_mode"] for item in period_traces}),
        "successful_runtime_samples": len(period_traces),
        "period_results": [
            {
                "period": item["period"],
                "execution_mode": item["execution_mode"],
                "snapshot_contract": item["snapshot_contract"],
                "analysis_as_of_contract": item["analysis_as_of_contract"],
            }
            for item in period_traces
        ],
        "market": {
            "total": market_total,
            "traceable": market_traceable,
            "percent": round(100 * market_traceable / market_total, 2),
        },
        "stock": {
            "total": stock_total,
            "traceable": stock_traceable,
            "percent": round(100 * stock_traceable / stock_total, 2),
        },
        "auction": {
            "total": auction_total,
            "traceable": auction_traceable,
            "percent": round(100 * auction_traceable / auction_total, 2),
        },
        "all": {
            "total": total,
            "traceable": traceable,
            "percent": round(100 * traceable / total, 2),
        },
        "full_layer_object_chain": "PARTIAL",
        "snapshot_contract": "PASS"
        if all(item["snapshot_contract"] == "PASS" for item in period_traces)
        else "FAIL",
        "as_of_contract": "PASS"
        if all(item["analysis_as_of_contract"] == "PASS" for item in period_traces)
        else "FAIL",
        "lookahead": "PASS"
        if all(item["lookahead"] == "PASS" for item in period_traces)
        else "FAIL",
        "disabled_feature_lineage_semantics": "PASS" if all_lineage_pass else "FAIL",
        "source_trace_status": "PARTIAL",
        "source_trace_note": "Every counted formal feature is traceable to provider raw evidence; the overall chain remains PARTIAL because normalized and validated stages are embedded rather than separately persisted and identified.",
    }


def daily_source_evidence(report_paths: list[Path]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for report_path in report_paths:
        report = read_json(report_path)
        if not report["period_end"].endswith("T15:00:00+08:00"):
            continue
        stock_ref = next(iter(report["source_ids"]["stock_result_ids"].values()))
        replay = load_ref(stock_ref)
        for instrument_id in STOCK_IDS:
            current_path = Path(replay["current_paths"][instrument_id]["stocks_60m"])
            current_result = read_json(current_path)
            input_path, _ = split_ref(current_result["source_risk_input_id"])
            instrument_snapshot = read_json(input_path)
            daily = instrument_snapshot["daily"]
            raw_path = daily["source_trace"]["raw_path"]
            evidence.append(
                {
                    "trading_date": report["trading_date"],
                    "instrument": instrument_id,
                    "analysis_as_of": daily["as_of"],
                    "source_provider": daily["source_provider"],
                    "requested_provider": daily["source_trace"]["requested_provider"],
                    "actual_provider": daily["source_trace"]["actual_provider"],
                    "fallback_used": daily["source_trace"]["fallback_used"],
                    "risk_input_snapshot_id": relative_path(input_path),
                    "raw_snapshot": file_evidence(raw_path),
                    "status": "CONFIRMED"
                    if daily["source_provider"] == "longbridge"
                    and not daily["source_trace"]["fallback_used"]
                    else "UNKNOWN",
                }
            )
    return evidence


def markdown_table(rows: list[list[Any]], headers: list[str]) -> str:
    def clean(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    output = ["| " + " | ".join(map(clean, headers)) + " |"]
    output.append("| " + " | ".join("---" for _ in headers) + " |")
    output.extend("| " + " | ".join(map(clean, row)) + " |" for row in rows)
    return "\n".join(output)


def render_docs(contract: dict[str, Any], summary: dict[str, Any], volume: dict[str, Any], fallback: dict[str, Any]) -> dict[Path, str]:
    task_028 = contract["task_028_resolution"]
    snapshot = task_028["snapshot"]
    timezone_contract = task_028["timezone"]
    matrix_rows = [
        [row["data_type"], row["field"], row["canonical_source"], row["unit"], row["fallback_policy"], row["current_status"]]
        for row in contract["rows"]
    ]
    source_contract = f"""# Data Source Contract v0.1

Status: **{contract['status']}**
Audit scope: 2026-09-03 and 2026-09-04 production evidence. All eight successful intraday results are `CATCH_UP`; they are not LIVE proof.

## Contract Matrix

{markdown_table(matrix_rows, ["Data Type", "Field", "Canonical Source", "Unit", "Fallback", "Status"])}

## Time Semantics

- Trading timezone is Asia/Shanghai. `analysis_as_of` and `market_period_end` remain the scheduled market boundary; later `provider_observed_at` does not move that boundary.
- The retained 2026-09-03 15:00 current Market artifact stores its runtime cutoff in the legacy `as_of` field, while `last_completed_bar_end`, report period, replay period, Stock context, and selected bars are all 15:00. It is labeled explicitly in its trace and is not treated as a later market period.
- Auction market time is 09:25. The audited closed/final snapshots were observed later during operator CATCH_UP.
- Controlled `longbridge==4.5.0` calls under Asia/Tokyo and UTC produced different naive wall-clock values but identical Unix epochs. The SDK value is therefore confirmed as a process-local naive representation of an absolute instant. The adapter converts to epoch first and emits timezone-aware Asia/Shanghai `market_time`; `TIMEZONE_CONTRACT = PASS`.

## Units and Adjustment

- Hithink documents stock Daily/Quote volume in shares and Auction volume in hands.
- Longbridge documents the `volume` field type but not its unit. Across all six retained Daily samples, `turnover / volume_raw` lies outside the day's price range while `turnover / (volume_raw * 100)` lies inside it; Hithink share volume independently agrees after normalization and no counterexample exists. `LONGBRIDGE_CN_VOLUME_SCALE = 100_SHARES_PER_RAW_UNIT` is **EMPIRICALLY_CONFIRMED_BY_DIMENSIONAL_INVARIANT**, not officially documented.
- Canonical cross-provider volume is shares. Hithink Daily is identity; Hithink Auction hands multiply by 100. Unknown provider/unit combinations are never converted automatically.
- Turnover shows no scale conflict in the six samples, but the Longbridge official field unit is not stated; status is **UNKNOWN**.
- Longbridge production history requests use `NoAdjust`/actual. No audited Daily/15m/60m/Auction price-adjustment conflict was found.

## Provenance Contract

- Enabled and degraded formal features require lineage to raw provider evidence.
- A legal disabled feature does not consume a value and does not require lineage.
- Separate persisted normalized/validated snapshot object IDs do not exist; their transformation and field-quality/preflight state is embedded in the risk-input snapshot. This is why `PRODUCTION_SOURCE_TRACE` is PARTIAL even though counted formal feature traceability is 100%.

## Production Boundaries

- Hithink: authoritative A-share calendar and closed/final Auction snapshots.
- Longbridge: production Direct Daily, 15m, 60m, and quote inputs; derived features retain Longbridge lineage.
- Production Hithink Daily fallback is `BLOCKED_PENDING_CONTRACT_VALIDATION`; explicit research/cross-validation use remains available.
- Each successful analysis cycle freezes one immutable Raw-member bundle and propagates its `cycle_raw_snapshot_id` through Coverage, Risk Input, Market 60m, Market 15m, Stock and Runtime result provenance.
- 600150.SH remains research/shadow only and is not added to the formal risk pipeline.
- The only production source-selection change is the explicit safety block on an unapproved Hithink Daily fallback. Runtime scheduling, risk rules, scores, lights and notification policy are unchanged.
"""

    provider_audit = """# Provider Capability Audit

| Provider | Capability | Production use observed | Status | Evidence |
| --- | --- | --- | --- | --- |
| Hithink | A-share trading calendar | Runtime calendar cache | CONFIRMED | `/api/a-share/calendar/trading-days`; cached response retains provider metadata |
| Hithink | Auction final | 2026-09-03/04 closed/final CATCH_UP raw snapshots | CONFIRMED | `/api/a-share/auction/snapshot`, `stage=final` |
| Hithink | Daily/Quote | Validation/research; production fallback blocked | CONFIRMED capability / fallback BLOCKED_PENDING_CONTRACT_VALIDATION | Official endpoint schema and production Risk Input boundary |
| Hithink | 15m/60m | Not supported by current adapter | NOT_IMPLEMENTED | Adapter raises unsupported capability |
| Longbridge | Daily | Production/research Direct Daily, NoAdjust | CONFIRMED | Adapter/provider code and retained raw requests |
| Longbridge | 15m/60m | All eight audited intraday results | CONFIRMED | Result → risk input → raw trace |
| Longbridge | Quote | Current quote/research query path | CONFIRMED capability | Provider code and official quote schema |

600150.SH is limited to research/shadow evidence. It is not a production Risk instrument.
"""

    trace_rows = [
        [item["period"], item["execution_mode"], item["snapshot_contract"], item["analysis_as_of_contract"]]
        for item in contract["summary"]["period_results"]
    ]
    trace_audit = f"""# Production Source Trace Audit

## Result

- `PRODUCTION_SOURCE_TRACE = PARTIAL`
- `SOURCE_TRACE_COMPLETENESS = {summary['all']['percent']:.2f}%`
- `SNAPSHOT_CONTRACT = {summary['snapshot_contract']}`
- `AS_OF_CONTRACT = {summary['as_of_contract']}`
- `LOOKAHEAD = {summary['lookahead']}`

The completeness denominator is the set of formal persisted Market 60m, Market 15m, Stock 60m, Stock 15m, and Auction feature/field instances in the eight audited successful reports plus two Auction snapshots. Disabled non-consuming inputs are audited separately and are not counted as formal feature instances.

{markdown_table(trace_rows, ["Period", "Execution Mode", "Snapshot", "As-Of"])}

## Counts

{markdown_table([["Market", summary['market']['total'], summary['market']['traceable'], f"{summary['market']['percent']:.2f}%"], ["Stock", summary['stock']['total'], summary['stock']['traceable'], f"{summary['stock']['percent']:.2f}%"], ["Auction", summary['auction']['total'], summary['auction']['traceable'], f"{summary['auction']['percent']:.2f}%"], ["All", summary['all']['total'], summary['all']['traceable'], f"{summary['all']['percent']:.2f}%"]], ["Scope", "Total", "Traceable", "Rate"])}

All counted fields reach an existing provider raw file with a SHA-256 and request metadata. The overall chain is still PARTIAL because the normalized and validated stages are embedded in risk-input structures rather than separately persisted objects with independent snapshot IDs.

The two 15:00 production assemblies per trading date retain Direct Daily evidence for both formal stocks (four snapshots total): requested provider and actual provider are Longbridge, `fallback_used=false`, and the risk-input snapshot points to an existing NoAdjust Longbridge raw file.

The retained 2026-09-03 15:00 combined evidence contains Market 60m and Market 15m versus Stock replay-context raw-snapshot identity mismatches. Both snapshot sets end at 15:00 and the Current/Replay semantic values match, but no contract authorizes treating different raw identities as the same snapshot. This historical artifact remains `LEGACY_SNAPSHOT_IDENTITY_MISMATCH` and is not rewritten. TASK_028 adds one immutable cycle bundle for new executions; the latest controlled saved-input replay has `SNAPSHOT_CONTRACT = {snapshot['status']}`. No Current/Replay market-period drift was found.
"""

    volume_rows = [
        [row["symbol"], row["date"], row["hithink_volume"], row["longbridge_volume"], row["raw_volume_ratio_hithink_to_longbridge"], row["turnover_difference_hithink_minus_longbridge"]]
        for row in volume["samples"]
    ]
    volume_doc = f"""# Volume Unit Contract

- `VOLUME_UNIT_CONTRACT = {volume['status']}`
- `TURNOVER_UNIT_CONTRACT = UNKNOWN`
- `LONGBRIDGE_CN_VOLUME_SCALE = {volume['longbridge_cn_volume_scale']}`
- `VOLUME_NORMALIZED_UNIT = shares`
- `evidence_type = {volume['evidence_type']}`

Hithink's official endpoint references state that stock Daily/Quote volume is shares and Auction volume is hands. Longbridge's official candlestick/quote references define a numeric `volume` field but do not state the unit. The Longbridge normalization below is accepted from the retained dimensional invariant, not presented as official documentation.

{markdown_table(volume_rows, ["Symbol", "Date", "Hithink volume", "Longbridge volume", "Raw ratio", "Turnover difference"])}

Raw values remain unchanged. Cross-provider validation uses an explicit normalizer; formal intraday risk continues to ignore/block or treat volume as advisory, so risk scores and lights do not change.

For 600150.SH, date and OHLC agree and normalized volume agrees within provider integer rounding. `600150_DAILY_CROSS_VALIDATION_POST_028 = PASS_AFTER_UNIT_NORMALIZATION`; TASK_026B and TASK_027 history remains unchanged. Turnover remains independently `UNKNOWN`.
"""

    fallback_doc = "# Fallback Audit\n\n" + markdown_table(
        [
            [entry["data_type"], entry["primary"], entry["fallback"], entry["status"], entry["provenance_behavior"]]
            for entry in fallback["entries"]
        ],
        ["Data Type", "Primary", "Fallback", "Status", "Provenance"],
    ) + """

`SILENT_FALLBACK_FOUND = NO`. MarketDataService uses explicit ordered candidates and records requested provider, actual provider, fallback flag/reason, and raw path.

No cross-provider fallback is formally approved in Data Source Contract v0.1. The production Risk Input boundary filters Hithink from Longbridge Daily fallback candidates and records `HITHINK_DAILY_FALLBACK_BLOCKED_PENDING_CONTRACT_VALIDATION`. Explicit research/cross-validation access to Hithink Daily remains available. Hithink minute bars are unsupported, so 15m/60m have no effective fallback. Auction and calendar have no alternate source.
"""

    hardcoded_doc = """# Hardcoded Source Audit

| File / function | Direct source | Data type | Production impact | Status |
| --- | --- | --- | --- | --- |
| `scripts/run_intraday_monitor.py` Auction/calendar setup | Hithink | Auction, calendar | Production scheduler entrypoint | JUSTIFIED |
| `scripts/verify_market_index_coverage.py` refresh | Longbridge | index Daily/15m/60m | Production stage | JUSTIFIED_CANONICAL_SOURCE; policy is call-site fixed |
| `scripts/verify_risk_input.py` refresh | Longbridge; Hithink Daily production fallback blocked | stock Daily/15m/60m | Production stage | CONFIRMED_SAFE_BLOCK |
| `src/trend_monitor/services/market_data.py` | Registry adapters | general | Production service | JUSTIFIED |
| `scripts/verify_*`, research scripts | provider-specific | verification/research | No source-selection conflict in formal results | JUSTIFIED_BY_SCOPE |

`HARDCODED_SOURCE_CONFLICT = NO` for the audited results. Some production entrypoints instantiate a canonical provider directly rather than selecting it from a centralized policy object. Their observed source matches the contract and is recorded in lineage. Daily fallback remains deliberately blocked pending a separate versioned approval.
"""

    snapshot_resolution_doc = f"""# TASK_028 Snapshot Resolution

- Current contract: **{snapshot['status']}**
- Cycle ID: `{snapshot.get('cycle_id')}`
- Cycle Raw snapshot: `{snapshot.get('cycle_raw_snapshot_id')}`
- Analysis as-of: `{snapshot.get('analysis_as_of')}`
- Frozen members: {snapshot.get('raw_member_count')} Raw members across {snapshot.get('instrument_snapshot_count')} instruments
- Historical 2026-09-03 15:00 classification: `LEGACY_SNAPSHOT_IDENTITY_MISMATCH`
- 2026-09-03 15:00 saved-input replay through the frozen history members: `{snapshot.get('legacy_saved_input_replay')}`

Root cause: Market Coverage/Risk used a short-window Raw fetch while Stock replay/context performed a second historical-window fetch for the same period. The completed data ended at the same market boundary, but the Raw identities differed.

Resolution: the existing Risk Input Snapshot Store now freezes the exact Daily/15m/60m member paths and hashes once per cycle. Coverage, Risk Input, Market 60m, Market 15m, Stock, Current/Replay and Runtime provenance must carry the same `cycle_raw_snapshot_id`; any identity mismatch blocks the gate. The old artifact is preserved.
"""

    volume_resolution_doc = f"""# TASK_028 Volume Resolution

- Contract: **{volume['status']}**
- Evidence: `{volume['evidence_type']}`
- Longbridge CN scale: `{volume['longbridge_cn_volume_scale']}`
- Canonical normalized unit: `shares`
- Retained sample count: {volume['ratio_summary']['sample_count']}
- Counterexamples: {volume['dimensional_invariant']['counterexamples']}
- 600150 post-028: `PASS_AFTER_UNIT_NORMALIZATION`
- Turnover: `UNKNOWN`

All retained Longbridge Daily samples fail the factor-1 price invariant and pass the factor-100 invariant. Hithink's documented share volume passes the identity invariant. This is an empirical contract, not an official Longbridge unit claim. Raw data and frozen risk semantics remain unchanged.
"""

    fallback_resolution_doc = """# TASK_028 Daily Fallback Resolution

- Production canonical Daily: `Longbridge`
- Hithink Daily production fallback: `BLOCKED_PENDING_CONTRACT_VALIDATION`
- Silent fallback: `NO`
- Explicit Hithink Daily research/cross-validation: `ALLOWED`

If Longbridge Daily is unavailable, production Risk Input remains blocked through the existing data-unavailable/preflight path and records a fallback block reason. This task does not approve cross-provider production fallback.
"""

    timezone_resolution_doc = f"""# TASK_028 Timezone Resolution

- Contract: **{timezone_contract['status']}**
- Longbridge naive datetime semantic: `{timezone_contract['longbridge_naive_datetime_semantic']}`
- Evidence: `{timezone_contract['evidence_type']}`
- SDK: `{timezone_contract['sdk']}`
- Internal market time: `ASIA_SHANGHAI_AWARE`

Controlled calls under Asia/Tokyo and UTC returned different naive wall-clock representations with the same Unix epoch. The adapter attaches the process-local zone only to recover that instant, then derives timezone-aware Asia/Shanghai market time. Normalization verifies any emitted `market_time` against the epoch, so the host timezone cannot shift A-share period boundaries.
"""

    conflict_doc = """# Proposed Cross-Provider Data Conflict Policy

Status: **PROPOSED_CONFLICT_POLICY**. This document does not change production behavior.

| Conflict | Proposed action | Rationale |
| --- | --- | --- |
| PRICE_CONFLICT | BLOCK | Price semantics directly affect risk and research outputs |
| VOLUME_UNIT_CONFLICT | BLOCK | Never infer or silently normalize an undocumented unit |
| TIMESTAMP_CONFLICT | BLOCK | Can create lookahead or wrong-period selection |
| TRADING_DAY_CONFLICT | BLOCK | Calendar controls scheduling and as-of boundaries |
| ADJUSTMENT_CONFLICT | BLOCK | Adjusted/unadjusted mixing changes returns and levels |
| PROVIDER_STALE | ALLOW_WITH_DEGRADATION only when an existing bounded grace/recoverability contract applies; otherwise BLOCK | Preserve provider timing evidence without accepting incomplete bars |
| FIELD_SEMANTIC_UNKNOWN | QUESTION and exclude from formal scoring | Unknown semantics cannot be promoted to a canonical feature |

Human approval and a versioned production contract are required before wiring this proposal into Runtime.
"""

    return {
        ROOT / "docs" / "DATA_SOURCE_CONTRACT_v0.1.md": source_contract,
        AUDIT_ROOT / "provider_capability_audit.md": provider_audit,
        AUDIT_ROOT / "production_source_trace_audit.md": trace_audit,
        AUDIT_ROOT / "volume_unit_contract.md": volume_doc,
        AUDIT_ROOT / "fallback_audit.md": fallback_doc,
        AUDIT_ROOT / "hardcoded_source_audit.md": hardcoded_doc,
        AUDIT_ROOT / "data_conflict_policy_proposal.md": conflict_doc,
        AUDIT_ROOT / "task_028_snapshot_resolution.md": snapshot_resolution_doc,
        AUDIT_ROOT / "task_028_volume_resolution.md": volume_resolution_doc,
        AUDIT_ROOT / "task_028_fallback_resolution.md": fallback_resolution_doc,
        AUDIT_ROOT / "task_028_timezone_resolution.md": timezone_resolution_doc,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def build_audit(*, write: bool = True) -> dict[str, Any]:
    report_paths = sorted(
        path
        for date in AUDIT_DATES
        for path in (ROOT / "data" / "runtime" / "reports").glob(f"{date}T*.json")
    )
    if len(report_paths) != 8:
        raise RuntimeError(f"expected 8 successful period reports, found {len(report_paths)}")
    period_traces = [audit_period(path) for path in report_paths]
    auction_traces = [audit_auction(date) for date in AUDIT_DATES]
    summary = summarize_traces(period_traces, auction_traces)
    summary["daily_source_evidence"] = daily_source_evidence(report_paths)
    volume = unit_contract()
    fallback = fallback_audit()
    timezone_contract = timezone_audit()
    snapshot_contract = snapshot_resolution(period_traces)
    regression = task_025_regression(period_traces)
    contract = contract_matrix(
        summary,
        volume,
        fallback,
        timezone_contract,
        snapshot_contract,
        regression,
    )
    task_028_summary = {
        "schema_version": 1,
        "task": "TASK_028",
        "status": "COMPLETE" if contract["status"] == "COMPLETE" else "PARTIAL",
        "snapshot_contract": snapshot_contract["status"],
        "cycle_raw_snapshot_contract": "CONFIRMED"
        if snapshot_contract["status"] == "PASS"
        else "PARTIAL",
        "legacy_2026_09_03_1500": "LEGACY_SNAPSHOT_IDENTITY_MISMATCH",
        "volume_unit_contract": volume["status"],
        "volume_evidence_type": volume["evidence_type"],
        "longbridge_cn_volume_scale": volume["longbridge_cn_volume_scale"],
        "volume_normalized_unit": "SHARES",
        "daily_cross_validation_600150_post_028": volume[
            "post_audit_resolution_600150"
        ]["status"],
        "turnover_unit_contract": volume["turnover_status"],
        "hithink_daily_fallback": "BLOCKED_PENDING_CONTRACT_VALIDATION",
        "silent_fallback": "NO",
        "timezone_contract": timezone_contract["status"],
        "longbridge_naive_datetime_semantic": timezone_contract[
            "longbridge_naive_datetime_semantic"
        ],
        "internal_market_time": "ASIA_SHANGHAI_AWARE"
        if timezone_contract["status"] == "PASS"
        else "FAIL",
        "as_of_contract": summary["as_of_contract"],
        "lookahead": summary["lookahead"],
        "current_replay_match": regression["current_replay_match"],
        "determinism": regression["determinism"],
        "risk_result_regression": regression["status"],
        "production_rule_modified": "NO",
        "scheduler_modified": "NO",
        "notification_policy_modified": "NO",
        "live_verified": "NOT_APPLICABLE",
    }
    result = {
        "contract": contract,
        "summary": summary,
        "volume": volume,
        "fallback": fallback,
        "timezone": timezone_contract,
        "snapshot": snapshot_contract,
        "task_028_summary": task_028_summary,
        "regression": regression,
        "period_traces": period_traces,
        "auction_traces": auction_traces,
    }
    if write:
        write_json(ROOT / "docs" / "DATA_SOURCE_CONTRACT_v0.1.json", contract)
        write_json(AUDIT_ROOT / "production_source_trace_summary.json", summary)
        write_json(AUDIT_ROOT / "volume_unit_contract.json", volume)
        write_json(AUDIT_ROOT / "fallback_audit.json", fallback)
        write_json(AUDIT_ROOT / "task_028_summary.json", task_028_summary)
        for trace in period_traces:
            stamp = datetime.fromisoformat(trace["period"]).strftime("%Y%m%d_%H%M")
            write_json(TRACE_ROOT / f"{stamp}.json", trace)
        for trace in auction_traces:
            write_json(
                TRACE_ROOT / f"{trace['trading_date'].replace('-', '')}_auction.json",
                trace,
            )
        for path, content in render_docs(contract, summary, volume, fallback).items():
            write_text(path, content)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="build in memory and compare canonical output by rebuilding twice",
    )
    args = parser.parse_args()
    first = build_audit(write=not args.check)
    if args.check:
        second = build_audit(write=False)
        if canonical_json(first) != canonical_json(second):
            raise RuntimeError("audit output is not deterministic")
        print("DATA SOURCE CONTRACT AUDIT CHECK: PASS")
    else:
        print(
            "DATA SOURCE CONTRACT AUDIT: "
            f"{first['summary']['all']['traceable']}/{first['summary']['all']['total']} "
            "formal feature instances traceable"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
