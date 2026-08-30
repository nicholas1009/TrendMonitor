#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.normalization import normalize_historical, normalize_snapshot  # noqa: E402
from trend_monitor.providers.hithink import (  # noqa: E402
    ErrorCategory,
    HithinkProvider,
    HithinkProviderError,
)
from trend_monitor.schemas import AssetType  # noqa: E402
from trend_monitor.utils.raw_samples import (  # noqa: E402
    save_normalized,
    save_raw_response,
)
from trend_monitor.validation import validate_raw_items, validate_records  # noqa: E402


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class Check:
    outcome: Outcome
    name: str
    detail: str = ""


STOCKS = [("600487", "亨通光电"), ("002463", "沪电股份")]
INDICES = [
    ("000001", "上证指数"),
    ("000016", "上证50"),
    ("399300", "沪深300"),
    ("000905", "中证500"),
    ("000902", "中证流通"),
    ("399006", "创业板指数"),
    ("000852", "中证1000"),
    ("000688", "科创50"),
]
SECTORS = [
    ("BK0475", "银行"),
    ("BK0437", "煤炭"),
    ("BK0448", "通信设备"),
    ("BK1036", "半导体"),
]
ETF_QUERY = ("510300", "沪深300")


def add(checks: list[Check], outcome: Outcome, name: str, detail: str = "") -> None:
    checks.append(Check(outcome, name, detail))
    suffix = f" — {detail}" if detail else ""
    print(f"[{outcome.value}] {name}{suffix}")


def data_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    validate_raw_items(raw)
    return raw["data"]["item"]


