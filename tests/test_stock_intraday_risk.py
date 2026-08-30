from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.schemas import (
    AnalysisPeriod,
    AssetType,
    FeatureEligibility,
    FeatureInput,
    FeatureLineage,
    InternalClassification,
    InternalPeriodStatus,
    PreflightStatus,
    RiskBar,
    RiskInput,
    RiskInputDataStatus,
    RiskLight,
    RiskSourceTrace,
    StockIntradayMonitorResult,
)
from trend_monitor.stock_risk import (
    Stock15mInternalEngine,
    Stock60mRiskEngine,
    StockIntradayRiskRules,
    StockReferenceObservation,
    StockIntradayOutputStore,
    StockRiskInputStore,
    render_stock_intraday_report,
)


ROOT = Path(__file__).resolve().parents[1]
RULES = StockIntradayRiskRules.load(ROOT / "config" / "stock_intraday_risk_rules.json")
STOCK = "stock.hengtong_optic"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 28)


def epoch(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def risk_bar(period: str, start: datetime, end: datetime, close: float, *, noisy: float = 0) -> RiskBar:
    return RiskBar(
        instrument_id=STOCK,
        period=period,
        start=epoch(start),
        end=epoch(end),
        open=close + noisy,
        high=close + 1000 + noisy,
        low=close - 1000 - noisy,
        close=close,
        volume=123456 + noisy,
        turnover=987654321 + noisy,
        source_provider="longbridge",
        provider_symbol="600487.SH",
        source_bar_ids=(f"longbridge:600487.SH:{period}:{epoch(start)}",),
        source_raw_paths=("/raw/stock.json",),
        fetched_at="2026-08-28T15:01:00+08:00",
        source_timestamp=epoch(start),
        transformation="DIRECT_NORMALIZED",
        quality_status="DIRECT_NORMALIZED",
        field_quality={
            "open": "APPROXIMATE",
            "high": "APPROXIMATE",
            "low": "APPROXIMATE",
            "close": "TRUSTED",
            "volume": "APPROXIMATE",
            "turnover": "APPROXIMATE",
        },
    )


def risk_input(period: AnalysisPeriod, bars: tuple[RiskBar, ...], as_of: datetime) -> RiskInput:
    current = bars[-1]
    lineage = FeatureLineage(
        period=current.period,
        source_provider="longbridge",
        provider_symbol="600487.SH",
        source_bar_ids=current.source_bar_ids,
        source_raw_paths=current.source_raw_paths,
        transformation=current.transformation,
    )
    feature = FeatureInput(
        feature_name="current_period_close",
        value=current.close,
        field_source=("current.close",),
        quality={"close": "TRUSTED"},
        eligibility=FeatureEligibility.ENABLED,
        reason="ELIGIBLE",
        lineage=(lineage,),
    )
    return RiskInput(
        instrument_id=STOCK,
        asset_type=AssetType.STOCK,
        analysis_period=period,
        as_of=as_of.isoformat(),
        trading_date=DAY.isoformat(),
        source_provider="longbridge",
        source_trace=RiskSourceTrace(
            requested_provider="longbridge",
            actual_provider="longbridge",
            provider_symbol="600487.SH",
            fallback_used=False,
            fallback_reason=None,
            raw_path="/raw/stock.json",
            fetched_at="2026-08-28T15:01:00+08:00",
            source_timestamp=current.source_timestamp,
        ),
        system_bars=bars,
        feature_inputs=(feature,),
        disabled_features=(),
        degraded_features=(),
        data_status=RiskInputDataStatus.DEGRADED,
        preflight_status=PreflightStatus.PASS_WITH_DEGRADATION,
        last_completed_bar_end=as_of.isoformat(),
        data_fetched_at="2026-08-28T15:01:00+08:00",
        layer_role="risk_warning_and_detail_confirmation" if period is AnalysisPeriod.MIN_60 else "internal_structure_support_for_60m_only",
    )


def current_60(close: float, *, noisy: float = 0) -> RiskInput:
    end = datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI)
    return risk_input(
        AnalysisPeriod.MIN_60,
        (risk_bar("60m", end - timedelta(hours=1), end, close, noisy=noisy),),
        end,
    )


