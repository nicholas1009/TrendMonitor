#!/usr/bin/env python3
"""TASK_020 natural-move sensitivity study for 600487.SH.

This module deliberately reuses the causal ATR replay and metric implementation
from TASK_017.  It changes only the instrument, output paths, and comparison
against the already-recorded 2026-07-01 through 2026-09-02 workbook states.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Sequence
from zipfile import ZipFile
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASE_STUDY_PATH = PROJECT_ROOT / "research" / "livermore" / "002463" / "study.py"
SPEC = importlib.util.spec_from_file_location("task017_shared_study", BASE_STUDY_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load TASK_017 study: {BASE_STUDY_PATH}")
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)

OUTPUT_DIR = Path(__file__).resolve().parent
DAILY_CSV = OUTPUT_DIR / "daily_input.csv"
SOURCE_MANIFEST = OUTPUT_DIR / "source_manifest.json"
SENSITIVITY_CSV = OUTPUT_DIR / "natural_move_sensitivity.csv"
TRANSITIONS_CSV = OUTPUT_DIR / "state_transitions.csv"
CURRENT_REPLAY_CSV = OUTPUT_DIR / "current_record_replay.csv"
WORKBOOK = PROJECT_ROOT / "legacy" / "A股价格趋势记录.xlsx"

SYMBOL = "600487.SH"
INSTRUMENT_ID = "stock.hengtong_optic_electric"
FORMAL_START = date(2026, 7, 1)
FORMAL_END = date(2026, 9, 2)


def write_daily_input(bars: Sequence[base.DailyBar]) -> str:
    return base.write_daily_input(bars, DAILY_CSV)


def write_source_manifest(
    bars: Sequence[base.DailyBar],
    *,
    requested_start: date,
    requested_end: date,
    csv_sha256: str,
) -> None:
    payload = {
        "schema_version": 1,
        "task": "TASK_020",
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
    SOURCE_MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_daily_input() -> list[base.DailyBar]:
    return base.load_daily_input(DAILY_CSV)


def _shared_strings(archive: ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ET.fromstring(archive.read(path))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{main}}}t"))
        for item in root
    ]


def load_current_records() -> list[base.LegacyRecord]:
    """Load Hengtong's formal record without invoking a spreadsheet save path."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    q = lambda name: f"{{{main}}}{name}"
    with ZipFile(WORKBOOK) as archive:
        strings = _shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = next(
            item for item in workbook.find(q("sheets"))
            if item.get("name") == "亨通光电"
        )
        relationship_id = sheet.get(f"{{{rel_ns}}}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(
            item.get("Target") for item in rels
            if item.get("Id") == relationship_id
        )
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

        cells = {
            cell.get("r"): cell
            for cell in root.findall(f".//{q('sheetData')}/{q('row')}/{q('c')}")
        }
        state_map = {
            "B": "SECONDARY_RALLY",
            "C": base.NATURAL_RALLY,
            "D": base.UPWARD_TREND,
            "E": base.DOWNWARD_TREND,
            "F": base.NATURAL_REACTION,
            "G": "SECONDARY_REACTION",
        }
        records: list[base.LegacyRecord] = []
        for row in range(3, 203):
            raw_date = value(cells.get(f"A{row}"))
            if raw_date in (None, ""):
                continue
            trading_date = date(1899, 12, 30) + timedelta(days=int(Decimal(raw_date)))
            if not FORMAL_START <= trading_date <= FORMAL_END:
                continue
            populated = [
                (state, value(cells.get(f"{column}{row}")))
                for column, state in state_map.items()
                if value(cells.get(f"{column}{row}")) not in (None, "")
            ]
            if len(populated) != 1:
                raise ValueError(f"Hengtong row {row} lacks exactly one state price")
            records.append(base.LegacyRecord(
                trading_date,
                populated[0][0],
                Decimal(str(populated[0][1])),
            ))
    if not records or records[0].trading_date != FORMAL_START or records[-1].trading_date != FORMAL_END:
        raise ValueError("Hengtong formal record range is incomplete")
    return records


def current_record_comparison(
    replay: base.Replay,
    records: Sequence[base.LegacyRecord],
) -> dict[str, object]:
    state_by_date = {item.trading_date: item.state for item in replay.states}
    matched_states = sum(state_by_date.get(item.trading_date) == item.state for item in records)

    expected: list[tuple[date, str]] = [(records[0].trading_date, records[0].state)]
    expected.extend(
        (current.trading_date, current.state)
        for previous, current in zip(records, records[1:])
        if current.state != previous.state
    )
    later_detected = [
        (item.trading_date, item.to_state)
        for item in replay.transitions
        if FORMAL_START < item.trading_date <= FORMAL_END
    ]
    prior = [item for item in replay.transitions if item.trading_date <= FORMAL_START]
    detected = (
        [(prior[-1].trading_date, prior[-1].to_state)] if prior else []
    ) + later_detected
    structure_match = [state for _, state in detected] == [state for _, state in expected]
    position = {item.trading_date: item.index for item in replay.states}
    offsets: list[int] = []
    if structure_match:
        offsets = [
            position[actual_day] - position[expected_day]
            for (actual_day, _), (expected_day, _) in zip(detected, expected)
        ]
    return {
        "k": str(replay.transitions[0].k) if replay.transitions else "",
        "expected_transitions": ";".join(
            f"{day.isoformat()}:{state}" for day, state in expected
        ),
        "detected_transitions": ";".join(
            f"{day.isoformat()}:{state}" for day, state in detected
        ),
        "transition_structure": "PASS" if structure_match else "FAIL",
        "transition_offsets_trading_days": ";".join(str(item) for item in offsets),
        "max_abs_transition_offset": max((abs(item) for item in offsets), default=""),
        "state_matches": matched_states,
        "state_records": len(records),
        "state_match_rate": round(matched_states / len(records), 6),
        "extra_transitions": max(0, len(detected) - len(expected)),
        "current_record_replay": (
            "PASS" if structure_match and matched_states == len(records) else "FAIL"
        ),
    }


