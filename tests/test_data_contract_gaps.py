from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.cache import RawCache
from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.normalization import (
    evaluate_cn_volume_invariant,
    normalize_longbridge_candlesticks,
    normalize_volume_shares,
)
from trend_monitor.providers.longbridge.provider import LongbridgeProvider, _epoch_seconds
from trend_monitor.quality import RiskFeatureContract
from trend_monitor.registry import InstrumentRegistry
from trend_monitor.risk_input import RiskInputService, RiskInputSnapshotStore
from trend_monitor.schemas import AssetType, MarketRecord, SourceTrace
from trend_monitor.services import MarketDataService
from trend_monitor.validation import record_timestamp


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _risk_input(raw_path: Path, period: str, as_of: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "instrument_id": "unused",
        "asset_type": "stock",
        "analysis_period": {"1d": "DAILY", "60m": "60M", "15m": "15M"}[period],
        "as_of": as_of,
        "trading_date": as_of[:10],
        "source_provider": "longbridge",
        "source_trace": {
            "requested_provider": "longbridge",
            "actual_provider": "longbridge",
            "provider_symbol": "600487.SH",
            "fallback_used": False,
            "fallback_reason": None,
            "raw_path": str(raw_path),
            "fetched_at": "2026-09-04T07:03:00+00:00",
            "source_timestamp": 1788505200000,
        },
        "system_bars": [],
        "feature_inputs": [],
        "disabled_features": [],
        "degraded_features": [],
        "data_status": "VALID",
        "preflight_status": "PASS",
        "last_completed_bar_end": as_of,
        "data_fetched_at": "2026-09-04T07:03:00+00:00",
        "layer_role": "test",
        "in_progress_source_bars": [],
        "preflight_reasons": [],
    }


def _save_instrument(
    store: RiskInputSnapshotStore,
    raw_root: Path,
    instrument_id: str,
    as_of: str,
) -> str:
    risks = {}
    for field, period in (("daily", "1d"), ("risk_60m", "60m"), ("support_15m", "15m")):
        raw_path = raw_root / f"{instrument_id}-{period}.json"
        raw_path.write_text(json.dumps({"instrument": instrument_id, "period": period}), encoding="utf-8")
        risks[field] = _risk_input(raw_path, period, as_of)
    payload = {
        "schema_version": 1,
        "instrument_id": instrument_id,
        "asset_type": "stock",
        "as_of": as_of,
        **risks,
        "data_status": "VALID",
        "preflight_status": "PASS",
        "reasons": [],
    }
    return store._save("instrument", instrument_id, as_of, payload)