def history(*, previous: float = 100, two_ago: float = 102, relative_available: bool = True):
    values = []
    start = date(2026, 5, 1)
    close = 100.0
    for day_index in range(60):
        day = start + timedelta(days=day_index)
        for index, label in enumerate(("10:30", "11:30", "14:00", "15:00")):
            close *= 1.01 if (day_index * 4 + index) % 2 == 0 else 1 / 1.01
            end = datetime.combine(day, datetime.strptime(label, "%H:%M").time(), tzinfo=SHANGHAI)
            stock_return = 0.01 if (day_index * 4 + index) % 2 == 0 else -0.009900990099
            values.append(
                StockReferenceObservation(
                    instrument_id=STOCK,
                    trading_date=day.isoformat(),
                    period_end=end.isoformat(),
                    close=close,
                    stock_return=stock_return,
                    market_median_return=0.0 if relative_available else None,
                    source_stock_risk_input_id=f"stock:{end.isoformat()}",
                    source_market_60m_result_id=f"market:{end.isoformat()}" if relative_available else None,
                )
            )
    for label, value in (("13:00", two_ago), ("14:00", previous)):
        end = datetime.combine(DAY, datetime.strptime(label, "%H:%M").time(), tzinfo=SHANGHAI)
        values.append(
            StockReferenceObservation(
                instrument_id=STOCK,
                trading_date=DAY.isoformat(),
                period_end=end.isoformat(),
                close=value,
                stock_return=0.0,
                market_median_return=0.0 if relative_available else None,
                source_stock_risk_input_id=f"stock:{end.isoformat()}",
                source_market_60m_result_id=f"market:{end.isoformat()}" if relative_available else None,
            )
        )
    return tuple(values)


def market60(*, light="ORANGE", index_return=0.0, end=None):
    end = end or datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI)
    return {
        "rules_version": "market_60m_risk_v0.1",
        "last_completed_bar_end": end.isoformat(),
        "risk_score": {"GREEN": 0, "YELLOW": 2, "ORANGE": 5, "RED": 7}[light],
        "risk_light": light,
        "risk_direction": "FLAT",
        "broad_selloff_resonance": light in {"ORANGE", "RED"},
        "strong_broad_weakness": light == "RED",
        "index_states": [{"close_change_pct": index_return} for _ in range(8)],
    }


def market15(state="WEAKNESS_BROADENING", end=None):
    end = end or datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI)
    return {
        "rules_version": "market_15m_internal_v0.1",
        "60m_period_end": end.isoformat(),
        "market_internal_state": state,
    }


def current_15(closes: tuple[float, ...], *, as_of=None, noisy=0):
    start = datetime(2026, 8, 28, 14, 0, tzinfo=SHANGHAI)
    end = datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI)
    as_of = as_of or end
    bars = [risk_bar("15m", start - timedelta(minutes=15), start, 100, noisy=noisy)]
    for index, close in enumerate(closes):
        bars.append(
            risk_bar(
                "15m",
                start + timedelta(minutes=15 * index),
                start + timedelta(minutes=15 * (index + 1)),
                close,
                noisy=noisy,
            )
        )
    return risk_input(AnalysisPeriod.MIN_15, tuple(bars), as_of)


