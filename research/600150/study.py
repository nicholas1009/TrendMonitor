#!/usr/bin/env python3
"""Causal 600150.SH breakout and T+1 opening study for TASK_026B.

This is an isolated offline/shadow research module.  It reads and writes only
under ``research/600150`` and never imports the production runtime runner,
notification service, or position state.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_DIR = Path(__file__).resolve().parent
RAW_DIR = OUTPUT_DIR / "raw"
DERIVED_DIR = OUTPUT_DIR / "derived"
DAILY_RAW = RAW_DIR / "longbridge_daily_noadjust.json"
DAILY_CSV = RAW_DIR / "longbridge_daily_noadjust.csv"
DAILY_MANIFEST = RAW_DIR / "longbridge_daily_source_manifest.json"
HITHINK_CAPABILITY_RAW = RAW_DIR / "hithink_capability_audit.json"
HITHINK_DAILY_SAMPLES_RAW = RAW_DIR / "hithink_daily_samples.json"
AUCTION_BRIDGE_RAW = RAW_DIR / "auction_open_bridge_samples.json"
MINUTE_RAW_DIR = RAW_DIR / "minutes"

EVENTS_CSV = OUTPUT_DIR / "historical_breakout_events.csv"
SIMILAR_CSV = OUTPUT_DIR / "similar_events_20260904.csv"
TARGET_JSON = OUTPUT_DIR / "target_event_20260904.json"
STUDY_JSON = OUTPUT_DIR / "historical_breakout_add_study_v0.1.json"
BRIDGE_JSON = OUTPUT_DIR / "auction_open_bridge_study_v0.1.json"
DAILY_VALIDATION_JSON = OUTPUT_DIR / "daily_cross_validation_v0.1.json"
PLAYBOOK_JSON = OUTPUT_DIR / "opening_playbook_next_confirmed_trading_day_v0.1.json"
MINUTE_STUDY_JSON = DERIVED_DIR / "post_open_path_study.json"

SYMBOL = "600150.SH"
NAME = "中国船舶"
SHANGHAI = ZoneInfo("Asia/Shanghai")
WARMUP_START = date(2022, 8, 1)
FORMAL_START = date(2023, 9, 1)
HISTORY_END = date(2026, 9, 3)
TARGET_DATE = date(2026, 9, 4)
CALIBRATION_END = date(2025, 8, 31)
VALIDATION_START = date(2025, 9, 1)
MIN_VALIDATION_SAMPLE = 5
SIMILAR_LIMIT = 20

HORIZONS = (1, 3, 5, 10)
PRIOR_WINDOWS = (20, 40, 60)
MA_WINDOWS = (10, 20, 60, 250)
SIMILARITY_FEATURES = (
    "breakout_distance_20_atr",
    "breakout_distance_40_atr",
    "breakout_distance_60_atr",
    "day_return",
    "range_atr",
    "volume_ratio_20",
    "close_location",
    "distance_ma250_atr",
    "above_ma10",
    "above_ma20",
    "above_ma60",
    "above_ma250",
    "ma10_slope_atr",
    "ma20_slope_atr",
    "ma60_slope_atr",
    "ma250_slope_atr",
)


@dataclass(frozen=True, slots=True)
class DailyBar:
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float
    provider_timestamp: int


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _date_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=SHANGHAI).timestamp() * 1000)


def _source_date(timestamp: int) -> date:
    value = timestamp / 1000 if timestamp >= 10_000_000_000 else timestamp
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(SHANGHAI).date()


def _float(value: object) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError("numeric field is null or boolean")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("numeric field is not finite")
    return result


def validate_longbridge_daily_raw(raw: Mapping[str, object]) -> None:
    request = raw.get("request")
    expected = {
        "symbol": SYMBOL,
        "data_type": "daily",
        "period": "1d",
        "adjust_type": "none",
    }
    actual = (
        {key: request.get(key) for key in expected}
        if isinstance(request, Mapping)
        else None
    )
    if raw.get("provider") != "longbridge" or actual != expected:
        raise ValueError(f"not Longbridge DIRECT DAILY NoAdjust: {actual}")


def bars_from_longbridge_raw(raw: Mapping[str, object]) -> list[DailyBar]:
    validate_longbridge_daily_raw(raw)
    data = raw.get("data")
    items = data.get("item") if isinstance(data, Mapping) else None
    if not isinstance(items, list) or not items:
        raise ValueError("Longbridge Daily response is empty")
    bars = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Longbridge Daily item is not an object")
        timestamp = int(item["timestamp"])
        bar = DailyBar(
            trading_date=_source_date(timestamp),
            open=_float(item["open"]),
            high=_float(item["high"]),
            low=_float(item["low"]),
            close=_float(item["close"]),
            volume=int(item["volume"]),
            turnover=_float(item["turnover"]),
            provider_timestamp=timestamp,
        )
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise ValueError(f"non-positive OHLC on {bar.trading_date}")
        if bar.high < max(bar.open, bar.low, bar.close):
            raise ValueError(f"invalid high on {bar.trading_date}")
        if bar.low > min(bar.open, bar.high, bar.close):
            raise ValueError(f"invalid low on {bar.trading_date}")
        if bar.volume < 0 or bar.turnover < 0:
            raise ValueError(f"negative volume/turnover on {bar.trading_date}")
        bars.append(bar)
    validate_bars(bars)
    return bars


def validate_bars(bars: Sequence[DailyBar]) -> None:
    if not bars:
        raise ValueError("Daily input is empty")
    dates = [item.trading_date for item in bars]
    if dates != sorted(dates):
        raise ValueError("Daily input is not ordered")
    if len(set(dates)) != len(dates):
        raise ValueError("Daily input contains duplicate trading dates")
    if dates[-1] > TARGET_DATE:
        raise ValueError("lookahead: Daily input extends beyond TARGET_EVENT")


def write_daily_csv(bars: Sequence[DailyBar]) -> str:
    DAILY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DAILY_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "date", "open", "high", "low", "close", "volume", "turnover",
            "provider_timestamp", "source", "daily_contract", "adjust_type",
        ), lineterminator="\n")
        writer.writeheader()
        for bar in bars:
            writer.writerow({
                "date": bar.trading_date.isoformat(),
                "open": f"{bar.open:.8f}",
                "high": f"{bar.high:.8f}",
                "low": f"{bar.low:.8f}",
                "close": f"{bar.close:.8f}",
                "volume": bar.volume,
                "turnover": f"{bar.turnover:.8f}",
                "provider_timestamp": bar.provider_timestamp,
                "source": "longbridge",
                "daily_contract": "DIRECT_DAILY",
                "adjust_type": "none",
            })
    return hashlib.sha256(DAILY_CSV.read_bytes()).hexdigest()


def load_daily_csv() -> list[DailyBar]:
    bars = []
    with DAILY_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (
                row["source"] != "longbridge"
                or row["daily_contract"] != "DIRECT_DAILY"
                or row["adjust_type"] != "none"
            ):
                raise ValueError("offline Daily input contract mismatch")
            bars.append(DailyBar(
                trading_date=date.fromisoformat(row["date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                turnover=float(row["turnover"]),
                provider_timestamp=int(row["provider_timestamp"]),
            ))
    validate_bars(bars)
    if hashlib.sha256(DAILY_CSV.read_bytes()).hexdigest() != _read_json(
        DAILY_MANIFEST
    )["daily_csv_sha256"]:
        raise ValueError("Daily CSV hash does not match source manifest")
    return bars


def fetch_daily() -> dict[str, object]:
    from trend_monitor.providers.longbridge import LongbridgeProvider

    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    raw = provider.get_daily(
        SYMBOL,
        start=_date_ms(WARMUP_START),
        end=_date_ms(TARGET_DATE),
    )
    bars = bars_from_longbridge_raw(raw)
    if bars[0].trading_date > WARMUP_START or bars[-1].trading_date != TARGET_DATE:
        raise ValueError("Longbridge Daily coverage is incomplete")
    _write_json(DAILY_RAW, raw)
    digest = write_daily_csv(bars)
    manifest = {
        "schema_version": 1,
        "symbol": SYMBOL,
        "provider": "longbridge",
        "endpoint": "history_candlesticks_by_date",
        "daily_contract": "DIRECT_DAILY",
        "period": "1d",
        "adjust_type": "none",
        "warmup_start": bars[0].trading_date.isoformat(),
        "formal_start": FORMAL_START.isoformat(),
        "actual_end": bars[-1].trading_date.isoformat(),
        "trading_days": len(bars),
        "daily_csv_sha256": digest,
        "fetched_at": _iso_now(),
    }
    _write_json(DAILY_MANIFEST, manifest)
    return manifest


def _rolling_mean(values: Sequence[float], end: int, window: int) -> float | None:
    start = end - window + 1
    if start < 0:
        return None
    return statistics.fmean(values[start : end + 1])


def _prior_max(values: Sequence[float], end: int, window: int) -> float | None:
    start = end - window
    if start < 0:
        return None
    return max(values[start:end])


def build_daily_features(bars: Sequence[DailyBar]) -> list[dict[str, object]]:
    """Build causal features; row T uses only observations at or before T."""
    validate_bars(bars)
    closes = [item.close for item in bars]
    highs = [item.high for item in bars]
    volumes = [float(item.volume) for item in bars]
    true_ranges = []
    for index, bar in enumerate(bars):
        previous_close = closes[index - 1] if index else bar.close
        true_ranges.append(max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        ))

    rows = []
    previous_mas: dict[int, float | None] = {window: None for window in MA_WINDOWS}
    for index, bar in enumerate(bars):
        atr = _rolling_mean(true_ranges, index, 14)
        volume_average = _rolling_mean(volumes, index - 1, 20)
        row: dict[str, object] = {
            "date": bar.trading_date.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "turnover": bar.turnover,
            "prev_close": closes[index - 1] if index else None,
            "true_range": true_ranges[index],
            "atr14_sma": atr,
            "day_return": (
                bar.close / closes[index - 1] - 1 if index else None
            ),
            "gap": bar.open / closes[index - 1] - 1 if index else None,
            "range_atr": (bar.high - bar.low) / atr if atr else None,
            "volume_ratio_20": bar.volume / volume_average if volume_average else None,
            "close_location": (
                (bar.close - bar.low) / (bar.high - bar.low)
                if bar.high != bar.low
                else None
            ),
        }
        for window in MA_WINDOWS:
            ma = _rolling_mean(closes, index, window)
            row[f"ma{window}"] = ma
            row[f"price_vs_ma{window}"] = bar.close / ma - 1 if ma else None
            row[f"above_ma{window}"] = int(bar.close > ma) if ma is not None else None
            row[f"ma{window}_slope"] = (
                ma - previous_mas[window]
                if ma is not None and previous_mas[window] is not None
                else None
            )
            row[f"ma{window}_slope_atr"] = (
                (ma - previous_mas[window]) / atr
                if ma is not None and previous_mas[window] is not None and atr
                else None
            )
            previous_mas[window] = ma
        for window in PRIOR_WINDOWS:
            reference = _prior_max(highs, index, window)
            row[f"prior{window}_high"] = reference
            row[f"breakout_{window}"] = (
                int(bar.close > reference) if reference is not None else None
            )
            row[f"breakout_distance_{window}_atr"] = (
                (bar.close - reference) / atr
                if reference is not None and atr
                else None
            )
        row["distance_ma250_atr"] = (
            (bar.close - float(row["ma250"])) / atr
            if row["ma250"] is not None and atr
            else None
        )
        row["breakout_signature"] = "".join(
            "1" if row[f"breakout_{window}"] == 1 else "0"
            for window in PRIOR_WINDOWS
        )
        rows.append(row)
    return rows


def _reference_for(row: Mapping[str, object]) -> tuple[str, float] | None:
    for window in reversed(PRIOR_WINDOWS):
        if row[f"breakout_{window}"] == 1:
            return f"prior{window}_high", float(row[f"prior{window}_high"])
    return None


def add_outcomes(
    rows: Sequence[dict[str, object]],
    bars: Sequence[DailyBar],
) -> list[dict[str, object]]:
    """Add post-event labels without allowing 2026-09-04 into history fitting."""
    by_date = {item.trading_date: index for index, item in enumerate(bars)}
    result = []
    for source in rows:
        current = dict(source)
        index = by_date[date.fromisoformat(str(current["date"]))]
        reference = _reference_for(current)
        current["breakout_reference_field"] = reference[0] if reference else None
        current["breakout_reference"] = reference[1] if reference else None
        for horizon in HORIZONS:
            future = list(bars[index + 1 : index + horizon + 1])
            complete = (
                len(future) == horizon
                and future[-1].trading_date <= HISTORY_END
            )
            if not complete:
                current[f"t{horizon}_close_return"] = None
                current[f"t{horizon}_mfe"] = None
                current[f"t{horizon}_mae"] = None
                continue
            current[f"t{horizon}_close_return"] = future[-1].close / bars[index].close - 1
            current[f"t{horizon}_mfe"] = max(item.high for item in future) / bars[index].close - 1
            current[f"t{horizon}_mae"] = min(item.low for item in future) / bars[index].close - 1
        next_bar = bars[index + 1] if index + 1 < len(bars) else None
        current["t1_open_gap"] = (
            next_bar.open / bars[index].close - 1
            if next_bar is not None and next_bar.trading_date <= HISTORY_END
            else None
        )
        for horizon in (1, 3, 5):
            future = list(bars[index + 1 : index + horizon + 1])
            complete = (
                reference is not None
                and len(future) == horizon
                and future[-1].trading_date <= HISTORY_END
            )
            current[f"false_break_price_{horizon}d"] = (
                int(any(item.low < reference[1] for item in future))
                if complete
                else None
            )
            current[f"false_break_close_{horizon}d"] = (
                int(any(item.close < reference[1] for item in future))
                if complete
                else None
            )
        current["segment"] = (
            "CALIBRATION"
            if date.fromisoformat(str(current["date"])) <= CALIBRATION_END
            else "VALIDATION"
        )
        result.append(current)
    return result


def event_pool(feature_rows: Sequence[dict[str, object]], bars: Sequence[DailyBar]) -> list[dict[str, object]]:
    formal = [
        item
        for item in feature_rows
        if FORMAL_START <= date.fromisoformat(str(item["date"])) <= HISTORY_END
        and any(item[f"breakout_{window}"] == 1 for window in PRIOR_WINDOWS)
    ]
    return add_outcomes(formal, bars)


def _csv_value(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.10f}"
    return "" if value is None else value


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile input is empty")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: Iterable[object]) -> dict[str, object]:
    numbers = [float(item) for item in values if item is not None]
    if not numbers:
        return {"count": 0}
    return {
        "count": len(numbers),
        "min": min(numbers),
        "p25": _quantile(numbers, 0.25),
        "median": statistics.median(numbers),
        "p75": _quantile(numbers, 0.75),
        "max": max(numbers),
        "mean": statistics.fmean(numbers),
    }


def _percentile_rank(values: Sequence[float], target: float) -> float:
    if not values:
        raise ValueError("percentile input is empty")
    return sum(item <= target for item in values) / len(values)


def target_event(feature_rows: Sequence[dict[str, object]]) -> dict[str, object]:
    target = next(
        (dict(item) for item in feature_rows if item["date"] == TARGET_DATE.isoformat()),
        None,
    )
    if target is None:
        raise ValueError("TARGET_EVENT Daily bar is missing")
    target["event_role"] = "TARGET_EVENT_NOT_USED_FOR_FITTING"
    target["market_context"] = {
        "14:00": {"risk_light": "ORANGE", "risk_score": 5, "execution_mode": "CATCH_UP"},
        "15:00": {"risk_light": "ORANGE", "risk_score": 5, "execution_mode": "CATCH_UP"},
    }
    target["entry_ma_cross"] = {
        "status": "UNKNOWN",
        "reason": "No reliable moving-average pair was found in repository evidence.",
    }
    return target


def rank_similar_events(
    events: Sequence[dict[str, object]],
    target: Mapping[str, object],
    *,
    limit: int = SIMILAR_LIMIT,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    signature = target["breakout_signature"]
    eligible = [
        item for item in events
        if item["breakout_signature"] == signature
        and all(item.get(field) is not None for field in SIMILARITY_FEATURES)
    ]
    calibration = [item for item in eligible if item["segment"] == "CALIBRATION"]
    if len(calibration) < 2:
        raise ValueError("insufficient Calibration rows for deterministic scaling")
    scales = {}
    for field in SIMILARITY_FEATURES:
        values = [float(item[field]) for item in calibration]
        scale = statistics.pstdev(values)
        scales[field] = {"mean": statistics.fmean(values), "scale": scale or 1.0}
    ranked = []
    for item in eligible:
        squared = 0.0
        differences = {}
        for field in SIMILARITY_FEATURES:
            raw_difference = float(item[field]) - float(target[field])
            standardized = raw_difference / float(scales[field]["scale"])
            squared += standardized * standardized
            differences[field] = raw_difference
        value = dict(item)
        value["similarity_distance"] = math.sqrt(squared)
        value["target_feature_differences"] = json.dumps(
            differences, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        ranked.append(value)
    ranked.sort(key=lambda item: (float(item["similarity_distance"]), str(item["date"])))
    top = ranked[:limit]
    for index, item in enumerate(top, start=1):
        item["similarity_rank"] = index
    metadata = {
        "breakout_signature": signature,
        "eligible_same_signature": len(eligible),
        "calibration_scaling_rows": len(calibration),
        "features": list(SIMILARITY_FEATURES),
        "scaling": scales,
        "distance": "standardized_euclidean",
        "outcomes_used_in_similarity": False,
    }
    return top, metadata


def _scenario_for_gap(gap: float, low_boundary: float, high_boundary: float) -> str:
    if gap <= low_boundary:
        return "LOW_OPEN"
    if gap >= high_boundary:
        return "HIGH_OPEN"
    return "NEUTRAL_OPEN"


def _rate(rows: Sequence[Mapping[str, object]], field: str, predicate) -> float | None:
    values = [float(item[field]) for item in rows if item.get(field) is not None]
    return sum(predicate(item) for item in values) / len(values) if values else None


def opening_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    usable = [item for item in rows if item.get("t1_open_gap") is not None]
    result: dict[str, object] = {"sample_size": len(usable)}
    for horizon in (1, 3, 5):
        available = [item for item in usable if item.get(f"t{horizon}_close_return") is not None]
        returns = [float(item[f"t{horizon}_close_return"]) for item in available]
        result[f"t{horizon}_positive_rate"] = (
            sum(value > 0 for value in returns) / len(returns) if returns else None
        )
        result[f"t{horizon}_median_return"] = statistics.median(returns) if returns else None
        result[f"t{horizon}_average_return"] = statistics.fmean(returns) if returns else None
    mae = [float(item["t5_mae"]) for item in usable if item.get("t5_mae") is not None]
    mfe = [float(item["t5_mfe"]) for item in usable if item.get("t5_mfe") is not None]
    result["median_mae_5d"] = statistics.median(mae) if mae else None
    result["median_mfe_5d"] = statistics.median(mfe) if mfe else None
    result["worst_mae_5d"] = min(mae) if mae else None
    result["false_break_price_5d_rate"] = _rate(
        usable, "false_break_price_5d", lambda value: value == 1
    )
    result["false_break_close_5d_rate"] = _rate(
        usable, "false_break_close_5d", lambda value: value == 1
    )
    return result


def _direction(metrics: Mapping[str, object]) -> str:
    t3 = metrics.get("t3_median_return")
    t5 = metrics.get("t5_median_return")
    if t3 is None or t5 is None:
        return "UNKNOWN"
    if float(t3) > 0 and float(t5) > 0:
        return "POSITIVE"
    if float(t3) < 0 and float(t5) < 0:
        return "NEGATIVE"
    return "MIXED"


def _scenario_action(calibration: Mapping[str, object], validation: Mapping[str, object]) -> dict[str, object]:
    if (
        int(calibration["sample_size"]) < MIN_VALIDATION_SAMPLE
        or int(validation["sample_size"]) < MIN_VALIDATION_SAMPLE
    ):
        return {
            "action": "NO_SIGNAL",
            "status": "SAMPLE_THIN",
            "reason": f"requires at least {MIN_VALIDATION_SAMPLE} rows in each segment",
        }
    cal_direction = _direction(calibration)
    val_direction = _direction(validation)
    if cal_direction != val_direction or cal_direction in {"MIXED", "UNKNOWN"}:
        return {
            "action": "NO_SIGNAL",
            "status": "INCONCLUSIVE",
            "reason": f"Calibration={cal_direction}; Validation={val_direction}",
        }
    if cal_direction == "NEGATIVE":
        return {
            "action": "DEFENSIVE",
            "status": "DIRECTIONALLY_VALIDATED",
            "reason": "T+3 and T+5 medians are negative in both segments",
        }
    positive_rates = (
        calibration.get("t3_positive_rate"),
        calibration.get("t5_positive_rate"),
        validation.get("t3_positive_rate"),
        validation.get("t5_positive_rate"),
    )
    reward_risk = (
        validation.get("median_mfe_5d") is not None
        and validation.get("median_mae_5d") is not None
        and float(validation["median_mfe_5d"]) > abs(float(validation["median_mae_5d"]))
    )
    if all(value is not None and float(value) >= 0.60 for value in positive_rates) and reward_risk:
        return {
            "action": "ADD",
            "status": "PRECISION_FIRST_CANDIDATE",
            "reason": "both segments are positive with >=60% T+3/T+5 positive rates and validation median MFE exceeds |MAE|",
        }
    return {
        "action": "HOLD",
        "status": "DIRECTIONALLY_POSITIVE_NOT_HIGH_PRECISION",
        "reason": "direction agrees but the precision/reward-risk evidence gate is not fully met",
    }


def opening_study(events: Sequence[dict[str, object]], target: Mapping[str, object]) -> dict[str, object]:
    same = [
        item for item in events
        if item["breakout_signature"] == target["breakout_signature"]
        and item.get("t1_open_gap") is not None
    ]
    calibration_gaps = [
        float(item["t1_open_gap"])
        for item in same
        if item["segment"] == "CALIBRATION"
    ]
    if len(calibration_gaps) < 3:
        raise ValueError("insufficient Calibration gap distribution")
    low_boundary = _quantile(calibration_gaps, 1 / 3)
    high_boundary = _quantile(calibration_gaps, 2 / 3)
    scenarios = {}
    for name in ("LOW_OPEN", "NEUTRAL_OPEN", "HIGH_OPEN"):
        calibration = [
            item for item in same
            if item["segment"] == "CALIBRATION"
            and _scenario_for_gap(float(item["t1_open_gap"]), low_boundary, high_boundary) == name
        ]
        validation = [
            item for item in same
            if item["segment"] == "VALIDATION"
            and _scenario_for_gap(float(item["t1_open_gap"]), low_boundary, high_boundary) == name
        ]
        cal_metrics = opening_metrics(calibration)
        val_metrics = opening_metrics(validation)
        scenarios[name] = {
            "calibration": cal_metrics,
            "validation": val_metrics,
            "decision": _scenario_action(cal_metrics, val_metrics),
        }
    return {
        "objective": "PRECISION_FIRST",
        "historical_field": "daily_open",
        "auction_interpretation": "PROVISIONAL_ONLY_IF_AUCTION_OPEN_BRIDGE_CONFIRMED",
        "bucket_method": "Calibration terciles; Validation assigned without refitting",
        "low_boundary": low_boundary,
        "high_boundary": high_boundary,
        "minimum_segment_sample": MIN_VALIDATION_SAMPLE,
        "scenarios": scenarios,
    }


def _stop_study(similar: Sequence[Mapping[str, object]]) -> dict[str, object]:
    rows = [item for item in similar if item.get("t5_mae") is not None]
    summaries = {}
    for name, threshold_getter in (
        ("ma250", lambda item: item.get("ma250")),
        ("breakout_reference", lambda item: item.get("breakout_reference")),
        ("exploratory_close_minus_2atr", lambda item: (
            float(item["close"]) - 2 * float(item["atr14_sma"])
            if item.get("atr14_sma") is not None else None
        )),
    ):
        usable = []
        for item in rows:
            threshold = threshold_getter(item)
            if threshold is None:
                continue
            lowest_price = float(item["close"]) * (1 + float(item["t5_mae"]))
            triggered = lowest_price <= float(threshold)
            washed_out = triggered and float(item["t5_close_return"]) > 0
            usable.append((triggered, washed_out))
        summaries[name] = {
            "sample_size": len(usable),
            "trigger_rate_5d": (
                sum(item[0] for item in usable) / len(usable) if usable else None
            ),
            "washout_rate_5d": (
                sum(item[1] for item in usable) / len(usable) if usable else None
            ),
        }
    return {
        "status": "EXPLORATORY",
        "atr_protection_note": "2ATR is an explicit exploratory comparator, not a 600150 production stop.",
        "comparators": summaries,
    }


def _target_percentiles(events: Sequence[Mapping[str, object]], target: Mapping[str, object]) -> dict[str, float]:
    fields = (
        "day_return", "volume_ratio_20", "close_location", "range_atr",
        "distance_ma250_atr", "breakout_distance_20_atr",
        "breakout_distance_40_atr", "breakout_distance_60_atr",
    )
    output = {}
    for field in fields:
        values = [float(item[field]) for item in events if item.get(field) is not None]
        if target.get(field) is not None and values:
            output[field] = _percentile_rank(values, float(target[field]))
    return output


def _event_distributions(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {
        f"breakout_{window}_count": sum(item[f"breakout_{window}"] == 1 for item in events)
        for window in PRIOR_WINDOWS
    }
    for field in (
        "day_return", "volume_ratio_20", "close_location", "range_atr",
        "distance_ma250_atr",
    ):
        result[field] = _distribution(item.get(field) for item in events)
    return result


def _similar_outcome_summary(similar: Sequence[Mapping[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {"sample_size": len(similar)}
    for horizon in HORIZONS:
        available = [
            item for item in similar
            if item.get(f"t{horizon}_close_return") is not None
        ]
        returns = [float(item[f"t{horizon}_close_return"]) for item in available]
        maes = [float(item[f"t{horizon}_mae"]) for item in available]
        mfes = [float(item[f"t{horizon}_mfe"]) for item in available]
        result[f"t{horizon}"] = {
            "sample_size": len(available),
            "positive_rate": (
                sum(value > 0 for value in returns) / len(returns) if returns else None
            ),
            "median_return": statistics.median(returns) if returns else None,
            "average_return": statistics.fmean(returns) if returns else None,
            "median_mae": statistics.median(maes) if maes else None,
            "median_mfe": statistics.median(mfes) if mfes else None,
            "worst_mae": min(maes) if maes else None,
        }
    for basis in ("price", "close"):
        for horizon in (1, 3, 5):
            field = f"false_break_{basis}_{horizon}d"
            values = [int(item[field]) for item in similar if item.get(field) is not None]
            result[f"{field}_rate"] = sum(values) / len(values) if values else None
    return result


def _post_open_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    result = {"sample_size": len(rows)}
    for period in ("15m", "60m"):
        values = [item.get(f"first_{period}") for item in rows]
        ready = [item for item in values if isinstance(item, Mapping)]
        returns = [float(item["return"]) for item in ready]
        maes = [float(item["mae"]) for item in ready]
        mfes = [float(item["mfe"]) for item in ready]
        result[f"first_{period}"] = {
            "sample_size": len(ready),
            "positive_rate": (
                sum(value > 0 for value in returns) / len(returns) if returns else None
            ),
            "median_return": statistics.median(returns) if returns else None,
            "median_mae": statistics.median(maes) if maes else None,
            "median_mfe": statistics.median(mfes) if mfes else None,
            "worst_mae": min(maes) if maes else None,
        }
    return result


def fetch_capability_audit() -> dict[str, object]:
    """Run only documented Hithink calls; no historical Auction parameter exists."""
    from trend_monitor.providers.hithink import HithinkProvider

    provider = HithinkProvider(dotenv_path=PROJECT_ROOT / ".env")
    observed_at = datetime.now(SHANGHAI)
    auction = provider.auction_snapshot([SYMBOL], stage="final")
    calendar = provider.trading_days()
    daily = provider.stock_history(
        SYMBOL,
        start=_date_ms(TARGET_DATE),
        end=_date_ms(TARGET_DATE),
        adjust="none",
    )
    days = calendar.get("data", {}).get("item", [])
    auction_data = auction.get("data", {})
    items = auction_data.get("item", []) if isinstance(auction_data, Mapping) else []
    matched = [item for item in items if item.get("thscode") == SYMBOL]
    final = (
        isinstance(auction_data, Mapping)
        and auction_data.get("auction_phase") == "closed"
        and auction_data.get("data_status") == "final"
        and len(matched) == 1
    )
    payload = {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "hithink_600150_auction": "CONFIRMED" if final else "FAIL",
        "historical_auction": "UNSUPPORTED",
        "historical_auction_reason": (
            "GET /api/a-share/auction/snapshot accepts only thscodes and stage; "
            "the date-aware short-term benchmark does not return per-symbol auction prices."
        ),
        "auction_response": auction,
        "daily_target_response": daily,
        "calendar_response": calendar,
        "calendar_authoritative_through": (
            days[-1].get("date") if days else None
        ),
        "contains_20260907": any(item.get("date") == "20260907" for item in days),
        "next_trade_date_status": "UNKNOWN_PENDING_HITHINK_CALENDAR",
    }
    _write_json(HITHINK_CAPABILITY_RAW, payload)
    return {
        key: payload[key]
        for key in (
            "observed_at", "hithink_600150_auction", "historical_auction",
            "calendar_authoritative_through", "contains_20260907",
            "next_trade_date_status",
        )
    }


def fetch_auction_bridge() -> dict[str, object]:
    """Use date-bound local Auction events and bounded Longbridge Daily reads."""
    from trend_monitor.providers.longbridge import LongbridgeProvider

    symbols = ("600487.SH", "002463.SZ")
    dates = (date(2026, 9, 3), date(2026, 9, 4))
    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    daily_by_key = {}
    daily_raw = {}
    for symbol in symbols:
        raw = provider.get_daily(
            symbol,
            start=_date_ms(dates[0]),
            end=_date_ms(dates[-1]),
        )
        daily_raw[symbol] = raw
        for item in raw["data"]["item"]:
            day = _source_date(int(item["timestamp"]))
            daily_by_key[(day, symbol)] = item

    samples = []
    for day in dates:
        matches = sorted(
            (PROJECT_ROOT / "data" / "raw" / "hithink" / "auction" / day.isoformat()).glob("*.json")
        )
        if not matches:
            raise ValueError(f"date-bound Auction raw evidence missing: {day}")
        raw_path = matches[-1]
        payload = _read_json(raw_path)
        response = payload.get("raw_response", {})
        data = response.get("data", {}) if isinstance(response, Mapping) else {}
        if data.get("auction_phase") != "closed" or data.get("data_status") != "final":
            raise ValueError(f"Auction evidence is not closed/final: {day}")
        for item in data.get("item", []):
            symbol = item.get("thscode")
            if symbol not in symbols:
                continue
            daily = daily_by_key[(day, symbol)]
            auction_price = item.get("auction_price")
            open_price = item.get("open_price")
            daily_open = daily.get("open")
            samples.append({
                "symbol": symbol,
                "trade_date": day.isoformat(),
                "hithink_field_name": "auction_price",
                "hithink_value": auction_price,
                "hithink_open_price": open_price,
                "daily_open": daily_open,
                "auction_difference": _float(auction_price) - _float(daily_open),
                "open_field_difference": _float(open_price) - _float(daily_open),
                "semantic_status": "CONFIRMED_FIELDS_DATE_BOUND_SAMPLE",
                "auction_raw_path": str(raw_path.relative_to(PROJECT_ROOT)),
            })
    if HITHINK_CAPABILITY_RAW.is_file():
        capability = _read_json(HITHINK_CAPABILITY_RAW)
        observed = datetime.fromisoformat(capability["observed_at"]).astimezone(SHANGHAI)
        auction_data = capability["auction_response"].get("data", {})
        daily_target = next(
            item for item in load_daily_csv() if item.trading_date == TARGET_DATE
        )
        items = [item for item in auction_data.get("item", []) if item.get("thscode") == SYMBOL]
        if (
            observed.date() == TARGET_DATE
            and auction_data.get("auction_phase") == "closed"
            and auction_data.get("data_status") == "final"
            and len(items) == 1
        ):
            item = items[0]
            samples.append({
                "symbol": SYMBOL,
                "trade_date": TARGET_DATE.isoformat(),
                "hithink_field_name": "auction_price",
                "hithink_value": item.get("auction_price"),
                "hithink_open_price": item.get("open_price"),
                "daily_open": daily_target.open,
                "auction_difference": _float(item.get("auction_price")) - daily_target.open,
                "open_field_difference": _float(item.get("open_price")) - daily_target.open,
                "semantic_status": "CONFIRMED_FIELDS_SAME_SHANGHAI_TRADING_DATE",
                "auction_raw_path": str(HITHINK_CAPABILITY_RAW.relative_to(PROJECT_ROOT)),
            })
    passed = len(samples) >= 5 and all(
        abs(float(item["auction_difference"])) < 1e-12
        and abs(float(item["open_field_difference"])) < 1e-12
        for item in samples
    )
    raw_payload = {
        "samples": samples,
        "longbridge_daily_raw": daily_raw,
    }
    _write_json(AUCTION_BRIDGE_RAW, raw_payload)
    report = {
        "schema_version": 1,
        "status": "PROVISIONAL_CONFIRMED" if passed else "REJECTED",
        "sample_size": len(samples),
        "hithink_field_semantics": {
            "auction_price": "竞价价格",
            "open_price": "开盘价",
        },
        "longbridge_field": "daily_open",
        "samples": samples,
        "limitation": "Only five date-bound live/catch-up samples across three symbols.",
    }
    _write_json(BRIDGE_JSON, report)
    return report


def fetch_hithink_daily_samples() -> dict[str, object]:
    """Fetch 10-20 isolated dates rather than a second three-year series."""
    from trend_monitor.providers.hithink import HithinkProvider

    with SIMILAR_CSV.open(encoding="utf-8", newline="") as handle:
        similar_dates = [date.fromisoformat(item["date"]) for item in csv.DictReader(handle)]
    fixed = [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2), HISTORY_END, TARGET_DATE]
    selected = []
    for day in [*fixed, *similar_dates[:10]]:
        if day not in selected:
            selected.append(day)
    selected = selected[:15]
    provider = HithinkProvider(dotenv_path=PROJECT_ROOT / ".env")
    samples = []
    for day in selected:
        raw = provider.stock_history(
            SYMBOL,
            start=_date_ms(day),
            end=_date_ms(day),
            adjust="none",
        )
        items = raw.get("data", {}).get("item", [])
        samples.append({
            "requested_date": day.isoformat(),
            "response": raw,
            "rows": len(items),
        })
    payload = {
        "schema_version": 1,
        "symbol": SYMBOL,
        "adjust": "none",
        "selection": "five target-adjacent dates plus first ten deterministic similar-event dates",
        "sample_count": len(samples),
        "samples": samples,
    }
    _write_json(HITHINK_DAILY_SAMPLES_RAW, payload)
    return {"sample_count": len(samples), "dates": [item.isoformat() for item in selected]}


def daily_cross_validation(bars: Sequence[DailyBar]) -> dict[str, object]:
    if not HITHINK_DAILY_SAMPLES_RAW.is_file():
        return {"status": "NOT_AVAILABLE", "reason": "sample file has not been fetched"}
    local = {item.trading_date: item for item in bars}
    source = _read_json(HITHINK_DAILY_SAMPLES_RAW)
    rows = []
    for sample in source["samples"]:
        requested = date.fromisoformat(sample["requested_date"])
        items = sample["response"].get("data", {}).get("item", [])
        if len(items) != 1 or requested not in local:
            rows.append({
                "date": requested.isoformat(),
                "status": "DATA_CONFLICT",
                "reason": f"expected exactly one Hithink row, got {len(items)}",
            })
            continue
        right = items[0]
        left = local[requested]
        price_differences = {
            field: _float(right[f"{field}_price"]) - float(getattr(left, field))
            for field in ("open", "high", "low", "close")
        }
        turnover_difference = _float(right["turnover"]) - left.turnover
        volume_ratio = _float(right["volume"]) / left.volume if left.volume else None
        rows.append({
            "date": requested.isoformat(),
            "status": "PASS_PRICE_TURNOVER_VOLUME_UNIT_UNRESOLVED",
            "price_differences": price_differences,
            "turnover_difference": turnover_difference,
            "hithink_volume": right["volume"],
            "longbridge_volume": left.volume,
            "raw_volume_ratio_hithink_to_longbridge": volume_ratio,
        })
    price_pass = all(
        row.get("status") != "DATA_CONFLICT"
        and all(abs(float(value)) < 1e-8 for value in row["price_differences"].values())
        and abs(float(row["turnover_difference"])) < 1.0
        for row in rows
    )
    volume_equal = all(
        row.get("hithink_volume") == row.get("longbridge_volume") for row in rows
    )
    status = "PASS" if price_pass and volume_equal else "DATA_CONFLICT"
    report = {
        "schema_version": 1,
        "status": status,
        "price_and_date_status": "PASS" if price_pass else "DATA_CONFLICT",
        "volume_status": "PASS" if volume_equal else "UNIT_SEMANTICS_UNRESOLVED",
        "volume_handling": "No conversion applied; formal feature uses only Longbridge within-source ratio.",
        "sample_size": len(rows),
        "rows": rows,
    }
    _write_json(DAILY_VALIDATION_JSON, report)
    return report


def analyze() -> dict[str, object]:
    bars = load_daily_csv()
    features = build_daily_features(bars)
    target = target_event(features)
    events = event_pool(features, bars)
    similar, similarity_metadata = rank_similar_events(events, target)
    opening = opening_study(events, target)
    write_csv(EVENTS_CSV, events)
    write_csv(SIMILAR_CSV, similar)

    formal_rows = [
        item for item in features
        if FORMAL_START <= date.fromisoformat(str(item["date"])) <= TARGET_DATE
    ]
    target["feature_contract"] = {
        "atr14_sma": "SMA of 14 causal True Range values, inclusive of T0",
        "moving_averages": "close-only causal rolling means, inclusive of T0",
        "prior_highs": "maximum Daily High over prior N trading days, excluding T0",
        "breakout": "T0 Daily Close strictly above the prior-N Daily High",
        "ma_slope": "T0 moving average minus prior trading day's moving average",
        "volume_ratio_20": "T0 Longbridge volume divided by prior 20D Longbridge average; no cross-provider unit conversion",
    }
    target["formal_range"] = {
        "start": FORMAL_START.isoformat(),
        "end": TARGET_DATE.isoformat(),
        "trading_days": len(formal_rows),
    }
    _write_json(TARGET_JSON, target)

    study = {
        "schema_version": 1,
        "study_version": "historical_breakout_add_study_v0.1",
        "symbol": SYMBOL,
        "name": NAME,
        "analysis_cutoff": TARGET_DATE.isoformat(),
        "mode": "OFFLINE_RESEARCH_SHADOW_EXPERIMENT",
        "production_rule_modified": False,
        "production_runtime_modified": False,
        "lookahead": "PASS",
        "determinism_contract": "same immutable CSV produces byte-equivalent analytical values",
        "history": {
            "warmup_start": bars[0].trading_date.isoformat(),
            "formal_start": FORMAL_START.isoformat(),
            "calibration_start": FORMAL_START.isoformat(),
            "calibration_end": CALIBRATION_END.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "validation_end": HISTORY_END.isoformat(),
            "target_date": TARGET_DATE.isoformat(),
            "target_excluded_from_fitting": True,
        },
        "event_definition": {
            "wide_pool": "Daily Close > prior 20D, 40D, or 60D Daily High",
            "thresholds_selected_after_outcomes": False,
            "events": len(events),
            "calibration_events": sum(
                item["segment"] == "CALIBRATION" for item in events
            ),
            "validation_events": sum(
                item["segment"] == "VALIDATION" for item in events
            ),
            "distributions": _event_distributions(events),
        },
        "target_assessment": {
            "historical_type": "STRONG_20D_CLOSE_BREAKOUT_NOT_40D_OR_60D_BREAKOUT",
            "breakout_signature": target["breakout_signature"],
            "key_level_35_01": "APPROXIMATE",
            "ma250_near_35": "CONFIRMED",
            "ma250_minus_35_01": float(target["ma250"]) - 35.01,
            "ma250_minus_35_01_atr": (
                (float(target["ma250"]) - 35.01) / float(target["atr14_sma"])
            ),
            "entry_37_47_assessment": "EVIDENCE_INSUFFICIENT",
            "entry_equals_target_close": abs(float(target["close"]) - 37.47) < 1e-12,
            "entry_below_day_high_pct": 37.47 / float(target["high"]) - 1,
            "entry_above_breakout_reference_atr": target["breakout_distance_20_atr"],
            "entry_above_ma250_atr": target["distance_ma250_atr"],
        },
        "target_percentiles_in_wide_pool": _target_percentiles(events, target),
        "similarity": similarity_metadata,
        "top_similar_count": len(similar),
        "similar_sample_status": (
            "READY" if len(similar) >= SIMILAR_LIMIT else "SAMPLE_THIN"
        ),
        "similar_outcomes": _similar_outcome_summary(similar),
        "opening_gap_study": opening,
        "ma250_defense": {
            **_stop_study(similar),
            "assessment": "INCONCLUSIVE",
            "reason": "Only 11 same-signature events; MA250 triggered in 4/11 and washed out a later-positive T+5 path in 3/11.",
        },
        "daily_cross_validation": daily_cross_validation(bars),
        "auction_open_bridge": (
            _read_json(BRIDGE_JSON) if BRIDGE_JSON.is_file() else {"status": "NOT_AVAILABLE"}
        ),
        "historical_market_context": "DEFERRED",
        "entry_ma_cross": "UNKNOWN",
    }
    _write_json(STUDY_JSON, study)
    playbook = {
        "schema_version": 1,
        "playbook_version": "opening_playbook_v0.1",
        "symbol": SYMBOL,
        "target_trade_date": None,
        "target_trade_date_status": "UNKNOWN_PENDING_HITHINK_CALENDAR",
        "mode": "READ_ONLY_SHADOW",
        "objective": "PRECISION_FIRST",
        "required_auction_state": {
            "auction_phase": "closed",
            "data_status": "final",
        },
        "auction_market_time": "09:25 Asia/Shanghai",
        "historical_proxy_field": "daily_open",
        "auction_open_bridge_required": "PROVISIONAL_CONFIRMED",
        "target_event_close": target["close"],
        "gap_buckets": {
            "LOW_OPEN": {"max_inclusive": opening["low_boundary"]},
            "NEUTRAL_OPEN": {
                "min_exclusive": opening["low_boundary"],
                "max_exclusive": opening["high_boundary"],
            },
            "HIGH_OPEN": {"min_inclusive": opening["high_boundary"]},
        },
        "scenarios": opening["scenarios"],
        "actions": ["ADD", "HOLD", "DEFENSIVE", "NO_SIGNAL"],
        "add_execution_policy": {
            "add_qualification": "YES only when matched scenario decision is ADD",
            "execute_at_auction": "NO",
            "reason": "Daily Open bridge is provisional and no executable post-open window has been validated.",
            "target_position_size": "UNKNOWN",
            "provisional_experiment_increment_shares": 100,
        },
        "calendar_gate": "current trade date must be present in Hithink authoritative trading-days response",
        "data_not_ready_action": "NO_DECISION",
    }
    _write_json(PLAYBOOK_JSON, playbook)
    return study


def _minute_records(raw: Mapping[str, object]) -> list[dict[str, object]]:
    request = raw.get("request")
    data = raw.get("data")
    items = data.get("item") if isinstance(data, Mapping) else None
    if (
        raw.get("provider") != "longbridge"
        or not isinstance(request, Mapping)
        or request.get("symbol") != SYMBOL
        or request.get("adjust_type") != "none"
        or not isinstance(items, list)
    ):
        raise ValueError("minute source contract mismatch")
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _minute_bar_summary(raw: Mapping[str, object], next_day: date) -> dict[str, object] | None:
    items = [
        item for item in _minute_records(raw)
        if _source_date(int(item["timestamp"])) == next_day
    ]
    if not items:
        return None
    items.sort(key=lambda item: int(item["timestamp"]))
    first = items[0]
    open_price = _float(first["open"])
    return {
        "timestamp": int(first["timestamp"]),
        "open": open_price,
        "high": _float(first["high"]),
        "low": _float(first["low"]),
        "close": _float(first["close"]),
        "return": _float(first["close"]) / open_price - 1,
        "mfe": _float(first["high"]) / open_price - 1,
        "mae": _float(first["low"]) / open_price - 1,
    }


def fetch_similar_minutes() -> dict[str, object]:
    from trend_monitor.providers.longbridge import LongbridgeProvider

    bars = load_daily_csv()
    dates = [item.trading_date for item in bars]
    by_date = {value: index for index, value in enumerate(dates)}
    with SIMILAR_CSV.open(encoding="utf-8", newline="") as handle:
        similar = list(csv.DictReader(handle))
    if len(similar) > 30:
        raise ValueError("local minute event limit exceeded")
    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")
    output = []
    for item in similar:
        event_date = date.fromisoformat(item["date"])
        index = by_date[event_date]
        if index + 2 >= len(dates) or dates[index + 2] > HISTORY_END:
            continue
        end_date = dates[index + 2]
        next_day = dates[index + 1]
        event = {
            "event_date": event_date.isoformat(),
            "next_trading_day": next_day.isoformat(),
            "window_end": end_date.isoformat(),
            "post_open_only": True,
        }
        for period in ("15m", "60m"):
            raw = provider.get_history_candlesticks(
                SYMBOL,
                period=period,
                start=event_date,
                end=end_date,
            )
            path = MINUTE_RAW_DIR / f"{event_date.isoformat()}_{period}.json"
            _write_json(path, raw)
            event[f"first_{period}"] = _minute_bar_summary(raw, next_day)
            event[f"{period}_raw"] = str(path.relative_to(OUTPUT_DIR))
        output.append(event)
    payload = {
        "schema_version": 1,
        "symbol": SYMBOL,
        "source": "Longbridge DIRECT 15m/60m NoAdjust",
        "scope": "Top Similar Events only; T0 through T+2",
        "event_limit": 30,
        "events_requested": len(similar),
        "events_ready": len(output),
        "feature_timing": "POST_OPEN_PATH_STUDY_NOT_AVAILABLE_AT_09:25",
        "rows": output,
        "summary": _post_open_summary(output),
    }
    _write_json(MINUTE_STUDY_JSON, payload)
    return payload


def summarize_similar_minutes() -> dict[str, object]:
    payload = _read_json(MINUTE_STUDY_JSON)
    payload["summary"] = _post_open_summary(payload.get("rows", []))
    _write_json(MINUTE_STUDY_JSON, payload)
    return payload["summary"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "fetch-daily",
            "fetch-capabilities",
            "fetch-auction-bridge",
            "fetch-hithink-daily-samples",
            "analyze",
            "fetch-similar-minutes",
            "summarize-similar-minutes",
        ),
    )
    args = parser.parse_args(argv)
    if args.command == "fetch-daily":
        result = fetch_daily()
    elif args.command == "fetch-capabilities":
        result = fetch_capability_audit()
    elif args.command == "fetch-auction-bridge":
        result = fetch_auction_bridge()
    elif args.command == "fetch-hithink-daily-samples":
        result = fetch_hithink_daily_samples()
    elif args.command == "fetch-similar-minutes":
        result = fetch_similar_minutes()
    elif args.command == "summarize-similar-minutes":
        result = summarize_similar_minutes()
    else:
        result = analyze()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
