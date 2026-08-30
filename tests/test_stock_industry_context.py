from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import TrendMonitorError
from trend_monitor.industry_context import (
    IndustryReferenceObservation,
    StockIndustryContextEngine,
    StockIndustryContextRules,
    StockIndustryContextStore,
    render_stock_industry_context_report,
)
from trend_monitor.registry import InstrumentRegistry
from trend_monitor.schemas import (
    AnalysisPeriod,
    AssetType,
    PreflightStatus,
    RiskBar,
    RiskInput,
    RiskInputDataStatus,
    RiskSourceTrace,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_RULES = StockIndustryContextRules.load(ROOT / "config" / "stock_industry_context_rules.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")
STOCK = "stock.hengtong_optic"
INDUSTRY = "sector.communication_equipment"
END = datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI)


def direct_rules(*, mapping_type: str = "EXACT") -> StockIndustryContextRules:
    raw = deepcopy(BASE_RULES.raw)
    raw["benchmarks"][STOCK]["minute_60m_capability"] = "DIRECT"
    raw["benchmarks"][STOCK]["mapping_type"] = mapping_type
    raw["benchmarks"][STOCK]["unavailable_reason"] = None
    rules = StockIndustryContextRules(raw)
    rules.validate()
    return rules


def stock_result(
    *, stock_return: float = -0.02, market_light: str = "ORANGE", relative_weakness: bool = False
):
    return {
        "rules_version": "stock_60m_risk_v0.1",
        "instrument_id": STOCK,
        "period_end": END.isoformat(),
        "risk_score": 2,
        "risk_light": "YELLOW",
        "current_return": stock_return,
        "relative_weakness": relative_weakness,
        "market_context": {
            "market_risk_light": market_light,
            "market_median_return": -0.01,
            "broad_selloff_resonance": market_light in {"ORANGE", "RED"},
        },
    }


def industry_input(close: float = 98.0, *, trusted: bool = True) -> RiskInput:
    start = END - timedelta(hours=1)
    bar = RiskBar(
        instrument_id=INDUSTRY,
        period="60m",
        start=int(start.timestamp() * 1000),
        end=int(END.timestamp() * 1000),
        open=1000,
        high=2000,
        low=1,
        close=close,
        volume=999999999,
        turnover=999999999,
        source_provider="hithink",
        provider_symbol="881129.TI",
        source_bar_ids=("hithink:881129.TI:60m:current",),
        source_raw_paths=("/raw/industry.json",),
        fetched_at="2026-08-28T15:01:00+08:00",
        source_timestamp=int(start.timestamp() * 1000),
        transformation="DIRECT_NORMALIZED",
        quality_status="DIRECT_NORMALIZED",
        field_quality={
            "open": "UNTRUSTED",
            "high": "UNTRUSTED",
            "low": "UNTRUSTED",
            "close": "TRUSTED" if trusted else "UNTRUSTED",
            "volume": "UNTRUSTED",
            "turnover": "UNTRUSTED",
        },
    )
    return RiskInput(
        instrument_id=INDUSTRY,
        asset_type=AssetType.SECTOR,
        analysis_period=AnalysisPeriod.MIN_60,
        as_of=END.isoformat(),
        trading_date=END.date().isoformat(),
        source_provider="hithink",
        source_trace=RiskSourceTrace(
            requested_provider="hithink",
            actual_provider="hithink",
            provider_symbol="881129.TI",
            fallback_used=False,
            fallback_reason=None,
            raw_path="/raw/industry.json",
            fetched_at="2026-08-28T15:01:00+08:00",
            source_timestamp=bar.source_timestamp,
        ),
        system_bars=(bar,),
        feature_inputs=(),
        disabled_features=(),
        degraded_features=(),
        data_status=RiskInputDataStatus.VALID,
        preflight_status=PreflightStatus.PASS,
        last_completed_bar_end=END.isoformat(),
        data_fetched_at="2026-08-28T15:01:00+08:00",
        layer_role="industry_context_auxiliary_only",
    )