class Stock60mRiskTests(unittest.TestCase):
    def setUp(self):
        self.engine = Stock60mRiskEngine(RULES)

    def evaluate(self, close, *, hist=None, market=Ellipsis, internal=Ellipsis, noisy=0, previous=None):
        return self.engine.evaluate(
            current_60(close, noisy=noisy),
            history=hist if hist is not None else history(),
            market_60m_result=market60() if market is Ellipsis else market,
            market_15m_result=market15() if internal is Ellipsis else internal,
            source_stock_risk_input_id="stock-input",
            source_market_60m_result_id="market60" if market is not None else None,
            source_market_15m_result_id="market15" if internal is not None else None,
            previous_result=previous,
        )

    def test_persistent_shock_relative_and_resonance_make_red(self):
        result = self.evaluate(98)
        self.assertTrue(result.persistent_weakness)
        self.assertTrue(result.downside_shock)
        self.assertTrue(result.relative_weakness)
        self.assertTrue(result.market_resonance)
        self.assertEqual(result.risk_score, 5)
        self.assertEqual(result.risk_light, RiskLight.RED)

    def test_full_close_repair_and_score_floor(self):
        result = self.evaluate(100, hist=history(previous=90, two_ago=100), market=market60(light="GREEN"))
        self.assertEqual(result.repair_state.value, "FULL_CLOSE_REPAIR")
        self.assertEqual(result.risk_score, 0)

    def test_repair_attempt_does_not_offset(self):
        result = self.evaluate(95, hist=history(previous=90, two_ago=100), market=market60(light="GREEN"))
        self.assertEqual(result.repair_state.value, "REPAIR_ATTEMPT")
        self.assertEqual(result.score_components["full_close_repair_offset"], 0)

    def test_risk_light_thresholds(self):
        self.assertEqual([RULES.light(i)[0] for i in range(6)], ["GREEN", "YELLOW", "YELLOW", "ORANGE", "ORANGE", "RED"])

    def test_market_yellow_alone_is_not_resonance(self):
        value = market60(light="YELLOW")
        value["broad_selloff_resonance"] = False
        result = self.evaluate(99, market=value)
        self.assertFalse(result.market_resonance)

    def test_market_unavailable_degrades_but_does_not_block(self):
        result = self.evaluate(99, market=None)
        self.assertEqual(result.status, "READY")
        self.assertEqual(result.confidence.value, "MEDIUM")
        self.assertEqual(result.relative_weakness_status, "RELATIVE_REFERENCE_UNAVAILABLE")
        self.assertFalse(result.market_resonance)

    def test_relative_reference_unavailable(self):
        result = self.evaluate(99, hist=history(relative_available=False))
        self.assertEqual(result.relative_weakness_status, "RELATIVE_REFERENCE_UNAVAILABLE")
        self.assertEqual(result.confidence.value, "MEDIUM")

    def test_stock_weak_market_stable(self):
        value = market60(light="GREEN")
        result = self.evaluate(98, market=value)
        self.assertIn("STOCK_WEAK_MARKET_STABLE", result.divergence_flags)

    def test_stock_strong_market_weak(self):
        result = self.evaluate(101, hist=history(previous=100, two_ago=99))
        self.assertIn("STOCK_STRONG_MARKET_WEAK", result.divergence_flags)

    def test_high_low_volume_turnover_do_not_score(self):
        first = self.evaluate(98)
        second = self.evaluate(98, noisy=999999)
        self.assertEqual(first.risk_score, second.risk_score)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_deterministic(self):
        self.assertEqual(self.evaluate(98).to_dict(), self.evaluate(98).to_dict())

    def test_future_history_is_rejected(self):
        bad = list(history())
        bad.append(replace(bad[-1], period_end="2026-08-28T15:00:00+08:00"))
        with self.assertRaises(TrendMonitorError) as raised:
            self.evaluate(98, hist=bad)
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)