def search_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate search shape while allowing a legitimate empty search page."""
    data = raw.get("data")
    if not isinstance(data, dict):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "search response data is not an object"
        )
    items = data.get("item")
    if not isinstance(items, list):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "search response data.item is not an array"
        )
    if not all(isinstance(item, dict) for item in items):
        raise HithinkProviderError(
            ErrorCategory.INVALID_DATA, "search response contains a non-object item"
        )
    return items


def validate_snapshot_fields(
    raw: dict[str, Any],
    *,
    asset_type: AssetType,
    expected_codes: list[str],
    names: dict[str, str] | None = None,
) -> None:
    data = raw.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("timestamp"), int):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "snapshot source timestamp is missing"
        )
    items = data_items(raw)
    by_code = {item.get("thscode"): item for item in items}
    missing_codes = [code for code in expected_codes if code not in by_code]
    if missing_codes:
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE,
            f"snapshot is missing requested symbols: {', '.join(missing_codes)}",
        )
    required = {
        "last_price",
        "price_change",
        "price_change_ratio_pct",
        "open_price",
        "high_price",
        "low_price",
        "volume",
        "turnover",
    }
    for code in expected_codes:
        missing = sorted(field for field in required if by_code[code].get(field) is None)
        if missing:
            raise HithinkProviderError(
                ErrorCategory.DATA_INCOMPLETE,
                f"snapshot {code} is missing fields: {', '.join(missing)}",
            )
    validate_records(normalize_snapshot(raw, asset_type=asset_type, names=names))


def validate_timestamped_data(raw: dict[str, Any]) -> None:
    data = raw.get("data")
    if not isinstance(data, dict):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "response data is not an object"
        )
    if not isinstance(data.get("timestamp"), int):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "response timestamp is missing"
        )
    if not isinstance(data.get("item"), list):
        raise HithinkProviderError(
            ErrorCategory.DATA_INCOMPLETE, "response item is not an array"
        )


def resolve_symbol(
    provider: HithinkProvider,
    ticker: str,
    expected_name: str,
    asset_type: str,
) -> dict[str, Any]:
    attempts = [ticker, expected_name]
    candidates: dict[str, dict[str, Any]] = {}
    for query in attempts:
        raw = provider.search_symbols(query, asset_type=asset_type, limit=50)
        for item in search_items(raw):
            if item.get("asset_type") == asset_type and item.get("thscode"):
                candidates[item["thscode"]] = item
        exact = [
            item
            for item in candidates.values()
            if str(item.get("thscode", "")).split(".", 1)[0] == ticker
            and item.get("name") == expected_name
        ]
        if len(exact) == 1:
            return exact[0]
    code_matches = [
        item
        for item in candidates.values()
        if str(item.get("thscode", "")).split(".", 1)[0] == ticker
    ]
    if len(code_matches) == 1:
        return code_matches[0]
    name_matches = [item for item in candidates.values() if item.get("name") == expected_name]
    if len(name_matches) == 1:
        return name_matches[0]
    raise HithinkProviderError(
        ErrorCategory.INVALID_DATA,
        f"symbol cannot be uniquely resolved: ticker={ticker}, name={expected_name}",
    )


def resolve_sector(
    catalog_items: list[dict[str, Any]], source_code: str, expected_name: str
) -> dict[str, Any]:
    exact = [item for item in catalog_items if item.get("name") == expected_name]
    if len(exact) == 1:
        return exact[0]
    partial = [item for item in catalog_items if expected_name in str(item.get("name", ""))]
    if len(partial) == 1:
        return partial[0]
    raise HithinkProviderError(
        ErrorCategory.INVALID_DATA,
        f"sector {source_code}/{expected_name} cannot be uniquely mapped to a Hithink .TI code",
    )


def resolve_index(
    provider: HithinkProvider, ticker: str, expected_name: str
) -> dict[str, Any]:
    """Resolve by metadata, then by bounded actual suffix probes when metadata is empty."""
    try:
        return resolve_symbol(provider, ticker, expected_name, "a-share-index")
    except HithinkProviderError as exc:
        if exc.category not in {ErrorCategory.EMPTY_DATA, ErrorCategory.INVALID_DATA}:
            raise

    matches: list[str] = []
    for suffix in ("SH", "SZ"):
        candidate = f"{ticker}.{suffix}"
        try:
            raw = provider.index_snapshot([candidate])
            items = data_items(raw)
        except HithinkProviderError as exc:
            if exc.category in {
                ErrorCategory.EMPTY_DATA,
                ErrorCategory.INVALID_DATA,
                ErrorCategory.UNSUPPORTED,
            }:
                continue
            raise
        if any(item.get("thscode") == candidate for item in items):
            matches.append(candidate)
    if len(matches) != 1:
        raise HithinkProviderError(
            ErrorCategory.INVALID_DATA,
            f"index cannot be uniquely resolved by actual suffix probes: {ticker}/{expected_name}",
        )
    return {
        "thscode": matches[0],
        "ticker": ticker,
        "name": expected_name,
        "asset_type": "a-share-index",
        "resolution": "actual_snapshot_suffix_probe",
    }


def short_window_ms() -> tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=370)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def long_window_ms() -> tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=3650)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def capture(
    checks: list[Check],
    name: str,
    action: Callable[[], Any],
    *,
    unsupported_on: set[ErrorCategory] | None = None,
) -> Any | None:
    try:
        value = action()
    except HithinkProviderError as exc:
        if unsupported_on and exc.category in unsupported_on:
            add(checks, Outcome.UNSUPPORTED, name, f"{exc.category.value}; code={exc.provider_code}")
        elif exc.category in {ErrorCategory.AUTH_ERROR, ErrorCategory.NETWORK_ERROR}:
            add(checks, Outcome.UNKNOWN, name, exc.category.value)
        else:
            add(checks, Outcome.FAIL, name, f"{exc.category.value}; code={exc.provider_code}")
        return None
    except Exception as exc:  # The failure remains visible; it is not swallowed.
        add(checks, Outcome.FAIL, name, f"{type(exc).__name__}: {exc}")
        return None
    add(checks, Outcome.PASS, name)
    return value


def probe_invalid_auth(checks: list[Check]) -> None:
    provider = HithinkProvider(api_key="task001-invalid-auth-probe", timeout=10)
    try:
        provider.search_symbols("600487", asset_type="a-share", limit=1)
    except HithinkProviderError as exc:
        if exc.category == ErrorCategory.AUTH_ERROR:
            add(checks, Outcome.PASS, "API authentication error mapping", "AUTH_ERROR")
        else:
            add(checks, Outcome.FAIL, "API authentication error mapping", exc.category.value)
    else:
        add(checks, Outcome.FAIL, "API authentication error mapping", "invalid key was accepted")


def probe_invalid_symbol(provider: HithinkProvider, checks: list[Check]) -> None:
    try:
        raw = provider.stock_snapshot(["999999.SH"])
        data_items(raw)
    except HithinkProviderError as exc:
        if exc.category in {ErrorCategory.EMPTY_DATA, ErrorCategory.INVALID_DATA}:
            add(
                checks,
                Outcome.PASS,
                "invalid symbol rejection",
                f"{exc.category.value}; code={exc.provider_code}",
            )
        else:
            add(checks, Outcome.FAIL, "invalid symbol rejection", exc.category.value)
    else:
        add(checks, Outcome.FAIL, "invalid symbol rejection", "unexpected non-empty response")


def report_blocked(checks: list[Check]) -> None:
    add(checks, Outcome.UNKNOWN, "stock quote", "BLOCKED_BY_API_KEY")
    add(checks, Outcome.UNKNOWN, "daily OHLCV", "BLOCKED_BY_API_KEY")
    add(checks, Outcome.UNKNOWN, "index quote/history", "BLOCKED_BY_API_KEY")
    add(checks, Outcome.UNKNOWN, "sector quote/history/constituents", "BLOCKED_BY_API_KEY")
    add(checks, Outcome.UNKNOWN, "ETF quote/history/profile", "BLOCKED_BY_API_KEY")
    add(checks, Outcome.UNKNOWN, "auction", "BLOCKED_BY_API_KEY")
    add(checks, Outcome.UNKNOWN, "special data", "BLOCKED_BY_API_KEY")
    add(
        checks,
        Outcome.UNSUPPORTED,
        "15m direct kline (official contract)",
        "actual rejection probe BLOCKED_BY_API_KEY",
    )
    add(
        checks,
        Outcome.UNSUPPORTED,
        "60m direct kline (official contract)",
        "actual rejection probe BLOCKED_BY_API_KEY",
    )


def run_online(provider: HithinkProvider, checks: list[Check]) -> None:
    sample_dir = PROJECT_ROOT / "data/samples/hithink"
    normalized_dir = PROJECT_ROOT / "data/samples/normalized"
    start, end = short_window_ms()

    stocks: list[dict[str, Any]] = []
    for ticker, name in STOCKS:
        resolved = capture(
            checks,
            f"resolve stock {ticker} {name}",
            lambda ticker=ticker, name=name: resolve_symbol(
                provider, ticker, name, "a-share"
            ),
        )
        if resolved:
            stocks.append(resolved)
    if stocks:
        stock_codes = [item["thscode"] for item in stocks]
        snapshots: list[dict[str, Any]] = []
        for attempt in range(3):
            raw = capture(
                checks,
                f"stock quote stability {attempt + 1}/3",
                lambda: provider.stock_snapshot(stock_codes),
            )
            if raw:
                capture(
                    checks,
                    f"stock quote fields {attempt + 1}/3",
                    lambda raw=raw: validate_snapshot_fields(
                        raw,
                        asset_type=AssetType.STOCK,
                        expected_codes=stock_codes,
                        names={item["thscode"]: item["name"] for item in stocks},
                    ),
                )
                snapshots.append(raw)
            if attempt < 2:
                time.sleep(0.5)
        if snapshots:
            save_raw_response(sample_dir / "600487_quote.json", snapshots[-1])

        for stock in stocks:
            raw = capture(
                checks,
                f"daily OHLCV {stock['ticker']} {stock['name']}",
                lambda stock=stock: provider.stock_history(
                    stock["thscode"], start=start, end=end, adjust="none"
                ),
            )
            if raw:
                records = capture(
                    checks,
                    f"normalize/validate stock {stock['ticker']}",
                    lambda raw=raw, stock=stock: normalize_historical(
                        raw,
                        symbol=stock["thscode"],
                        name=stock.get("name"),
                        asset_type=AssetType.STOCK,
                    ),
                )
                if records:
                    capture(checks, f"validate stock {stock['ticker']}", lambda: validate_records(records))
                    if stock["ticker"] == "600487":
                        save_raw_response(sample_dir / "600487_daily.json", raw)
                        save_normalized(
                            normalized_dir / "600487_daily.json",
                            [record.to_dict() for record in records],
                        )

        long_start, long_end = long_window_ms()
        long_raw = capture(
            checks,
            "stock historical 10-year request window",
            lambda: provider.stock_history(
                stocks[0]["thscode"], start=long_start, end=long_end, adjust="none"
            ),
        )
        if long_raw:
            items = data_items(long_raw)
            add(
                checks,
                Outcome.PASS,
                "stock historical available length",
                f"rows={len(items)}; first_date_ms={items[0].get('date_ms')}; last_date_ms={items[-1].get('date_ms')}",
            )

        for interval in ("15m", "60m"):
            capture(
                checks,
                f"{interval} direct kline actual API probe",
                lambda interval=interval: data_items(
                    provider.stock_history(
                        stocks[0]["thscode"],
                        start=start,
                        end=end,
                        interval=interval,
                        adjust="none",
                    )
                ),
                unsupported_on={ErrorCategory.INVALID_DATA, ErrorCategory.UNSUPPORTED},
            )

    indices: list[dict[str, Any]] = []
    for ticker, name in INDICES:
        resolved = capture(
            checks,
            f"resolve index {ticker} {name}",
            lambda ticker=ticker, name=name: resolve_index(provider, ticker, name),
        )
        if resolved:
            indices.append(resolved)
    if indices:
        raw = capture(
            checks,
            "index quote batch",
            lambda: provider.index_snapshot([item["thscode"] for item in indices]),
        )
        if raw:
            capture(
                checks,
                "index quote fields",
                lambda: validate_snapshot_fields(
                    raw,
                    asset_type=AssetType.INDEX,
                    expected_codes=[item["thscode"] for item in indices],
                    names={item["thscode"]: item["name"] for item in indices},
                ),
            )
            save_raw_response(sample_dir / "000905_index.json", raw)
        for index in indices:
            history = capture(
                checks,
                f"index daily {index['ticker']} {index['name']}",
                lambda index=index: provider.index_history(
                    index["thscode"], start=start, end=end
                ),
            )
            if history and str(index.get("thscode", "")).split(".", 1)[0] == "000905":
                save_raw_response(sample_dir / "000905_index_daily.json", history)
                records = normalize_historical(
                    history,
                    symbol=index["thscode"],
                    name=index.get("name"),
                    asset_type=AssetType.INDEX,
                )
                capture(checks, "normalize/validate index 000905", lambda: validate_records(records))
                save_normalized(
                    normalized_dir / "000905_index_daily.json",
                    [record.to_dict() for record in records],
                )

    catalog_raw = capture(checks, "sector industry catalog", lambda: provider.index_catalog("industry"))
    sectors: list[tuple[str, dict[str, Any]]] = []
    if catalog_raw:
        catalog = data_items(catalog_raw)
        for source_code, name in SECTORS:
            try:
                resolved = resolve_sector(catalog, source_code, name)
            except HithinkProviderError as exc:
                if exc.category == ErrorCategory.INVALID_DATA:
                    add(
                        checks,
                        Outcome.UNKNOWN,
                        f"resolve sector {source_code} {name}",
                        "official catalog mapping is not unique",
                    )
                    resolved = None
                else:
                    add(
                        checks,
                        Outcome.FAIL,
                        f"resolve sector {source_code} {name}",
                        exc.category.value,
                    )
                    resolved = None
            else:
                add(checks, Outcome.PASS, f"resolve sector {source_code} {name}")
            if resolved:
                sectors.append((source_code, resolved))
    if sectors:
        raw = capture(
            checks,
            "sector quote batch",
            lambda: provider.index_snapshot([item["thscode"] for _, item in sectors]),
        )
        if raw:
            capture(
                checks,
                "sector quote fields",
                lambda: validate_snapshot_fields(
                    raw,
                    asset_type=AssetType.SECTOR,
                    expected_codes=[item["thscode"] for _, item in sectors],
                    names={item["thscode"]: item["name"] for _, item in sectors},
                ),
            )
            save_raw_response(sample_dir / "BK0448_sector.json", raw)
        for source_code, sector in sectors:
            capture(
                checks,
                f"sector constituents {source_code} {sector['name']}",
                lambda sector=sector: data_items(
                    provider.index_constituents(sector["thscode"])
                ),
            )
            history = capture(
                checks,
                f"sector daily {source_code} {sector['name']}",
                lambda sector=sector: provider.index_history(
                    sector["thscode"], start=start, end=end
                ),
            )
            if history and source_code == "BK0448":
                save_raw_response(sample_dir / "BK0448_sector_daily.json", history)
                records = normalize_historical(
                    history,
                    symbol=sector["thscode"],
                    name=sector.get("name"),
                    asset_type=AssetType.SECTOR,
                )
                capture(checks, "normalize/validate sector BK0448", lambda: validate_records(records))
                save_normalized(
                    normalized_dir / "BK0448_sector_daily.json",
                    [record.to_dict() for record in records],
                )

    etf = capture(
        checks,
        "resolve ETF 510300",
        lambda: resolve_symbol(provider, ETF_QUERY[0], ETF_QUERY[1], "fund-etf"),
    )
    if etf:
        profile = capture(checks, "ETF basic profile", lambda: provider.fund_profile(etf["thscode"]))
        quote = capture(checks, "ETF quote", lambda: provider.fund_snapshot(etf["thscode"]))
        history = capture(
            checks,
            "ETF daily history",
            lambda: provider.fund_history(etf["thscode"], start=start, end=end),
        )
        if profile:
            save_raw_response(sample_dir / "510300_etf_profile.json", profile)
        if quote:
            capture(
                checks,
                "ETF quote fields",
                lambda: validate_snapshot_fields(
                    quote,
                    asset_type=AssetType.ETF,
                    expected_codes=[etf["thscode"]],
                    names={etf["thscode"]: etf["name"]},
                ),
            )
            save_raw_response(sample_dir / "510300_etf_quote.json", quote)
        if history:
            save_raw_response(sample_dir / "510300_etf_daily.json", history)
            records = normalize_historical(
                history,
                symbol=etf["thscode"],
                name=etf.get("name"),
                asset_type=AssetType.ETF,
            )
            capture(checks, "normalize/validate ETF 510300", lambda: validate_records(records))
            save_normalized(
                normalized_dir / "510300_etf_daily.json",
                [record.to_dict() for record in records],
            )

    if stocks:
        auction = capture(
            checks,
            "auction final snapshot",
            lambda: provider.auction_snapshot([item["thscode"] for item in stocks]),
        )
        if auction:
            capture(checks, "auction response fields", lambda: validate_timestamped_data(auction))
            save_raw_response(sample_dir / "600487_auction.json", auction)
    calendar = capture(checks, "trading calendar", lambda: provider.trading_days())
    last_trading_day_ms = None
    if calendar:
        calendar_items = capture(checks, "trading calendar fields", lambda: data_items(calendar))
        if calendar_items:
            last_trading_day_ms = max(item["date_ms"] for item in calendar_items)
        save_raw_response(sample_dir / "trading_calendar.json", calendar)
    limit_up = capture(
        checks,
        "special limit-up pool",
        lambda: provider.special_data(
            "limit-up-pool", date_ms=last_trading_day_ms, page=1, size=5
        ),
    )
    if limit_up:
        capture(checks, "special limit-up fields", lambda: data_items(limit_up))
        save_raw_response(sample_dir / "special_limit_up.json", limit_up)
    for capability, label, filename in (
        ("limit-down-pool", "special limit-down pool", "special_limit_down.json"),
        ("limit-break-pool", "special limit-break pool", "special_limit_break.json"),
    ):
        pool = capture(
            checks,
            label,
            lambda capability=capability: provider.special_data(
                capability, date_ms=last_trading_day_ms, page=1, size=5
            ),
        )
        if pool:
            capture(checks, f"{label} response fields", lambda pool=pool: validate_timestamped_data(pool))
            save_raw_response(sample_dir / filename, pool)
    anomaly = capture(
        checks,
        "special anomaly capability",
        lambda: provider.special_data("anomaly-analysis-list"),
    )
    if anomaly:
        capture(checks, "special anomaly response fields", lambda: validate_timestamped_data(anomaly))
        save_raw_response(sample_dir / "special_anomaly.json", anomaly)
    dragon_tiger = capture(
        checks,
        "special dragon-tiger capability",
        lambda: provider.special_data("dragon-tiger-list", board_type="all"),
    )
    if dragon_tiger:
        save_raw_response(sample_dir / "special_dragon_tiger.json", dragon_tiger)

    probe_invalid_symbol(provider, checks)


def print_summary(checks: list[Check]) -> None:
    counts = Counter(check.outcome for check in checks)
    print("\nSummary")
    for outcome in Outcome:
        print(f"{outcome.value}: {counts[outcome]}")


def main() -> int:
    checks: list[Check] = []
    provider = HithinkProvider(dotenv_path=str(PROJECT_ROOT / ".env"))
    probe_invalid_auth(checks)
    if not provider.configured:
        print("\nBLOCKED_BY_API_KEY: configure HITHINK_FINANCE_API_KEY in the environment or .env")
        report_blocked(checks)
        print_summary(checks)
        return 2
    run_online(provider, checks)
    print_summary(checks)
    return 1 if any(check.outcome == Outcome.FAIL for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
