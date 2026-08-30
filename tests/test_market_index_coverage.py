from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.quality import RiskFeatureContract
from trend_monitor.registry import (
    InstrumentRegistry,
    MappingConfidence,
    MappingStatus,
    MappingType,
    ProviderMapping,
)
from trend_monitor.risk_input import (
    MARKET_INDEXES,
    RiskInputAssembler,
    RiskInputSnapshotStore,
    build_market_risk_group,
    market_coverage_status,
)
from trend_monitor.schemas import AssetType, PreflightStatus
from tests.test_risk_input_assembly import result_for, source_records


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_INDEXES = {
    "index.sse_composite": "000001.SH",
    "index.sse50": "000016.SH",
    "index.csi300": "000300.SH",
    "index.csi_free_float": "000902.SH",
    "index.chinext": "399006.SZ",
    "index.csi1000": "000852.SH",
}


class FakeRegistry:
    def __init__(self, mapping: ProviderMapping) -> None:
        self.mapping = mapping

    def resolve(self, instrument_id: str, provider: str) -> ProviderMapping:
        return replace(self.mapping, instrument_id=instrument_id, provider=provider)


class MarketIndexCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = InstrumentRegistry.load(ROOT / "config" / "instruments.json")
        cls.assembler = RiskInputAssembler(
            RiskFeatureContract.load(ROOT / "config" / "risk_feature_contract.json")
        )
        cls.as_of = datetime(2026, 8, 28, 16, 0, tzinfo=SHANGHAI)

    def test_six_new_mappings_load_and_resolve_as_verified_exact(self):
        for instrument_id, symbol in NEW_INDEXES.items():
            with self.subTest(instrument_id=instrument_id):
                mapping = self.registry.resolve(instrument_id, "longbridge")
                self.assertEqual(mapping.provider_symbol, symbol)
                self.assertEqual(mapping.mapping_type, MappingType.EXACT)
                self.assertEqual(mapping.confidence, MappingConfidence.HIGH)
                self.assertEqual(mapping.status, MappingStatus.VERIFIED)

    def test_six_indexes_apply_existing_system_bar_and_field_contract(self):
        for instrument_id, symbol in NEW_INDEXES.items():
            for period, count in (("15m", 16), ("60m", 4)):
                with self.subTest(instrument_id=instrument_id, period=period):
                    records = tuple(
                        replace(
                            item,
                            instrument_id=instrument_id,
                            symbol=symbol,
                            asset_type=AssetType.INDEX,
                            name=self.registry.get_instrument(instrument_id).display_name,
                        )
                        for item in source_records(period)
                    )
                    risk = self.assembler.assemble_minute(
                        result_for(period, records),
                        asset_type=AssetType.INDEX,
                        period=period,
                        as_of=self.as_of,
                        trading_date="2026-08-28",
                    )
                    self.assertEqual(len(risk.system_bars), count)
                    self.assertEqual(risk.system_bars[-1].transformation, "MERGE_CLOSING_BUCKET")
                    self.assertEqual(
                        risk.preflight_status, PreflightStatus.PASS_WITH_DEGRADATION
                    )
                    self.assertIn(
                        "index_volume_signal",
                        {item.feature_name for item in risk.disabled_features},
                    )

    def test_unverified_mapping_is_never_ready(self):
        mapping = ProviderMapping(
            instrument_id="index.sse_composite",
            provider="longbridge",
            provider_symbol="000001.SH",
            provider_name="上证指数",
            mapping_type=MappingType.EXACT,
            confidence=MappingConfidence.HIGH,
            status=MappingStatus.CANDIDATE,
            notes="test candidate",
        )
        bundles = {
            item: SimpleNamespace(preflight_status=PreflightStatus.PASS_WITH_DEGRADATION)
            for item in MARKET_INDEXES
        }
        paths = {item: f"/tmp/{item}.json" for item in MARKET_INDEXES}
        group = build_market_risk_group(
            as_of=self.as_of.isoformat(),
            registry=FakeRegistry(mapping),
            bundles=bundles,
            snapshot_paths=paths,
        )
        self.assertTrue(all(item.status == "UNAVAILABLE" for item in group.entries))
        self.assertEqual(market_coverage_status(group), "NO")

    def test_partial_and_full_market_bundle_status_and_snapshot(self):
        degraded = SimpleNamespace(preflight_status=PreflightStatus.PASS_WITH_DEGRADATION)
        partial = build_market_risk_group(
            as_of=self.as_of.isoformat(),
            registry=self.registry,
            bundles={MARKET_INDEXES[0]: degraded},
            snapshot_paths={MARKET_INDEXES[0]: "/evidence/index.json"},
        )
        self.assertEqual(market_coverage_status(partial), "PARTIAL_READY")
        bundles = {item: degraded for item in MARKET_INDEXES}
        paths = {item: f"/evidence/{item}.json" for item in MARKET_INDEXES}
        full = build_market_risk_group(
            as_of=self.as_of.isoformat(),
            registry=self.registry,
            bundles=bundles,
            snapshot_paths=paths,
        )
        self.assertEqual(market_coverage_status(full), "FULL_READY")
        self.assertTrue(all(item.status == "DEGRADED" for item in full.entries))
        with TemporaryDirectory() as directory:
            store = RiskInputSnapshotStore(directory)
            path = store.save_group(full)
            self.assertEqual(store.load(path), full.to_dict())


if __name__ == "__main__":
    unittest.main()
