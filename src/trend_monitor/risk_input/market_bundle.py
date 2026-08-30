"""Deterministic readiness grouping for the formal eight-index market bundle."""

from __future__ import annotations

from collections.abc import Mapping

from trend_monitor.registry import (
    InstrumentRegistry,
    MappingConfidence,
    MappingStatus,
    MappingType,
)
from trend_monitor.schemas import (
    GroupEntry,
    InstrumentRiskInputBundle,
    PreflightStatus,
    RiskInputGroup,
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
CONSUMABLE_STATUSES = frozenset({"READY", "DEGRADED"})


def build_market_risk_group(
    *,
    as_of: str,
    registry: InstrumentRegistry,
    bundles: Mapping[str, InstrumentRiskInputBundle],
    snapshot_paths: Mapping[str, str],
    provider: str = "longbridge",
) -> RiskInputGroup:
    """Build a group without treating guessed/unverified mappings as ready."""
    entries = []
    for instrument_id in MARKET_INDEXES:
        mapping = registry.resolve(instrument_id, provider)
        verified = (
            mapping.provider_symbol is not None
            and mapping.mapping_type is MappingType.EXACT
            and mapping.confidence is MappingConfidence.HIGH
            and mapping.status is MappingStatus.VERIFIED
        )
        if not verified:
            entries.append(
                GroupEntry(instrument_id, "UNAVAILABLE", "MAPPING_NOT_VERIFIED", None)
            )
            continue
        bundle = bundles.get(instrument_id)
        path = snapshot_paths.get(instrument_id)
        if bundle is None or path is None:
            entries.append(
                GroupEntry(instrument_id, "UNAVAILABLE", "RISK_INPUT_NOT_ASSEMBLED", None)
            )
        elif bundle.preflight_status is PreflightStatus.BLOCKED:
            entries.append(GroupEntry(instrument_id, "BLOCKED", "PREFLIGHT_BLOCKED", path))
        elif bundle.preflight_status is PreflightStatus.PASS_WITH_DEGRADATION:
            entries.append(GroupEntry(instrument_id, "DEGRADED", None, path))
        else:
            entries.append(GroupEntry(instrument_id, "READY", None, path))
    return RiskInputGroup("market_risk_input", as_of, tuple(entries))


def market_coverage_status(group: RiskInputGroup) -> str:
    consumable = sum(item.status in CONSUMABLE_STATUSES for item in group.entries)
    if consumable == len(MARKET_INDEXES):
        return "FULL_READY"
    if consumable:
        return "PARTIAL_READY"
    return "NO"
