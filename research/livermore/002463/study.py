#!/usr/bin/env python3
"""Offline TASK_017 natural-move sensitivity study for 002463.SZ.

The study deliberately implements only an ATR reversal baseline.  It does not
promote a Natural Rally/Reaction into a new primary trend because doing so
would require the still-undefined pivot confirmation rule.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import statistics
import sys
from typing import Iterable, Sequence
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_DIR = Path(__file__).resolve().parent
DAILY_CSV = OUTPUT_DIR / "daily_input.csv"
SOURCE_MANIFEST = OUTPUT_DIR / "source_manifest.json"
SENSITIVITY_CSV = OUTPUT_DIR / "natural_move_sensitivity.csv"
TRANSITIONS_CSV = OUTPUT_DIR / "state_transitions.csv"
LEGACY_REPLAY_CSV = OUTPUT_DIR / "legacy_replay.csv"
LEGACY_XLSX = PROJECT_ROOT / "legacy" / "A股价格趋势记录.xlsx"
LEGACY_END = date(2026, 8, 12)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SYMBOL = "002463.SZ"
INSTRUMENT_ID = "stock.wus_printed_circuit"
ATR_PERIOD = 14
K_VALUES = tuple(Decimal(value) for value in (
    "1.0", "1.25", "1.5", "1.75", "2.0", "2.25", "2.5", "2.75", "3.0"
))

UPWARD_TREND = "UPWARD_TREND"
DOWNWARD_TREND = "DOWNWARD_TREND"
NATURAL_RALLY = "NATURAL_RALLY"
NATURAL_REACTION = "NATURAL_REACTION"


@dataclass(frozen=True, slots=True)
class DailyBar:
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    turnover: Decimal
    provider_timestamp: int
    atr14_sma: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DailyState:
    index: int
    trading_date: date
    state: str
    direction: str
    close: Decimal
    atr14_sma: Decimal


@dataclass(frozen=True, slots=True)
class Transition:
    k: Decimal
    index: int
    trading_date: date
    from_state: str
    to_state: str
    new_direction: str
    anchor_index: int
    anchor_date: date
    anchor_price: Decimal
    anchor_atr: Decimal
    threshold: Decimal
    trigger_close: Decimal

    @property
    def detection_delay_days(self) -> int:
        return self.index - self.anchor_index


@dataclass(frozen=True, slots=True)
class Replay:
    states: tuple[DailyState, ...]
    transitions: tuple[Transition, ...]


@dataclass(frozen=True, slots=True)
class LegacyRecord:
    trading_date: date
    state: str
    price: Decimal


def decimal_text(value: Decimal, places: str = "0.000000") -> str:
    return format(value.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def validate_direct_daily_contract(
    raw: dict[str, object],
    *,
    symbol: str = SYMBOL,
) -> None:
    request = raw.get("request")
    if not isinstance(request, dict):
        raise ValueError("Longbridge request metadata is missing")
    expected = {
        "symbol": symbol,
        "data_type": "daily",
        "period": "1d",
        "adjust_type": "none",
    }
    actual = {key: request.get(key) for key in expected}
    if actual != expected:
        raise ValueError(f"not DIRECT DAILY NoAdjust: {actual}")
    if raw.get("provider") != "longbridge":
        raise ValueError("unexpected provider")


def fetch_daily(
    start: date,
    end: date,
    *,
    symbol: str = SYMBOL,
) -> list[DailyBar]:
    """Fetch one verified DIRECT Daily series through the existing provider."""
    from trend_monitor.providers.longbridge import LongbridgeProvider

    if start > end:
        raise ValueError("start is after end")
    provider = LongbridgeProvider(dotenv_path=PROJECT_ROOT / ".env")

    def epoch_ms(day: date) -> int:
        local = datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI)
        return int(local.timestamp() * 1000)

    raw = provider.get_daily(symbol, start=epoch_ms(start), end=epoch_ms(end))
    validate_direct_daily_contract(raw, symbol=symbol)
    data = raw.get("data")
    items = data.get("item") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("Longbridge Daily response is empty")
    bars: list[DailyBar] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Longbridge Daily item is not an object")
        timestamp = int(item["timestamp"])
        local_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(SHANGHAI).date()
        bar = DailyBar(
            trading_date=local_date,
            open=Decimal(str(item["open"])),
            high=Decimal(str(item["high"])),
            low=Decimal(str(item["low"])),
            close=Decimal(str(item["close"])),
            volume=int(item["volume"]),
            turnover=Decimal(str(item["turnover"])),
            provider_timestamp=timestamp,
        )
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise ValueError(f"non-positive OHLC on {local_date}")
        if bar.high < max(bar.open, bar.low, bar.close) or bar.low > min(bar.open, bar.high, bar.close):
            raise ValueError(f"invalid OHLC on {local_date}")
        bars.append(bar)
    bars.sort(key=lambda item: item.trading_date)
    if len({item.trading_date for item in bars}) != len(bars):
        raise ValueError("duplicate Longbridge Daily date")
    return bars


def write_daily_input(bars: Sequence[DailyBar], path: Path = DAILY_CSV) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow((
            "date", "open", "high", "low", "close", "volume", "turnover",
            "provider_timestamp", "source", "daily_contract", "adjust_type",
        ))
        for bar in bars:
            writer.writerow((
                bar.trading_date.isoformat(), str(bar.open), str(bar.high), str(bar.low),
                str(bar.close), bar.volume, str(bar.turnover), bar.provider_timestamp,
                "longbridge", "DIRECT_DAILY", "none",
            ))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_source_manifest(
    bars: Sequence[DailyBar],
    *,
    requested_start: date,
    requested_end: date,
    csv_sha256: str,
    path: Path = SOURCE_MANIFEST,
) -> None:
    payload = {
        "schema_version": 1,
        "task": "TASK_017",
        "instrument_id": INSTRUMENT_ID,
        "symbol": SYMBOL,
        "provider": "longbridge",
        "endpoint": "history_candlesticks_by_date",
        "daily_contract": "DIRECT_DAILY",
        "period": "1d",
        "adjust_type": "none",
        "atr_contract": "ATR14_SMA",
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "actual_start": bars[0].trading_date.isoformat(),
        "actual_end": bars[-1].trading_date.isoformat(),
        "trading_days": len(bars),
        "daily_input": DAILY_CSV.name,
        "daily_input_sha256": csv_sha256,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_daily_input(path: Path = DAILY_CSV) -> list[DailyBar]:
    bars: list[DailyBar] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["source"] != "longbridge" or row["daily_contract"] != "DIRECT_DAILY":
                raise ValueError("offline input is not Longbridge DIRECT DAILY")
            if row["adjust_type"] != "none":
                raise ValueError("offline input is not NoAdjust")
            bars.append(DailyBar(
                trading_date=date.fromisoformat(row["date"]),
                open=Decimal(row["open"]), high=Decimal(row["high"]),
                low=Decimal(row["low"]), close=Decimal(row["close"]),
                volume=int(row["volume"]), turnover=Decimal(row["turnover"]),
                provider_timestamp=int(row["provider_timestamp"]),
            ))
    if not bars or bars != sorted(bars, key=lambda item: item.trading_date):
        raise ValueError("offline Daily input is empty or unordered")
    if len({item.trading_date for item in bars}) != len(bars):
        raise ValueError("offline Daily input has duplicate dates")
    return bars


def with_atr14_sma(bars: Sequence[DailyBar]) -> list[DailyBar]:
    """Causal 14-period SMA of True Range; T uses only bars through T."""
    result: list[DailyBar] = []
    true_ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in bars:
        values = [bar.high - bar.low]
        if previous_close is not None:
            values.extend((abs(bar.high - previous_close), abs(bar.low - previous_close)))
        true_ranges.append(max(values))
        atr = sum(true_ranges[-ATR_PERIOD:], Decimal("0")) / ATR_PERIOD if len(true_ranges) >= ATR_PERIOD else None
        result.append(replace(bar, atr14_sma=atr))
        previous_close = bar.close
    return result


def replay_natural_moves(
    bars: Sequence[DailyBar],
    k: Decimal,
    *,
    start: date | None = None,
    end: date | None = None,
    initial_state: str | None = None,
) -> Replay:
    """Run a causal ATR-reversal replay.

    The ATR is frozen at the current directional extreme.  A newly established
    extreme cannot trigger a reversal on the same Daily bar, avoiding an
    unknowable intraday high/low ordering assumption.
    """
    if k <= 0:
        raise ValueError("natural_move_k must be positive")
    eligible = [
        item for item in bars
        if item.atr14_sma is not None
        and (start is None or item.trading_date >= start)
        and (end is None or item.trading_date <= end)
    ]
    if len(eligible) < 2:
        raise ValueError("insufficient ATR-ready Daily bars")
    first = eligible[0]
    assert first.atr14_sma is not None
    if initial_state is None:
        original_index = bars.index(first)
        if original_index == 0:
            raise ValueError("a prior close is required for causal initialization")
        direction = "UP" if first.close >= bars[original_index - 1].close else "DOWN"
        state = UPWARD_TREND if direction == "UP" else DOWNWARD_TREND
    else:
        if initial_state not in {UPWARD_TREND, DOWNWARD_TREND}:
            raise ValueError("initial state must be a primary trend state")
        state = initial_state
        direction = "UP" if state == UPWARD_TREND else "DOWN"

    anchor_price = first.high if direction == "UP" else first.low
    anchor_atr = first.atr14_sma
    anchor_index = 0
    anchor_date = first.trading_date
    states = [DailyState(0, first.trading_date, state, direction, first.close, first.atr14_sma)]
    transitions: list[Transition] = []

    for index, bar in enumerate(eligible[1:], start=1):
        assert bar.atr14_sma is not None
        if direction == "UP":
            if bar.high > anchor_price:
                anchor_price, anchor_atr = bar.high, bar.atr14_sma
                anchor_index, anchor_date = index, bar.trading_date
            else:
                threshold = anchor_price - k * anchor_atr
                if bar.close <= threshold:
                    old_state = state
                    state = NATURAL_REACTION
                    transitions.append(Transition(
                        k, index, bar.trading_date, old_state, state, "DOWN",
                        anchor_index, anchor_date, anchor_price, anchor_atr,
                        threshold, bar.close,
                    ))
                    direction = "DOWN"
                    anchor_price, anchor_atr = bar.low, bar.atr14_sma
                    anchor_index, anchor_date = index, bar.trading_date
        else:
            if bar.low < anchor_price:
                anchor_price, anchor_atr = bar.low, bar.atr14_sma
                anchor_index, anchor_date = index, bar.trading_date
            else:
                threshold = anchor_price + k * anchor_atr
                if bar.close >= threshold:
                    old_state = state
                    state = NATURAL_RALLY
                    transitions.append(Transition(
                        k, index, bar.trading_date, old_state, state, "UP",
                        anchor_index, anchor_date, anchor_price, anchor_atr,
                        threshold, bar.close,
                    ))
                    direction = "UP"
                    anchor_price, anchor_atr = bar.high, bar.atr14_sma
                    anchor_index, anchor_date = index, bar.trading_date
        states.append(DailyState(index, bar.trading_date, state, direction, bar.close, bar.atr14_sma))
    return Replay(tuple(states), tuple(transitions))


def _state_runs(states: Sequence[DailyState]) -> list[int]:
    if not states:
        return []
    lengths: list[int] = []
    current = states[0].state
    count = 0
    for item in states:
        if item.state != current:
            lengths.append(count)
            current, count = item.state, 0
        count += 1
    lengths.append(count)
    return lengths


def _next_transition(events: Sequence[Transition], index: int) -> Transition | None:
    return events[index + 1] if index + 1 < len(events) else None


def segment_metrics(
    replay: Replay,
    bars: Sequence[DailyBar],
    *,
    start: date,
    end: date,
) -> dict[str, object]:
    states = [item for item in replay.states if start <= item.trading_date <= end]
    events = [item for item in replay.transitions if start <= item.trading_date <= end]
    state_index = {item.trading_date: item.index for item in replay.states}
    all_bar_by_date = {item.trading_date: item for item in bars}

    reverse = {10: 0, 20: 0}
    whipsaw = {5: 0, 10: 0}
    premature = 0
    for event_index, event in enumerate(events):
        following = _next_transition(events, event_index)
        if following is None:
            continue
        gap = state_index[following.trading_date] - state_index[event.trading_date]
        for horizon in reverse:
            if gap <= horizon:
                reverse[horizon] += 1
        between_states = [
            item for item in replay.states
            if event.trading_date <= item.trading_date < following.trading_date
        ]
        between_bars = [all_bar_by_date[item.trading_date] for item in between_states]
        if event.new_direction == "UP":
            favorable = max((item.high for item in between_bars), default=event.trigger_close) - event.trigger_close
        else:
            favorable = event.trigger_close - min((item.low for item in between_bars), default=event.trigger_close)
        for horizon in whipsaw:
            if gap <= horizon and favorable < event.anchor_atr:
                whipsaw[horizon] += 1

        if gap <= 20:
            event_position = state_index[event.trading_date]
            horizon_states = [
                item for item in replay.states
                if following.trading_date <= item.trading_date
                and item.index <= event_position + 20
            ]
            horizon_bars = [all_bar_by_date[item.trading_date] for item in horizon_states]
            if event.new_direction == "UP" and any(item.low < event.anchor_price for item in horizon_bars):
                premature += 1
            if event.new_direction == "DOWN" and any(item.high > event.anchor_price for item in horizon_bars):
                premature += 1

    run_lengths = _state_runs(states)
    counts = {name: sum(item.state == name for item in states) for name in (
        UPWARD_TREND, DOWNWARD_TREND, NATURAL_RALLY, NATURAL_REACTION,
    )}
    event_count = len(events)
    return {
        "trading_days": len(states),
        "rally_count": sum(item.to_state == NATURAL_RALLY for item in events),
        "reaction_count": sum(item.to_state == NATURAL_REACTION for item in events),
        "event_count": event_count,
        "average_duration": round(statistics.mean(run_lengths), 3) if run_lengths else None,
        "median_duration": round(statistics.median(run_lengths), 3) if run_lengths else None,
        "reverse_10d": reverse[10],
        "reverse_20d": reverse[20],
        "whipsaw_5d": whipsaw[5],
        "whipsaw_10d": whipsaw[10],
        "whipsaw_10d_rate": round(whipsaw[10] / event_count, 6) if event_count else None,
        "premature_interruptions": premature,
        "premature_rate": round(premature / event_count, 6) if event_count else None,
        "mean_detection_delay": round(statistics.mean(item.detection_delay_days for item in events), 3) if events else None,
        "median_detection_delay": round(statistics.median(item.detection_delay_days for item in events), 3) if events else None,
        "state_coverage_rate": round(len(states) / len(states), 6) if states else 0,
        "upward_trend_coverage": round(counts[UPWARD_TREND] / len(states), 6) if states else 0,
        "downward_trend_coverage": round(counts[DOWNWARD_TREND] / len(states), 6) if states else 0,
        "natural_rally_coverage": round(counts[NATURAL_RALLY] / len(states), 6) if states else 0,
        "natural_reaction_coverage": round(counts[NATURAL_REACTION] / len(states), 6) if states else 0,
    }


def _shared_strings(archive: ZipFile) -> list[str]:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{{{ns}}}t")) for item in root]


def load_legacy_records(path: Path = LEGACY_XLSX) -> tuple[list[LegacyRecord], dict[str, Decimal]]:
    """Read the legacy workbook without invoking any spreadsheet save path."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    q = lambda name: f"{{{main}}}{name}"
    with ZipFile(path) as archive:
        strings = _shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = next(item for item in workbook.find(q("sheets")) if item.get("name") == "沪电股份")
        relationship_id = sheet.get(f"{{{rel_ns}}}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(item.get("Target") for item in rels if item.get("Id") == relationship_id)
        normalized_target = str(target).lstrip("/")
        sheet_path = (
            normalized_target
            if normalized_target.startswith("xl/")
            else "xl/" + normalized_target
        )
        root = ET.fromstring(archive.read(sheet_path))

        def value(cell: ET.Element | None) -> str | None:
            if cell is None:
                return None
            if cell.get("t") == "inlineStr":
                inline = cell.find(q("is"))
                return "" if inline is None else "".join(
                    node.text or "" for node in inline.iter(q("t"))
                )
            node = cell.find(q("v"))
            if node is None or node.text is None:
                return None
            return strings[int(node.text)] if cell.get("t") == "s" else node.text

        cells = {cell.get("r"): cell for cell in root.findall(f".//{q('sheetData')}/{q('row')}/{q('c')}")}
        state_map = {
            "B": "SECONDARY_RALLY", "C": NATURAL_RALLY, "D": UPWARD_TREND,
            "E": DOWNWARD_TREND, "F": NATURAL_REACTION, "G": "SECONDARY_REACTION",
        }
        records: list[LegacyRecord] = []
        for row in range(3, 203):
            raw_date = value(cells.get(f"A{row}"))
            if raw_date in (None, ""):
                continue
            trading_date = date(1899, 12, 30) + timedelta(days=int(Decimal(raw_date)))
            if trading_date > LEGACY_END:
                continue
            populated = [
                (state_map[column], value(cells.get(f"{column}{row}")))
                for column in state_map
                if value(cells.get(f"{column}{row}")) not in (None, "")
            ]
            if len(populated) != 1:
                raise ValueError(f"legacy row {row} does not have exactly one state price")
            records.append(LegacyRecord(trading_date, populated[0][0], Decimal(str(populated[0][1]))))
        decline_low = Decimal(str(value(cells.get("J4"))))
        legacy_atr14 = Decimal(str(value(cells.get("J6"))))
        raw_threshold = value(cells.get("J8"))
        parameters = {
            "decline_low": decline_low,
            "legacy_atr14": legacy_atr14,
            # openpyxl deliberately does not invent cached formula results.
            # The preserved J8 formula is =J4+J7 and J7 is =J6*2, so the
            # equivalent value is derived from those unchanged source cells.
            "legacy_threshold": (
                Decimal(str(raw_threshold))
                if raw_threshold not in (None, "")
                else decline_low + Decimal("2") * legacy_atr14
            ),
        }
        return records, parameters


def legacy_comparison(
    bars: Sequence[DailyBar],
    k: Decimal,
    legacy: Sequence[LegacyRecord],
    parameters: dict[str, Decimal],
) -> dict[str, object]:
    start, end = legacy[0].trading_date, legacy[-1].trading_date
    replay = replay_natural_moves(bars, k, start=start, end=end, initial_state=DOWNWARD_TREND)
    expected_transition = next(
        item.trading_date for previous, item in zip(legacy, legacy[1:]) if item.state != previous.state
    )
    rally = next((item for item in replay.transitions if item.to_state == NATURAL_RALLY), None)
    state_by_date = {item.trading_date: item.state for item in replay.states}
    state_index_by_date = {item.trading_date: item.index for item in replay.states}
    bar_by_date = {item.trading_date: item for item in bars}
    matched_states = sum(state_by_date.get(item.trading_date) == item.state for item in legacy)
    matched_prices = sum(bar_by_date[item.trading_date].close == item.price for item in legacy)
    if rally is None:
        timing, offset, trigger_date = "NOT_TRIGGERED", None, None
    else:
        offset = state_index_by_date[rally.trading_date] - state_index_by_date[expected_transition]
        timing = "ON_TIME" if offset == 0 else "EARLY" if offset < 0 else "LATE"
        trigger_date = rally.trading_date
    anchor_bar = bar_by_date[date(2026, 7, 30)]
    assert anchor_bar.atr14_sma is not None
    legacy_threshold = parameters["decline_low"] + k * parameters["legacy_atr14"]
    return {
        "k": str(k),
        "expected_transition_date": expected_transition.isoformat(),
        "detected_transition_date": trigger_date.isoformat() if trigger_date else "",
        "timing": timing,
        "trading_day_offset": offset if offset is not None else "",
        "anchor_date": "2026-07-30",
        "anchor_low": str(anchor_bar.low),
        "atr14_sma_exact": decimal_text(anchor_bar.atr14_sma, "0.000000"),
        "atr14_sma_3dp": decimal_text(anchor_bar.atr14_sma, "0.000"),
        "threshold_exact": decimal_text(parameters["decline_low"] + k * anchor_bar.atr14_sma, "0.000000"),
        "threshold_legacy_3dp_atr": decimal_text(legacy_threshold, "0.000"),
        "trigger_close": str(rally.trigger_close) if rally else "",
        "state_matches": matched_states,
        "state_records": len(legacy),
        "price_matches": matched_prices,
        "price_records": len(legacy),
        "legacy_replay": "PASS" if matched_states == len(legacy) else "FAIL",
    }


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_offline() -> dict[str, object]:
    bars = with_atr14_sma(load_daily_input())
    eligible = [item for item in bars if item.atr14_sma is not None]
    split_index = len(eligible) * 2 // 3
    calibration_start = eligible[0].trading_date
    calibration_end = eligible[split_index - 1].trading_date
    validation_start = eligible[split_index].trading_date
    validation_end = eligible[-1].trading_date
    legacy, parameters = load_legacy_records()

    sensitivity_rows: list[dict[str, object]] = []
    transition_rows: list[dict[str, object]] = []
    legacy_rows: list[dict[str, object]] = []
    for k in K_VALUES:
        replay = replay_natural_moves(bars, k)
        combined = segment_metrics(replay, bars, start=calibration_start, end=validation_end)
        calibration = segment_metrics(replay, bars, start=calibration_start, end=calibration_end)
        validation = segment_metrics(replay, bars, start=validation_start, end=validation_end)
        legacy_row = legacy_comparison(bars, k, legacy, parameters)
        legacy_rows.append(legacy_row)
        row: dict[str, object] = {"k": str(k)}
        row.update(combined)
        row["legacy_replay"] = legacy_row["legacy_replay"]
        row["validation_result"] = (
            "IN_CANDIDATE_STABLE_REGION"
            if Decimal("1.75") <= k <= Decimal("2.5")
            else "OUTSIDE_CANDIDATE_STABLE_REGION"
        )
        for prefix, metrics in (("calibration", calibration), ("validation", validation)):
            row.update({f"{prefix}_{key}": value for key, value in metrics.items()})
        sensitivity_rows.append(row)
        for item in replay.transitions:
            segment = "CALIBRATION" if item.trading_date <= calibration_end else "VALIDATION"
            transition_rows.append({
                "k": str(k), "date": item.trading_date.isoformat(),
                "from_state": item.from_state, "to_state": item.to_state,
                "new_direction": item.new_direction, "anchor_date": item.anchor_date.isoformat(),
                "anchor_price": str(item.anchor_price), "anchor_atr14_sma": decimal_text(item.anchor_atr),
                "threshold": decimal_text(item.threshold), "trigger_close": str(item.trigger_close),
                "detection_delay_days": item.detection_delay_days, "segment": segment,
            })
    write_csv(SENSITIVITY_CSV, sensitivity_rows)
    write_csv(TRANSITIONS_CSV, transition_rows)
    write_csv(LEGACY_REPLAY_CSV, legacy_rows)
    return {
        "range": (bars[0].trading_date.isoformat(), bars[-1].trading_date.isoformat()),
        "trading_days": len(bars),
        "atr_ready_days": len(eligible),
        "calibration": (calibration_start.isoformat(), calibration_end.isoformat()),
        "validation": (validation_start.isoformat(), validation_end.isoformat()),
        "sensitivity": sensitivity_rows,
        "legacy": legacy_rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="refresh the offline DIRECT Daily input")
    parser.add_argument("--start", default="2023-08-01")
    parser.add_argument("--end", default="2026-09-01")
    args = parser.parse_args(argv)
    if args.fetch:
        requested_start, requested_end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        bars = fetch_daily(requested_start, requested_end)
        digest = write_daily_input(bars)
        write_source_manifest(
            bars, requested_start=requested_start, requested_end=requested_end, csv_sha256=digest
        )
    result = run_offline()
    k2 = next(item for item in result["legacy"] if item["k"] == "2.0")
    print(json.dumps({
        "range": result["range"],
        "trading_days": result["trading_days"],
        "atr_ready_days": result["atr_ready_days"],
        "calibration": result["calibration"],
        "validation": result["validation"],
        "k2_legacy_replay": k2["legacy_replay"],
        "k2_transition_date": k2["detected_transition_date"],
        "outputs": [
            SENSITIVITY_CSV.name, TRANSITIONS_CSV.name, LEGACY_REPLAY_CSV.name,
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
