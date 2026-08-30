from pathlib import Path
import unittest

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.registry import (
    InstrumentRegistry,
    MappingConfidence,
    MappingStatus,
    MappingType,
    ProviderMapping,
)


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "instruments.json"


class InstrumentRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = InstrumentRegistry.load(REGISTRY_PATH)

    def test_all_formal_monitoring_instruments_load(self):
        self.assertEqual(len(self.registry.instruments), 16)
        self.assertTrue(all(item.enabled for item in self.registry.instruments))

    def test_exact_hithink_mapping(self):
        mapping = self.registry.resolve("index.csi500", "HITHINK")
        self.assertEqual(mapping.provider_symbol, "000905.SH")
        self.assertEqual(mapping.mapping_type, MappingType.EXACT)
        self.assertEqual(mapping.confidence, MappingConfidence.HIGH)
        self.assertEqual(mapping.status, MappingStatus.VERIFIED)

    def test_coal_is_candidate_proxy_and_never_exact(self):
        mapping = self.registry.resolve("sector.coal", "hithink")
        self.assertEqual(mapping.provider_symbol, "881105.TI")
        self.assertEqual(mapping.mapping_type, MappingType.CANDIDATE_PROXY)
        self.assertEqual(mapping.confidence, MappingConfidence.LOW)
        self.assertNotEqual(mapping.mapping_type, MappingType.EXACT)

    def test_proxy_mapping_type_is_expressible_without_fake_registry_evidence(self):
        mapping = ProviderMapping(
            instrument_id="sector.coal",
            provider="future_provider",
            provider_symbol="future.proxy",
            provider_name="proxy example",
            mapping_type=MappingType.PROXY,
            confidence=MappingConfidence.MEDIUM,
            status=MappingStatus.CANDIDATE,
            notes="Schema-only test; not present in the production registry.",
        )
        self.assertEqual(mapping.mapping_type, MappingType.PROXY)

    def test_eastmoney_bk0437_is_provider_specific(self):
        mapping = self.registry.resolve("sector.coal", "eastmoney")
        self.assertEqual(mapping.provider_symbol, "BK0437")
        self.assertEqual(mapping.mapping_type, MappingType.EXACT)
        self.assertEqual(mapping.status, MappingStatus.NOT_CONFIGURED)

    def test_missing_provider_mapping_returns_unmapped_without_guessing(self):
        mapping = self.registry.resolve("sector.bank", "longbridge")
        self.assertIsNone(mapping.provider_symbol)
        self.assertEqual(mapping.mapping_type, MappingType.UNMAPPED)
        self.assertEqual(mapping.confidence, MappingConfidence.UNKNOWN)

    def test_longbridge_verified_mappings_are_exact(self):
        mapping = self.registry.resolve("stock.hengtong_optic", "longbridge")
        self.assertEqual(mapping.provider_symbol, "600487.SH")
        self.assertEqual(mapping.mapping_type, MappingType.EXACT)
        self.assertEqual(mapping.status, MappingStatus.VERIFIED)
        csi500 = self.registry.resolve("index.csi500", "longbridge")
        self.assertEqual(csi500.provider_symbol, "000905.SH")
        self.assertEqual(csi500.mapping_type, MappingType.EXACT)
        self.assertEqual(csi500.status, MappingStatus.VERIFIED)

    def test_task_007_longbridge_index_mappings_are_verified_exact(self):
        expected = {
            "index.sse_composite": "000001.SH",
            "index.sse50": "000016.SH",
            "index.csi300": "000300.SH",
            "index.csi_free_float": "000902.SH",
            "index.chinext": "399006.SZ",
            "index.csi1000": "000852.SH",
        }
        for instrument_id, symbol in expected.items():
            with self.subTest(instrument_id=instrument_id):
                mapping = self.registry.resolve(instrument_id, "longbridge")
                self.assertEqual(mapping.provider_symbol, symbol)
                self.assertEqual(mapping.mapping_type, MappingType.EXACT)
                self.assertEqual(mapping.confidence, MappingConfidence.HIGH)
                self.assertEqual(mapping.status, MappingStatus.VERIFIED)

    def test_unknown_internal_instrument_is_explicit(self):
        with self.assertRaises(TrendMonitorError) as raised:
            self.registry.resolve("stock.not_real", "hithink")
        self.assertEqual(raised.exception.category, ErrorCategory.UNMAPPED)


if __name__ == "__main__":
    unittest.main()
