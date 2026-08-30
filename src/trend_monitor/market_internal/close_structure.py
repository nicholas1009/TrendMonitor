"""Shared deterministic four-Close structure classification.

This module contains no market breadth or stock scoring logic.  It is the
single v0.1 implementation used by both the market and stock 15m auxiliary
layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas.market_internal import InternalClassification


@dataclass(frozen=True, slots=True)
class CloseStructure:
    classification: InternalClassification
    direction_sequence: tuple[str, ...]
    close_changes_pct: tuple[float, ...]
    repair_strength: float | None
    finish_position: float | None


def _direction(value: Decimal) -> str:
    return "↑" if value > 0 else "↓" if value < 0 else "→"


def classify_close_structure(
    closes: Sequence[float | Decimal],
    *,
    previous_close: float | Decimal,
    precedence: Sequence[str],
    healthy_direction_min: int = 3,
    completed: bool,
) -> CloseStructure:
    """Classify 1-4 completed 15m closes without High/Low/volume inputs."""
    values = tuple(Decimal(str(item)) for item in closes)
    previous = Decimal(str(previous_close))
    expected = 4 if completed else None
    if previous <= 0 or not values or len(values) > 4 or (expected and len(values) != expected):
        raise TrendMonitorError(ErrorCategory.INVALID_DATA, "invalid four-Close structure input")
    chain = (previous, *values)
    changes = tuple(right / left - Decimal(1) for left, right in zip(chain, chain[1:]))
    directions = tuple(_direction(item) for item in changes)

    if not completed:
        if len(values) > 3:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "in-progress structure accepts one to three closes")
        up = sum(item > 0 for item in changes)
        down = sum(item < 0 for item in changes)
        classification = (
            InternalClassification.EARLY_STRENGTH
            if up > down
            else InternalClassification.EARLY_WEAKNESS
            if down > up
            else InternalClassification.EARLY_MIXED
        )
    else:
        c1, c2, c3, c4 = values
        r1, r2, r3, r4 = changes
        up = sum(item > 0 for item in changes)
        down = sum(item < 0 for item in changes)
        first_half_cumulative = c2 / previous - Decimal(1)
        conditions = {
            "HEALTHY_UP": up >= healthy_direction_min and r4 >= 0,
            "HEALTHY_DOWN": down >= healthy_direction_min and r4 <= 0,
            "LATE_REPAIR": (
                (r1 < 0 or r2 < 0)
                and first_half_cumulative < 0
                and r3 > 0
                and r4 > 0
                and c4 > c2
            ),
            "FAILED_REPAIR": (
                any(changes[index] > 0 and any(item < 0 for item in changes[:index]) for index in (1, 2))
                and r4 < 0
                and c4 < c3
            ),
            "LATE_WEAKENING": first_half_cumulative >= 0 and r3 < 0 and r4 < 0 and c4 < c2,
            "MIXED": True,
        }
        classification = next(
            (InternalClassification(str(name)) for name in precedence if conditions.get(str(name), False)),
            None,
        )
        if classification is None:
            raise TrendMonitorError(ErrorCategory.INVALID_DATA, "classification precedence has no MIXED fallback")

    close_low, close_high = min(values), max(values)
    position = None if close_high == close_low else float((values[-1] - close_low) / (close_high - close_low))
    return CloseStructure(
        classification=classification,
        direction_sequence=directions,
        close_changes_pct=tuple(float(item) for item in changes),
        repair_strength=position,
        finish_position=position,
    )