class CycleSnapshotContractTests(unittest.TestCase):
    def test_same_cycle_snapshot_bundle_identity_and_no_mid_cycle_change(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            raw_root = base / "raw"
            raw_root.mkdir()
            store = RiskInputSnapshotStore(base / "risk_inputs")
            as_of = "2026-09-04T15:00:00+08:00"
            paths = {
                instrument_id: _save_instrument(store, raw_root, instrument_id, as_of)
                for instrument_id in ("index.sse_composite", "stock.hengtong_optic")
            }
            first_path, first = store.save_cycle(
                cycle_id="test-cycle-1",
                analysis_as_of=as_of,
                provider_observed_at="2026-09-04T15:06:00+08:00",
                instrument_snapshot_paths=paths,
                raw_root=raw_root,
            )
            second_path, second = store.save_cycle(
                cycle_id="test-cycle-1",
                analysis_as_of=as_of,
                provider_observed_at="2026-09-04T15:06:00+08:00",
                instrument_snapshot_paths=paths,
                raw_root=raw_root,
            )
            self.assertEqual(first_path, second_path)
            self.assertEqual(first["cycle_raw_snapshot_id"], second["cycle_raw_snapshot_id"])
            self.assertEqual(len(first["members"]), 6)
            store.require_cycle_members(store.load_cycle(first_path), paths)

            third_path, third = store.save_cycle(
                cycle_id="test-cycle-1-replay",
                analysis_as_of=as_of,
                provider_observed_at="2026-09-04T15:06:00+08:00",
                instrument_snapshot_paths=paths,
                raw_root=raw_root,
            )
            self.assertNotEqual(first_path, third_path)
            self.assertNotEqual(
                first["cycle_raw_snapshot_id"], third["cycle_raw_snapshot_id"]
            )

            member = Path(first["members"][0]["raw_path"])
            member.write_text('{"changed":true}', encoding="utf-8")
            with self.assertRaises(TrendMonitorError) as raised:
                store.load_cycle(first_path)
            self.assertEqual(raised.exception.category, ErrorCategory.CACHE_INVALID)

    def test_market_stock_or_replay_snapshot_identity_mismatch_is_blocked(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            raw_root = base / "raw"
            raw_root.mkdir()
            store = RiskInputSnapshotStore(base / "risk_inputs")
            as_of = "2026-09-04T11:30:00+08:00"
            paths = {
                "index.sse_composite": _save_instrument(
                    store, raw_root, "index.sse_composite", as_of
                ),
                "stock.hengtong_optic": _save_instrument(
                    store, raw_root, "stock.hengtong_optic", as_of
                ),
            }
            cycle_path, _ = store.save_cycle(
                cycle_id="test-cycle-2",
                analysis_as_of=as_of,
                provider_observed_at="2026-09-04T11:33:00+08:00",
                instrument_snapshot_paths=paths,
                raw_root=raw_root,
            )
            different = _save_instrument(
                store, raw_root, "stock.hengtong_optic", as_of
            )
            with self.assertRaises(TrendMonitorError) as raised:
                store.require_cycle_members(
                    store.load_cycle(cycle_path),
                    {**paths, "stock.hengtong_optic": different},
                )
            self.assertEqual(raised.exception.category, ErrorCategory.DATA_CONFLICT)


class VolumeContractTests(unittest.TestCase):
    def test_dimensional_invariant_selects_factor_100(self) -> None:
        result = evaluate_cn_volume_invariant(
            volume_raw=1_007_900,
            turnover_raw=3_484_004_388,
            low=34.10,
            high=35.04,
        )
        self.assertFalse(result.factor_1_valid)
        self.assertTrue(result.factor_100_valid)

    def test_confirmed_units_normalize_to_shares(self) -> None:
        self.assertEqual(
            normalize_volume_shares(1_007_900, provider="longbridge", data_type="daily"),
            100_790_000,
        )
        self.assertEqual(
            normalize_volume_shares(1_000, provider="hithink", data_type="daily"),
            1_000,
        )
        self.assertEqual(
            normalize_volume_shares(123, provider="hithink", data_type="auction"),
            12_300,
        )

    def test_unknown_unit_must_not_auto_convert(self) -> None:
        self.assertIsNone(
            normalize_volume_shares(
                123,
                provider="longbridge",
                data_type="daily",
                raw_unit="UNKNOWN",
            )
        )
        self.assertIsNone(
            normalize_volume_shares(123, provider="unverified", data_type="daily")
        )


class ProductionDailyFallbackTests(unittest.TestCase):
    def test_hithink_daily_fallback_is_blocked_at_production_risk_boundary(self) -> None:
        class FailingProductionData:
            registry = InstrumentRegistry.load(ROOT / "config" / "instruments.json")

            def __init__(self):
                self.daily_fallbacks = None

            def get_daily(self, *args, fallback_providers=(), **kwargs):
                self.daily_fallbacks = tuple(fallback_providers)
                raise TrendMonitorError(ErrorCategory.NETWORK_ERROR, "Longbridge unavailable")

            def get_bars(self, *args, **kwargs):
                raise TrendMonitorError(ErrorCategory.DATA_INCOMPLETE, "minute unavailable")

        data = FailingProductionData()
        service = RiskInputService(
            data, RiskFeatureContract.load(ROOT / "config" / "risk_feature_contract.json")
        )
        bundle = service.build_bundle(
            "stock.hengtong_optic",
            as_of=datetime(2026, 9, 4, 15, 0, tzinfo=SHANGHAI),
            requested_provider="longbridge",
            fallback_providers=("hithink",),
        )
        self.assertEqual(data.daily_fallbacks, ())
        self.assertTrue(
            any(
                "HITHINK_DAILY_FALLBACK_BLOCKED_PENDING_CONTRACT_VALIDATION" in reason
                for reason in bundle.daily.preflight_reasons
            )
        )

    def test_research_can_still_explicitly_request_hithink_daily(self) -> None:
        class DailyProvider:
            def __init__(self, name: str, *, fails: bool = False):
                self.name = name
                self.fails = fails

            def get_daily(self, provider_symbol, asset_type, *, start, end):
                if self.fails:
                    raise TrendMonitorError(ErrorCategory.NETWORK_ERROR, "controlled")
                return {"data": {"item": [{"timestamp": start}]}}

            def normalize_daily(self, raw, instrument, mapping, source_trace):
                return [
                    MarketRecord(
                        symbol=mapping.provider_symbol,
                        name=instrument.display_name,
                        asset_type=instrument.asset_type,
                        timestamp=1_788_451_200_000,
                        open=10,
                        high=11,
                        low=9,
                        close=10,
                        volume=100,
                        turnover=1_000,
                        source=self.name,
                        period="1d",
                        source_trace=source_trace,
                        instrument_id=instrument.instrument_id,
                    )
                ]

        with TemporaryDirectory() as directory:
            service = MarketDataService(
                InstrumentRegistry.load(ROOT / "config" / "instruments.json"),
                (DailyProvider("longbridge", fails=True), DailyProvider("hithink")),
                RawCache(directory),
            )
            result = service.get_daily(
                "stock.hengtong_optic",
                "longbridge",
                start=1_788_451_200_000,
                end=1_788_451_200_000,
                fallback_providers=("hithink",),
            )
        self.assertTrue(result.metadata.fallback_used)
        self.assertEqual(result.metadata.actual_provider, "hithink")


class LongbridgeTimezoneContractTests(unittest.TestCase):
    def test_confirmed_sdk_process_local_naive_semantic_is_epoch_invariant(self) -> None:
        tokyo_value = datetime(2026, 9, 4, 16, 0)
        utc_value = datetime(2026, 9, 4, 7, 0)
        tokyo_epoch = _epoch_seconds(tokyo_value, host_timezone=ZoneInfo("Asia/Tokyo"))
        utc_epoch = _epoch_seconds(utc_value, host_timezone=timezone.utc)
        expected = int(datetime(2026, 9, 4, 15, 0, tzinfo=SHANGHAI).timestamp())
        self.assertEqual(tokyo_epoch, utc_epoch)
        self.assertEqual(tokyo_epoch, expected)

    def test_provider_emits_aware_shanghai_market_time(self) -> None:
        item = SimpleNamespace(
            close=1,
            open=1,
            low=1,
            high=1,
            volume=1,
            turnover=1,
            timestamp=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
            trade_session="Intraday",
        )
        raw = LongbridgeProvider._candlestick_item(item)
        self.assertEqual(raw["market_time"], "2026-09-04T15:00:00+08:00")
        self.assertEqual(raw["timestamp"], 1_788_505_200)

    def test_normalizer_rejects_epoch_market_time_conflict(self) -> None:
        trace = SourceTrace(
            provider="longbridge",
            provider_symbol="600487.SH",
            raw_path="data/raw/test.json",
            fetched_at="2026-09-04T07:03:00+00:00",
        )
        raw = {
            "data": {
                "item": [
                    {
                        "timestamp": 1_788_505_200,
                        "market_time": "2026-09-04T14:00:00+08:00",
                        "open": "1",
                        "high": "1",
                        "low": "1",
                        "close": "1",
                        "volume": 1,
                        "turnover": "1",
                    }
                ]
            }
        }
        with self.assertRaises(TrendMonitorError) as raised:
            normalize_longbridge_candlesticks(
                raw,
                instrument_id="stock.hengtong_optic",
                symbol="600487.SH",
                name="亨通光电",
                asset_type=AssetType.STOCK,
                period="60m",
                source_trace=trace,
            )
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_CONFLICT)

    def test_daily_and_intraday_market_times_are_shanghai_stable(self) -> None:
        daily = MarketRecord(
            symbol="600487.SH",
            name="亨通光电",
            asset_type=AssetType.STOCK,
            timestamp=int(datetime(2026, 9, 4, 0, 0, tzinfo=SHANGHAI).timestamp() * 1000),
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
            turnover=1,
            source="longbridge",
            period="1d",
        )
        self.assertEqual(record_timestamp(daily).isoformat(), "2026-09-04T00:00:00+08:00")
        for clock in ("09:30", "10:30", "11:30", "13:00", "14:00", "15:00"):
            with self.subTest(clock=clock):
                market = datetime.fromisoformat(f"2026-09-04T{clock}:00+08:00")
                tokyo = market.astimezone(ZoneInfo("Asia/Tokyo"))
                self.assertEqual(tokyo.astimezone(SHANGHAI), market)


if __name__ == "__main__":
    unittest.main()