def history(*, prior: float = 99.0, two_ago: float = 100.0, extreme_current: bool = False):
    result = []
    start = date(2026, 5, 1)
    close = 100.0
    for day_index in range(60):
        day = start + timedelta(days=day_index)
        for period_index, label in enumerate(("10:30", "11:30", "14:00", "15:00")):
            end = datetime.combine(day, datetime.strptime(label, "%H:%M").time(), tzinfo=SHANGHAI)
            relative = -0.01 if (day_index * 4 + period_index) % 5 == 0 else 0.0
            result.append(
                IndustryReferenceObservation(
                    instrument_id=STOCK,
                    trading_date=day.isoformat(),
                    period_end=end.isoformat(),
                    industry_close=close,
                    stock_return=relative,
                    industry_return=0.0,
                    source_industry_risk_input_id=f"industry:{end.isoformat()}",
                )
            )
    for label, value in (("13:00", two_ago), ("14:00", prior)):
        end = datetime.combine(END.date(), datetime.strptime(label, "%H:%M").time(), tzinfo=SHANGHAI)
        result.append(
            IndustryReferenceObservation(
                instrument_id=STOCK,
                trading_date=END.date().isoformat(),
                period_end=end.isoformat(),
                industry_close=value,
                stock_return=0.0,
                industry_return=0.0,
                source_industry_risk_input_id=f"industry:{end.isoformat()}",
            )
        )
    return tuple(result)


def evaluate(engine, *, stock=None, close=98.0, hist=None):
    return engine.evaluate(
        instrument_id=STOCK,
        stock_60m_result=stock or stock_result(),
        industry_risk_input=industry_input(close),
        history=hist or history(),
        source_stock_60m_result_id="stock-result",
        source_market_60m_result_id="market-result",
        source_industry_risk_input_id="industry-input",
        source_benchmark_evidence_id="mapping-evidence",
    )


class MappingTests(unittest.TestCase):
    def test_exact_benchmarks_and_wus_is_pcb_not_semiconductor(self):
        hengtong = BASE_RULES.benchmark(STOCK)
        wus = BASE_RULES.benchmark("stock.wus_printed_circuit")
        self.assertEqual((hengtong.mapping_type, hengtong.provider_symbol), ("EXACT", "881129.TI"))
        self.assertEqual((wus.mapping_type, wus.provider_symbol), ("EXACT", "884092.TI"))
        self.assertEqual(wus.industry_name, "印制电路板")
        self.assertNotIn("semiconductor", wus.industry_id)

    def test_proxy_mapping_is_supported_without_becoming_exact(self):
        self.assertEqual(direct_rules(mapping_type="PROXY").benchmark(STOCK).mapping_type, "PROXY")

    def test_longbridge_sector_is_unmapped_and_no_symbol_is_guessed(self):
        registry = InstrumentRegistry.load(ROOT / "config" / "instruments.json")
        mapping = registry.resolve("sector.printed_circuit_board", "longbridge")
        self.assertEqual(mapping.mapping_type.value, "UNMAPPED")
        self.assertIsNone(mapping.provider_symbol)


class IndustryContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = StockIndustryContextEngine(direct_rules())

    def test_no_minute_benchmark_is_explicitly_unavailable(self):
        result = StockIndustryContextEngine(BASE_RULES).evaluate(
            instrument_id=STOCK,
            stock_60m_result=stock_result(),
            industry_risk_input=None,
            history=(),
            source_stock_60m_result_id="stock-result",
            source_market_60m_result_id="market-result",
            source_industry_risk_input_id=None,
            source_benchmark_evidence_id="mapping-evidence",
        )
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.unavailable_reason, "NO_DIRECT_MINUTE_BENCHMARK")
        self.assertIsNone(result.industry_return)
        self.assertEqual(result.joint_15m_flags, ())

    def test_15m_joint_weakness_is_not_emitted_without_direct_industry_15m(self):
        result = StockIndustryContextEngine(BASE_RULES).evaluate(
            instrument_id=STOCK,
            stock_60m_result=stock_result(),
            industry_risk_input=None,
            history=(),
            source_stock_60m_result_id="stock-result",
            source_market_60m_result_id="market-result",
            source_industry_risk_input_id=None,
            source_benchmark_evidence_id="mapping-evidence",
        )
        self.assertIsNone(result.industry_15m_internal)
        self.assertEqual(result.joint_15m_flags, ())
        self.assertEqual(result.data_quality["minute_15m_capability"], "UNSUPPORTED")

    def test_industry_persistent_weakness_stock_resonance_and_triple_resonance(self):
        result = evaluate(self.engine, hist=history(prior=99, two_ago=100), close=98)
        self.assertTrue(result.industry_persistent_weakness)
        self.assertTrue(result.stock_industry_weak_resonance)
        self.assertTrue(result.triple_weak_resonance)
        self.assertEqual(result.context_classification, "TRIPLE_WEAKNESS")

    def test_stock_weak_vs_industry_uses_asof_p10(self):
        result = evaluate(self.engine, stock=stock_result(stock_return=-0.03), close=99)
        self.assertEqual(result.relative_reference_status, "AVAILABLE")
        self.assertTrue(result.stock_weak_vs_industry)

    def test_stock_strong_vs_industry_and_industry_relative_strength(self):
        strong_stock = evaluate(self.engine, stock=stock_result(stock_return=0.01), close=98)
        self.assertTrue(strong_stock.stock_strong_against_industry)
        strong_industry = evaluate(self.engine, stock=stock_result(stock_return=-0.01), close=101)
        self.assertTrue(strong_industry.industry_relative_strength)

    def test_independent_weakness_decomposition(self):
        both = evaluate(
            self.engine,
            stock=stock_result(stock_return=-0.03, relative_weakness=True),
            close=99,
        )
        self.assertEqual(both.independent_weakness_decomposition, "INDUSTRY_AND_MARKET_INDEPENDENT")
        market_only = evaluate(
            self.engine,
            stock=stock_result(stock_return=-0.001, relative_weakness=True),
            close=99,
        )
        self.assertEqual(market_only.independent_weakness_decomposition, "MARKET_INDEPENDENT_ONLY")

    def test_untrusted_close_degrades_context(self):
        result = self.engine.evaluate(
            instrument_id=STOCK,
            stock_60m_result=stock_result(),
            industry_risk_input=industry_input(98, trusted=False),
            history=history(),
            source_stock_60m_result_id="stock-result",
            source_market_60m_result_id="market-result",
            source_industry_risk_input_id="industry-input",
            source_benchmark_evidence_id="mapping-evidence",
        )
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.unavailable_reason, "CLOSE_NOT_TRUSTED")

    def test_high_low_volume_do_not_change_flags(self):
        first = evaluate(self.engine)
        changed = industry_input(98)
        bar = changed.system_bars[0]
        from dataclasses import replace

        changed = replace(changed, system_bars=(replace(bar, high=999999, low=-999999, volume=1),))
        second = self.engine.evaluate(
            instrument_id=STOCK,
            stock_60m_result=stock_result(),
            industry_risk_input=changed,
            history=history(),
            source_stock_60m_result_id="stock-result",
            source_market_60m_result_id="market-result",
            source_industry_risk_input_id="industry-input",
            source_benchmark_evidence_id="mapping-evidence",
        )
        comparable = (
            "industry_return",
            "stock_industry_relative_return",
            "industry_persistent_weakness",
            "stock_industry_weak_resonance",
            "triple_weak_resonance",
            "stock_weak_vs_industry",
        )
        self.assertEqual(tuple(getattr(first, key) for key in comparable), tuple(getattr(second, key) for key in comparable))

    def test_deterministic_and_stock_score_immutable(self):
        source = stock_result()
        before = dict(source)
        first = evaluate(self.engine, stock=source)
        second = evaluate(self.engine, stock=source)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(source, before)
        self.assertEqual(first.stock_risk_score, source["risk_score"])
        self.assertTrue(first.stock_score_immutable)

    def test_lookahead_rejected(self):
        bad = list(history())
        last = bad[-1]
        bad.append(
            IndustryReferenceObservation(
                instrument_id=STOCK,
                trading_date=END.date().isoformat(),
                period_end=END.isoformat(),
                industry_close=99,
                stock_return=0,
                industry_return=0,
                source_industry_risk_input_id=last.source_industry_risk_input_id,
            )
        )
        with self.assertRaises(TrendMonitorError):
            evaluate(self.engine, hist=tuple(bad))

    def test_append_only_store(self):
        value = evaluate(self.engine)
        with TemporaryDirectory() as directory:
            store = StockIndustryContextStore(directory)
            first = store.save_result(value, render_stock_industry_context_report(value, stock_name="亨通光电"))
            second = store.save_result(value, render_stock_industry_context_report(value, stock_name="亨通光电"))
            self.assertNotEqual(first["json"], second["json"])
            self.assertTrue(Path(first["json"]).is_file())
            self.assertEqual(len((Path(directory) / "manifest.jsonl").read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
