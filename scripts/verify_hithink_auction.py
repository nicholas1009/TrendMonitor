#!/usr/bin/env python3
"""Minimal authenticated verification of the TASK_016 Auction endpoint."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.providers.hithink import HithinkProvider  # noqa: E402
from trend_monitor.runtime.auction import parse_auction_snapshot  # noqa: E402


SYMBOLS = ("600487.SH", "002463.SZ")
OUTPUT_FIELDS = (
    "thscode",
    "name",
    "auction_price",
    "auction_pct",
    "auction_volume",
    "auction_amount",
    "auction_unmatched",
    "auction_volume_ratio",
)


def main() -> int:
    provider = HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env"))
    raw = provider.auction_snapshot(list(SYMBOLS), stage="final")
    parsed = parse_auction_snapshot(raw, expected_symbols=SYMBOLS)
    rows = []
    for item in parsed["items"]:
        row = {field: item.get(field) for field in OUTPUT_FIELDS}
        row["auction_phase"] = parsed["auction_phase"]
        row["data_status"] = parsed["data_status"]
        rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if parsed["final"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
