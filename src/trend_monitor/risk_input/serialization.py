"""Deserialize stable Risk Input snapshots without consulting Provider data."""

from __future__ import annotations

from trend_monitor.schemas import (
    AnalysisPeriod,
    AssetType,
    FeatureEligibility,
    FeatureInput,
    FeatureLineage,
    PreflightStatus,
    RiskBar,
    RiskInput,
    RiskInputDataStatus,
    RiskSourceTrace,
    InstrumentRiskInputBundle,
)


def risk_input_from_dict(value: dict[str, object]) -> RiskInput:
    trace_raw = dict(value["source_trace"])
    trace = RiskSourceTrace(**trace_raw)
    bars_list = []
    for raw in value["system_bars"]:
        item = dict(raw)
        item["source_bar_ids"] = tuple(item["source_bar_ids"])
        item["source_raw_paths"] = tuple(item["source_raw_paths"])
        bars_list.append(RiskBar(**item))
    bars = tuple(bars_list)

    def features(key: str) -> tuple[FeatureInput, ...]:
        result = []
        for raw in value[key]:
            item = dict(raw)
            lineage_items = []
            for raw_child in item["lineage"]:
                child = dict(raw_child)
                child["source_bar_ids"] = tuple(child["source_bar_ids"])
                child["source_raw_paths"] = tuple(child["source_raw_paths"])
                lineage_items.append(FeatureLineage(**child))
            lineage = tuple(lineage_items)
            result.append(
                FeatureInput(
                    feature_name=str(item["feature_name"]),
                    value=item["value"],
                    field_source=tuple(item["field_source"]),
                    quality=dict(item["quality"]),
                    eligibility=FeatureEligibility(str(item["eligibility"])),
                    reason=str(item["reason"]),
                    lineage=lineage,
                )
            )
        return tuple(result)

    return RiskInput(
        instrument_id=str(value["instrument_id"]),
        asset_type=AssetType(str(value["asset_type"])),
        analysis_period=AnalysisPeriod(str(value["analysis_period"])),
        as_of=str(value["as_of"]),
        trading_date=str(value["trading_date"]) if value.get("trading_date") is not None else None,
        source_provider=str(value["source_provider"]) if value.get("source_provider") is not None else None,
        source_trace=trace,
        system_bars=bars,
        feature_inputs=features("feature_inputs"),
        disabled_features=features("disabled_features"),
        degraded_features=features("degraded_features"),
        data_status=RiskInputDataStatus(str(value["data_status"])),
        preflight_status=PreflightStatus(str(value["preflight_status"])),
        last_completed_bar_end=(
            str(value["last_completed_bar_end"])
            if value.get("last_completed_bar_end") is not None
            else None
        ),
        data_fetched_at=str(value["data_fetched_at"]) if value.get("data_fetched_at") is not None else None,
        layer_role=str(value["layer_role"]),
        in_progress_source_bars=tuple(int(item) for item in value.get("in_progress_source_bars", [])),
        preflight_reasons=tuple(str(item) for item in value.get("preflight_reasons", [])),
        schema_version=int(value.get("schema_version", 1)),
    )


def instrument_bundle_from_dict(value: dict[str, object]) -> InstrumentRiskInputBundle:
    return InstrumentRiskInputBundle(
        instrument_id=str(value["instrument_id"]),
        asset_type=AssetType(str(value["asset_type"])),
        as_of=str(value["as_of"]),
        daily=risk_input_from_dict(dict(value["daily"])),
        risk_60m=risk_input_from_dict(dict(value["risk_60m"])),
        support_15m=risk_input_from_dict(dict(value["support_15m"])),
        data_status=RiskInputDataStatus(str(value["data_status"])),
        preflight_status=PreflightStatus(str(value["preflight_status"])),
        reasons=tuple(str(item) for item in value.get("reasons", [])),
        schema_version=int(value.get("schema_version", 1)),
    )
