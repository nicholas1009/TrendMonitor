"""Load and enforce the data-only Safe Feature Contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import (
    AssetType,
    FieldQuality,
    FieldQualityMap,
    MarketField,
    SystemBar,
    SystemBarTransformation,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FeatureUsage(StrEnum):
    EXACT_TRIGGER = "EXACT_TRIGGER"
    ADVISORY = "ADVISORY"


class RiskEngineReadiness(StrEnum):
    YES = "YES"
    YES_WITH_LIMITS = "YES_WITH_LIMITS"
    NO = "NO"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    feature: str
    layers: tuple[str, ...]
    asset_types: tuple[AssetType, ...]
    fields: tuple[MarketField, ...]
    usage: FeatureUsage


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    feature: str
    enabled: bool
    usage: FeatureUsage
    reason: str
    affected_fields: tuple[str, ...]
    quality_status: dict[str, str]
    source: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["usage"] = self.usage.value
        result["affected_fields"] = list(self.affected_fields)
        return result


@dataclass(frozen=True, slots=True)
class HardBlockContext:
    close_missing: bool = False
    timestamp_invalid: bool = False
    period_missing: bool = False
    duplicate_bars: bool = False
    lineage_missing: bool = False
    trading_day_unknown: bool = False
    bar_count_incomplete: bool = False

    def reasons(self) -> tuple[str, ...]:
        return tuple(key for key, value in asdict(self).items() if value)


@dataclass(frozen=True, slots=True)
class RuntimeOverride:
    instrument_id: str
    date: str
    fields: dict[str, FieldQuality]
    reason: str


@dataclass(frozen=True, slots=True)
class RiskInputAssessment:
    instrument_id: str
    period: str
    readiness: RiskEngineReadiness
    data_status: str
    hard_block_reasons: tuple[str, ...]
    quality_reasons: tuple[str, ...]
    field_quality: FieldQualityMap
    features: tuple[FeatureDecision, ...]
    source: str

    @property
    def feature_disabled(self) -> tuple[FeatureDecision, ...]:
        return tuple(item for item in self.features if not item.enabled)

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "period": self.period,
            "readiness": self.readiness.value,
            "data_status": self.data_status,
            "hard_block_reasons": list(self.hard_block_reasons),
            "quality_reasons": list(self.quality_reasons),
            "field_quality": self.field_quality.to_dict(),
            "features": [item.to_dict() for item in self.features],
            "feature_disabled": [item.to_dict() for item in self.feature_disabled],
            "source": self.source,
        }


class RiskFeatureContract:
    def __init__(
        self,
        *,
        version: int,
        formal_daily: dict[str, object],
        layers: dict[str, str],
        profiles: dict[tuple[AssetType, str], FieldQualityMap],
        features: tuple[FeatureSpec, ...],
        runtime_overrides: tuple[RuntimeOverride, ...],
    ) -> None:
        self.version = version
        self.formal_daily = formal_daily
        self.layers = layers
        self.profiles = profiles
        self.features = features
        self.runtime_overrides = runtime_overrides

    @classmethod
    def load(cls, path: str | Path) -> "RiskFeatureContract":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            formal_daily = raw["formal_daily"]
            if formal_daily["source_requirement"] != "DIRECT":
                raise ValueError("formal daily source must be DIRECT")
            if formal_daily["minute_derived_daily_allowed"] is not False:
                raise ValueError("minute-derived daily substitution must be prohibited")
            layers = {str(key): str(value) for key, value in raw["layers"].items()}
            if set(layers) != {"15m", "60m"}:
                raise ValueError("contract layers must be exactly 15m and 60m")
            profiles = {
                (AssetType(asset_type), period): FieldQualityMap.from_dict(fields)
                for asset_type, periods in raw["field_profiles"].items()
                for period, fields in periods.items()
            }
            features = tuple(
                FeatureSpec(
                    feature=str(item["feature"]),
                    layers=tuple(str(value) for value in item["layers"]),
                    asset_types=tuple(AssetType(value) for value in item["asset_types"]),
                    fields=tuple(MarketField(value) for value in item["fields"]),
                    usage=FeatureUsage(item["usage"]),
                )
                for item in raw["features"]
            )
            overrides = tuple(
                RuntimeOverride(
                    instrument_id=str(item["instrument_id"]),
                    date=str(item["date"]),
                    fields={key: FieldQuality(value) for key, value in item["fields"].items()},
                    reason=str(item["reason"]),
                )
                for item in raw.get("runtime_overrides", [])
            )
            return cls(
                version=int(raw["version"]),
                formal_daily=dict(formal_daily),
                layers=layers,
                profiles=profiles,
                features=features,
                runtime_overrides=overrides,
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TrendMonitorError(
                ErrorCategory.INVALID_DATA,
                f"invalid risk feature contract: {path}",
            ) from exc

    def profile(self, asset_type: AssetType, period: str) -> FieldQualityMap:
        try:
            return self.profiles[(asset_type, period)]
        except KeyError as exc:
            raise TrendMonitorError(
                ErrorCategory.UNSUPPORTED,
                f"no field profile for {asset_type.value}/{period}",
            ) from exc

    def overrides_for(self, instrument_id: str, day: str) -> tuple[RuntimeOverride, ...]:
        return tuple(
            item
            for item in self.runtime_overrides
            if item.instrument_id == instrument_id and item.date == day
        )


def _transformed(base: FieldQuality) -> FieldQuality:
    return (
        FieldQuality.TRUSTED_WITH_TRANSFORMATION
        if base is FieldQuality.TRUSTED
        else base
    )


def annotate_system_bar(
    bar: SystemBar,
    *,
    asset_type: AssetType,
    contract: RiskFeatureContract,
) -> tuple[SystemBar, tuple[str, ...]]:
    profile = contract.profile(asset_type, bar.period)
    values = profile.to_dict()
    reasons: list[str] = []
    if bar.transformation is SystemBarTransformation.MERGE_CLOSING_BUCKET:
        for field in ("close", "volume", "turnover"):
            values[field] = _transformed(FieldQuality(values[field])).value
        reasons.append("MERGE_CLOSING_BUCKET")
    if bar.transformation is SystemBarTransformation.SOURCE_BOUNDARY_ENVELOPE:
        for field in ("high", "low"):
            values[field] = _transformed(FieldQuality(values[field])).value
        reasons.append("SOURCE_BOUNDARY_ENVELOPE")
    day = datetime.fromtimestamp(bar.system_start / 1000, tz=timezone.utc).astimezone(
        SHANGHAI
    ).date().isoformat()
    for override in contract.overrides_for(bar.instrument_id, day):
        for field, status in override.fields.items():
            values[field] = status.value
        reasons.append(override.reason)
    return replace(bar, field_quality=FieldQualityMap.from_dict(values)), tuple(reasons)


def _eligible(quality: FieldQuality, usage: FeatureUsage) -> bool:
    if usage is FeatureUsage.EXACT_TRIGGER:
        return quality in {
            FieldQuality.TRUSTED,
            FieldQuality.TRUSTED_WITH_TRANSFORMATION,
        }
    return quality not in {FieldQuality.BLOCKED, FieldQuality.UNKNOWN}


def evaluate_risk_input(
    bar: SystemBar,
    *,
    asset_type: AssetType,
    contract: RiskFeatureContract,
    hard_blocks: HardBlockContext | None = None,
    quality_reasons: tuple[str, ...] = (),
) -> RiskInputAssessment:
    hard_reasons = (hard_blocks or HardBlockContext()).reasons()
    source = f"{bar.source_provider}:{bar.instrument_id}:{bar.period}"
    decisions: list[FeatureDecision] = []
    for spec in contract.features:
        if bar.period not in spec.layers or asset_type not in spec.asset_types:
            continue
        statuses = {field.value: bar.field_quality.get(field).value for field in spec.fields}
        ineligible = [
            field.value
            for field in spec.fields
            if not _eligible(bar.field_quality.get(field), spec.usage)
        ]
        enabled = not hard_reasons and not ineligible
        reason = (
            f"HARD_BLOCK:{','.join(hard_reasons)}"
            if hard_reasons
            else f"FIELD_QUALITY_DISABLED:{','.join(ineligible)}"
            if ineligible
            else "ELIGIBLE"
        )
        if ineligible and quality_reasons:
            reason = f"{reason};QUALITY_REASONS:{','.join(quality_reasons)}"
        decisions.append(
            FeatureDecision(
                feature=spec.feature,
                enabled=enabled,
                usage=spec.usage,
                reason=reason,
                affected_fields=tuple(field.value for field in spec.fields),
                quality_status=statuses,
                source=source,
            )
        )
    if hard_reasons:
        readiness = RiskEngineReadiness.NO
    elif not any(
        item.enabled and item.feature == "period_close_change" for item in decisions
    ):
        readiness = RiskEngineReadiness.NO
    elif all(item.enabled for item in decisions):
        readiness = RiskEngineReadiness.YES
    else:
        readiness = RiskEngineReadiness.YES_WITH_LIMITS
    return RiskInputAssessment(
        instrument_id=bar.instrument_id,
        period=bar.period,
        readiness=readiness,
        data_status=(
            ErrorCategory.DATA_INCOMPLETE.value
            if hard_reasons
            else "DEGRADED"
            if readiness is RiskEngineReadiness.YES_WITH_LIMITS
            else "VALID"
        ),
        hard_block_reasons=hard_reasons,
        quality_reasons=quality_reasons,
        field_quality=bar.field_quality,
        features=tuple(decisions),
        source=source,
    )
