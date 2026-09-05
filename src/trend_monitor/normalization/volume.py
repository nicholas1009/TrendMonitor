"""Explicit cross-provider volume-unit normalization contracts."""

from __future__ import annotations

from dataclasses import dataclass


CANONICAL_VOLUME_UNIT = "shares"
LONGBRIDGE_CN_VOLUME_SCALE = 100


@dataclass(frozen=True, slots=True)
class VolumeInvariantResult:
    volume_raw: float
    turnover_raw: float
    low: float
    high: float
    implied_price_factor_1: float
    factor_1_valid: bool
    implied_price_factor_100: float
    factor_100_valid: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "volume_raw": self.volume_raw,
            "turnover_raw": self.turnover_raw,
            "low": self.low,
            "high": self.high,
            "implied_price_factor_1": self.implied_price_factor_1,
            "factor_1_valid": self.factor_1_valid,
            "implied_price_factor_100": self.implied_price_factor_100,
            "factor_100_valid": self.factor_100_valid,
        }


def evaluate_cn_volume_invariant(
    *, volume_raw: float, turnover_raw: float, low: float, high: float
) -> VolumeInvariantResult:
    if volume_raw <= 0 or turnover_raw < 0 or low <= 0 or high < low:
        raise ValueError("invalid Daily values for volume dimensional invariant")
    factor_1 = turnover_raw / volume_raw
    factor_100 = turnover_raw / (volume_raw * LONGBRIDGE_CN_VOLUME_SCALE)
    return VolumeInvariantResult(
        volume_raw=volume_raw,
        turnover_raw=turnover_raw,
        low=low,
        high=high,
        implied_price_factor_1=factor_1,
        factor_1_valid=low <= factor_1 <= high,
        implied_price_factor_100=factor_100,
        factor_100_valid=low <= factor_100 <= high,
    )


def normalize_volume_shares(
    value: float | int | None,
    *,
    provider: str,
    data_type: str,
    raw_unit: str | None = None,
    market: str = "CN",
) -> float | None:
    """Normalize only confirmed contracts; unknown units never auto-convert."""

    if value is None:
        return None
    numeric = float(value)
    unit = raw_unit.lower() if isinstance(raw_unit, str) else None
    provider_name = provider.lower()
    data_kind = data_type.lower()
    if unit in {"share", "shares"}:
        return numeric
    if unit in {"hand", "hands", "lot", "lots"}:
        return numeric * 100
    if unit is not None:
        return None
    if provider_name == "longbridge" and market.upper() == "CN":
        return numeric * LONGBRIDGE_CN_VOLUME_SCALE
    if provider_name == "hithink" and data_kind in {"daily", "quote"}:
        return numeric
    if provider_name == "hithink" and data_kind == "auction":
        return numeric * 100
    return None