class Stock15mInternalTests(unittest.TestCase):
    def setUp(self):
        self.engine = Stock15mInternalEngine(RULES)
        self.start = datetime(2026, 8, 28, 14, 0, tzinfo=SHANGHAI)
        self.end = datetime(2026, 8, 28, 15, 0, tzinfo=SHANGHAI)

    def evaluate(self, closes, *, as_of=None, state="WEAKNESS_BROADENING", noisy=0):
        as_of = as_of or self.end
        return self.engine.evaluate(
            current_15(closes, as_of=as_of, noisy=noisy),
            as_of=as_of,
            period_start=self.start,
            period_end=self.end,
            source_stock_risk_input_id="stock15",
            market_15m_result=market15(state),
            source_market_15m_result_id="market15",
        )

    def test_late_weakening_and_joint_weakness(self):
        result = self.evaluate((101, 102, 101, 100))
        self.assertEqual(result.classification, InternalClassification.LATE_WEAKENING)
        self.assertIn("JOINT_WEAKNESS", result.joint_market_flags)

    def test_late_repair_against_weak_market(self):
        result = self.evaluate((99, 98, 99, 101))
        self.assertEqual(result.classification, InternalClassification.LATE_REPAIR)
        self.assertIn("STOCK_REPAIR_AGAINST_WEAK_MARKET", result.joint_market_flags)

    def test_joint_repair(self):
        result = self.evaluate((99, 98, 99, 101), state="REPAIR_BROADENING")
        self.assertIn("JOINT_REPAIR", result.joint_market_flags)

    def test_in_progress_two_and_three(self):
        for count in (2, 3):
            with self.subTest(count=count):
                as_of = self.start + timedelta(minutes=count * 15)
                result = self.evaluate(tuple(101 + i for i in range(count)), as_of=as_of)
                self.assertEqual(result.period_status, InternalPeriodStatus.IN_PROGRESS)
                self.assertEqual(result.completed_15m_count, count)
                self.assertIn(result.classification.value, {"EARLY_STRENGTH", "EARLY_WEAKNESS", "EARLY_MIXED"})

    def test_flat_denominator(self):
        result = self.evaluate((100, 100, 100, 100))
        self.assertIsNone(result.repair_strength)
        self.assertIsNone(result.finish_position)

    def test_noisy_fields_do_not_change_classification(self):
        self.assertEqual(self.evaluate((101, 102, 101, 100)).classification, self.evaluate((101, 102, 101, 100), noisy=999).classification)

    def test_internal_does_not_modify_stock_score(self):
        stock = Stock60mRiskEngine(RULES).evaluate(
            current_60(98),
            history=history(),
            market_60m_result=market60(),
            market_15m_result=market15(),
            source_stock_risk_input_id="stock",
            source_market_60m_result_id="market60",
            source_market_15m_result_id="market15",
        )
        before = stock.to_dict()
        self.evaluate((99, 98, 99, 101))
        self.assertEqual(before, stock.to_dict())

    def test_append_only_outputs_and_risk_input_deduplication(self):
        stock = Stock60mRiskEngine(RULES).evaluate(
            current_60(98),
            history=history(),
            market_60m_result=market60(),
            market_15m_result=market15(),
            source_stock_risk_input_id="stock",
            source_market_60m_result_id="market60",
            source_market_15m_result_id="market15",
        )
        internal = self.evaluate((101, 102, 101, 100))
        monitor = StockIntradayMonitorResult(
            instrument_id=STOCK,
            symbol="600487",
            stock_60m_risk=stock,
            stock_15m_internal=internal,
            market_60m_context=stock.market_context,
            market_15m_context={"market_internal_state": "WEAKNESS_BROADENING"},
        )
        with TemporaryDirectory() as directory:
            output = StockIntradayOutputStore(directory)
            first = output.save_monitor(monitor, render_stock_intraday_report(monitor))
            second = output.save_monitor(monitor, render_stock_intraday_report(monitor))
            self.assertNotEqual(first["stock_intraday_monitor"], second["stock_intraday_monitor"])
            input_store = StockRiskInputStore(Path(directory) / "inputs")
            input60 = current_60(98)
            input15 = current_15((101, 102, 101, 100))
            path1 = input_store.save_period(
                as_of=self.end.isoformat(),
                inputs_60m={STOCK: input60},
                inputs_15m={STOCK: input15},
                rules_version=RULES.rules_version,
                market_60m_result=market60(),
                market_15m_result=market15(),
            )
            path2 = input_store.save_period(
                as_of=self.end.isoformat(),
                inputs_60m={STOCK: input60},
                inputs_15m={STOCK: input15},
                rules_version=RULES.rules_version,
                market_60m_result=market60(),
                market_15m_result=market15(),
            )
            self.assertEqual(path1, path2)


if __name__ == "__main__":
    unittest.main()