def run_offline() -> dict[str, object]:
    bars = base.with_atr14_sma(load_daily_input())
    eligible = [item for item in bars if item.atr14_sma is not None]
    split_index = len(eligible) * 2 // 3
    calibration_start = eligible[0].trading_date
    calibration_end = eligible[split_index - 1].trading_date
    validation_start = eligible[split_index].trading_date
    validation_end = eligible[-1].trading_date
    records = load_current_records()

    sensitivity_rows: list[dict[str, object]] = []
    transition_rows: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    for k in base.K_VALUES:
        replay = base.replay_natural_moves(bars, k)
        combined = base.segment_metrics(
            replay, bars, start=calibration_start, end=validation_end
        )
        calibration = base.segment_metrics(
            replay, bars, start=calibration_start, end=calibration_end
        )
        validation = base.segment_metrics(
            replay, bars, start=validation_start, end=validation_end
        )
        formal = base.segment_metrics(
            replay, bars, start=FORMAL_START, end=FORMAL_END
        )
        comparison = current_record_comparison(replay, records)
        comparison["k"] = str(k)
        comparison.update({
            "formal_event_count": formal["event_count"],
            "formal_whipsaw_5d": formal["whipsaw_5d"],
            "formal_whipsaw_10d": formal["whipsaw_10d"],
        })
        comparison_rows.append(comparison)

        row: dict[str, object] = {"k": str(k)}
        row.update(combined)
        row.update({
            "current_record_replay": comparison["current_record_replay"],
            "record_transition_structure": comparison["transition_structure"],
            "record_state_match_rate": comparison["state_match_rate"],
            "record_max_abs_transition_offset": comparison["max_abs_transition_offset"],
            "formal_whipsaw_5d": formal["whipsaw_5d"],
            "formal_whipsaw_10d": formal["whipsaw_10d"],
        })
        for prefix, metrics in (("calibration", calibration), ("validation", validation)):
            row.update({f"{prefix}_{key}": value for key, value in metrics.items()})
        sensitivity_rows.append(row)

        for item in replay.transitions:
            segment = "CALIBRATION" if item.trading_date <= calibration_end else "VALIDATION"
            transition_rows.append({
                "k": str(k),
                "date": item.trading_date.isoformat(),
                "from_state": item.from_state,
                "to_state": item.to_state,
                "new_direction": item.new_direction,
                "anchor_date": item.anchor_date.isoformat(),
                "anchor_price": str(item.anchor_price),
                "anchor_atr14_sma": base.decimal_text(item.anchor_atr),
                "threshold": base.decimal_text(item.threshold),
                "trigger_close": str(item.trigger_close),
                "detection_delay_days": item.detection_delay_days,
                "segment": segment,
            })

    base.write_csv(SENSITIVITY_CSV, sensitivity_rows)
    base.write_csv(TRANSITIONS_CSV, transition_rows)
    base.write_csv(CURRENT_REPLAY_CSV, comparison_rows)
    return {
        "range": (bars[0].trading_date.isoformat(), bars[-1].trading_date.isoformat()),
        "trading_days": len(bars),
        "atr_ready_days": len(eligible),
        "calibration": (calibration_start.isoformat(), calibration_end.isoformat()),
        "validation": (validation_start.isoformat(), validation_end.isoformat()),
        "sensitivity": sensitivity_rows,
        "current_replay": comparison_rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--start", default="2023-08-01")
    parser.add_argument("--end", default="2026-09-02")
    args = parser.parse_args(argv)
    if args.fetch:
        requested_start = date.fromisoformat(args.start)
        requested_end = date.fromisoformat(args.end)
        bars = base.fetch_daily(requested_start, requested_end, symbol=SYMBOL)
        digest = write_daily_input(bars)
        write_source_manifest(
            bars,
            requested_start=requested_start,
            requested_end=requested_end,
            csv_sha256=digest,
        )
    result = run_offline()
    k2 = next(item for item in result["current_replay"] if item["k"] == "2.0")
    print(json.dumps({
        "range": result["range"],
        "trading_days": result["trading_days"],
        "atr_ready_days": result["atr_ready_days"],
        "calibration": result["calibration"],
        "validation": result["validation"],
        "k2_current_record_replay": k2["current_record_replay"],
        "k2_detected_transitions": k2["detected_transitions"],
        "outputs": [
            SENSITIVITY_CSV.name,
            TRANSITIONS_CSV.name,
            CURRENT_REPLAY_CSV.name,
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
