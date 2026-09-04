from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory, TrendMonitorError
from trend_monitor.market_risk import (
    HistoricalRiskInputBuilder,
    Market60mRiskEngine,
    Market60mRiskRules,
    MarketRiskOutputStore,
    render_market_60m_report,
)
from trend_monitor.quality import RiskFeatureContract
from trend_monitor.registry import InstrumentRegistry
from trend_monitor.risk_input import RiskInputAssembler
from trend_monitor.schemas import (
    AnalysisPeriod,
    AssetType,
    FeatureEligibility,
    FeatureInput,
    FeatureLineage,
    PreflightStatus,
    RiskBar,
    RiskChangeDirection,
    RiskInput,
    RiskInputDataStatus,
    RiskLight,
    RiskSourceTrace,
    SignalConfidence,
)
from tests.test_risk_input_assembly import result_for, source_records


ROOT = Path(__file__).resolve().parents[1]
RULES = Market60mRiskRules.load(ROOT / "config" / "market_60m_risk_rules.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def epoch(day: date, label: str) -> int:
    return int(datetime.combine(day, datetime.strptime(label, "%H:%M").time(), tzinfo=SHANGHAI).timestamp() * 1000)


def bar(instrument_id: str, end: int, close: float, *, noisy: float = 0) -> RiskBar:
    source_id = f"longbridge:{instrument_id}:60m:{end}"
    raw_path = f"/raw/{instrument_id}.json"
    return RiskBar(
        instrument_id=instrument_id,
        period="60m",
        start=end - 60 * 60 * 1000,
        end=end,
        open=close + noisy,
        high=close + 1000 + noisy,
        low=close - 1000 - noisy,
        close=close,
        volume=-999999 + noisy,
        turnover=-999999999 + noisy,
        source_provider="longbridge",
        provider_symbol="test.CN",
        source_bar_ids=(source_id,),
        source_raw_paths=(raw_path,),
        fetched_at="2026-08-30T00:00:00+08:00",
        source_timestamp=end,
        transformation="DIRECT_NORMALIZED",
        quality_status="DIRECT_NORMALIZED",
        field_quality={
            "open": "APPROXIMATE",
            "high": "APPROXIMATE",
            "low": "APPROXIMATE",
            "close": "TRUSTED",
            "volume": "BLOCKED",
            "turnover": "ADVISORY_ONLY",
        },
    )


def risk_input(instrument_id: str, bars: tuple[RiskBar, ...]) -> RiskInput:
    current = bars[-1]
    lineage = FeatureLineage(
        period="60m",
        source_provider="longbridge",
        provider_symbol="test.CN",
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
    as_of = datetime.fromtimestamp(current.end / 1000, tz=timezone.utc).astimezone(SHANGHAI)
    return RiskInput(
        instrument_id=instrument_id,
        asset_type=AssetType.INDEX,
        analysis_period=AnalysisPeriod.MIN_60,
        as_of=as_of.isoformat(),
        trading_date=as_of.date().isoformat(),
        source_provider="longbridge",
        source_trace=RiskSourceTrace(
            requested_provider="longbridge",
            actual_provider="longbridge",
            provider_symbol="test.CN",
            fallback_used=False,
            fallback_reason=None,
            raw_path=f"/raw/{instrument_id}.json",
            fetched_at="2026-08-30T00:00:00+08:00",
            source_timestamp=current.end,
        ),
        system_bars=bars,
        feature_inputs=(feature,),
        disabled_features=(),
        degraded_features=(),
        data_status=RiskInputDataStatus.DEGRADED,
        preflight_status=PreflightStatus.PASS_WITH_DEGRADATION,
        last_completed_bar_end=as_of.isoformat(),
        data_fetched_at="2026-08-30T00:00:00+08:00",
        layer_role="risk_warning_and_detail_confirmation",
    )


def history_input(instrument_id: str) -> RiskInput:
    bars = []
    start = date(2026, 5, 1)
    close = 100.0
    for offset in range(60):
        day = start + timedelta(days=offset)
        for index, label in enumerate(("10:30", "11:30", "14:00", "15:00")):
            close = 102.0 if len(bars) % 2 == 0 else 100.0
            bars.append(bar(instrument_id, epoch(day, label), close))
    return risk_input(instrument_id, tuple(bars))


def current_input(instrument_id: str, closes: tuple[float, float, float], *, noisy: float = 0) -> RiskInput:
    day = date(2026, 8, 28)
    values = tuple(
        bar(instrument_id, epoch(day, label), close, noisy=noisy)
        for label, close in zip(("10:30", "11:30", "14:00"), closes)
    )
    return risk_input(instrument_id, values)


class Market60mRiskEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = Market60mRiskEngine(RULES)
        self.history = {item: (history_input(item),) for item in RULES.instrument_ids}
        self.sources = {item: f"snapshot:{item}" for item in RULES.instrument_ids}

    def evaluate(self, patterns=None, *, previous=None, noisy=0):
        patterns = patterns or {item: (100.0, 101.0, 102.0) for item in RULES.instrument_ids}
        inputs = {
            item: current_input(item, patterns[item], noisy=noisy)
            for item in patterns
        }
        return self.engine.evaluate(
            inputs,
            history_inputs=self.history,
            source_snapshot_ids=self.sources,
            previous_result=previous,
        )

    def test_eight_advance_is_green(self):
        result = self.evaluate()
        self.assertEqual(result.breadth, {"advancers": 8, "decliners": 0, "unchanged": 0})
        self.assertEqual(result.risk_score, 0)
        self.assertEqual(result.risk_light, RiskLight.GREEN)

    def test_eight_decline_persistent_and_shock_is_red(self):
        patterns = {item: (100.0, 99.0, 85.0) for item in RULES.instrument_ids}
        result = self.evaluate(patterns)
        self.assertEqual(result.breadth["decliners"], 8)
        self.assertEqual(result.persistent_weakness["count"], 8)
        self.assertEqual(result.downside_shocks["count"], 8)
        self.assertEqual(result.risk_score, 7)
        self.assertEqual(result.risk_light, RiskLight.RED)

    def test_four_advance_four_decline_breadth_points(self):
        patterns = {
            item: ((100.0, 99.0, 100.0) if index < 4 else (100.0, 101.0, 100.0))
            for index, item in enumerate(RULES.instrument_ids)
        }
        result = self.evaluate(patterns)
        self.assertEqual(result.breadth["decliners"], 4)
        self.assertEqual(result.score_components["breadth_points"], 1)

    def test_seven_decline_and_persistent_weakness(self):
        patterns = {
            item: ((100.0, 99.0, 98.0) if index < 7 else (100.0, 101.0, 102.0))
            for index, item in enumerate(RULES.instrument_ids)
        }
        result = self.evaluate(patterns)
        self.assertEqual(result.breadth["decliners"], 7)
        self.assertEqual(result.persistent_weakness["count"], 7)
        self.assertTrue(result.strong_broad_weakness)

    def test_downside_shock_uses_historical_p95(self):
        patterns = {item: (100.0, 101.0, 102.0) for item in RULES.instrument_ids}
        patterns["index.sse_composite"] = (100.0, 99.0, 80.0)
        result = self.evaluate(patterns)
        state = next(item for item in result.index_states if item.instrument_id == "index.sse_composite")
        self.assertTrue(state.downside_shock)
        self.assertEqual(state.shock_feature_status, "AVAILABLE")
        self.assertIsNotNone(state.shock_reference_p95)

    def test_weighted_support_distortion(self):
        patterns = {item: (100.0, 101.0, 100.0) for item in RULES.instrument_ids}
        for item in RULES.groups["LARGE_CAP"]:
            patterns[item] = (100.0, 99.0, 100.0)
        result = self.evaluate(patterns)
        self.assertTrue(result.weighted_support_distortion)
        self.assertEqual(result.score_components["weighted_support_distortion_points"], 1)

    def test_small_cap_stress_and_style_divergence(self):
        patterns = {item: (100.0, 101.0, 100.0) for item in RULES.instrument_ids}
        for item in RULES.groups["LARGE_CAP"]:
            patterns[item] = (100.0, 99.0, 100.0)
        result = self.evaluate(patterns)
        self.assertTrue(result.small_cap_stress)
        self.assertTrue(result.style_divergence_strong)

    def test_broad_repair_and_score_floor(self):
        patterns = {item: (100.0, 99.0, 101.0) for item in RULES.instrument_ids}
        result = self.evaluate(patterns)
        self.assertTrue(result.broad_repair)
        self.assertEqual(result.repair_count, 8)
        self.assertEqual(result.risk_score, 0)

    def test_risk_light_boundaries(self):
        expected = {
            0: "GREEN", 1: "GREEN", 2: "YELLOW", 3: "YELLOW",
            4: "ORANGE", 5: "ORANGE", 6: "RED", 8: "RED",
        }
        for score, name in expected.items():
            with self.subTest(score=score):
                self.assertEqual(RULES.light(score)[0], name)

    def test_previous_result_direction_and_first_run(self):
        first = self.evaluate()
        self.assertEqual(first.risk_direction, RiskChangeDirection.NOT_AVAILABLE)
        rising = self.evaluate(previous={
            "risk_score": -1,
            "last_completed_bar_end": "2026-08-28T13:00:00+08:00",
        })
        self.assertEqual(rising.risk_direction, RiskChangeDirection.RISING)

    def test_confidence_high_medium_and_blocked(self):
        full = self.evaluate()
        self.assertEqual(full.signal_confidence, SignalConfidence.HIGH)
        patterns = {item: (100.0, 101.0, 102.0) for item in RULES.instrument_ids[:-1]}
        medium = self.evaluate(patterns)
        self.assertEqual(medium.signal_confidence, SignalConfidence.MEDIUM)
        missing_group = {
            item: (100.0, 101.0, 102.0)
            for item in RULES.instrument_ids
            if item not in RULES.groups["GROWTH"]
        }
        blocked = self.evaluate(missing_group)
        self.assertEqual(blocked.status, ErrorCategory.DATA_INCOMPLETE.value)
        self.assertIsNone(blocked.risk_light)

    def test_blocked_volume_and_high_low_never_affect_score(self):
        normal = self.evaluate()
        noisy = self.evaluate(noisy=123456789)
        first = normal.to_dict()
        second = noisy.to_dict()
        for item in first["index_states"]:
            item.pop("source_snapshot_id", None)
        for item in second["index_states"]:
            item.pop("source_snapshot_id", None)
        self.assertEqual(first, second)

    def test_future_history_is_rejected(self):
        inputs = {item: current_input(item, (100.0, 101.0, 102.0)) for item in RULES.instrument_ids}
        with self.assertRaises(TrendMonitorError) as raised:
            self.engine.evaluate(
                inputs,
                history_inputs={item: (inputs[item],) for item in RULES.instrument_ids},
                source_snapshot_ids=self.sources,
            )
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_deterministic_replay_output_and_append_only_store(self):
        first = self.evaluate()
        second = self.evaluate()
        self.assertEqual(first.to_dict(), second.to_dict())
        report = render_market_60m_report(first)
        self.assertNotIn("应该卖出", report)
        with TemporaryDirectory() as directory:
            store = MarketRiskOutputStore(directory)
            path1, _ = store.save(first, report)
            path2, _ = store.save(first, report)
            self.assertNotEqual(path1, path2)
            self.assertEqual(store.load(path1), first.to_dict())
            replay1 = store.save_replay(
                {"schema_version": 1, "periods": 80},
                last_completed_bar_end=first.last_completed_bar_end,
                rules_version=first.rules_version,
            )
            replay2 = store.save_replay(
                {"schema_version": 1, "periods": 80},
                last_completed_bar_end=first.last_completed_bar_end,
                rules_version=first.rules_version,
            )
            self.assertNotEqual(replay1, replay2)
            self.assertEqual(store.load(replay1)["periods"], 80)


class HistoricalRiskInputBuilderRetryTests(unittest.TestCase):
    def _intraday_sources(self):
        base = source_records(
            "60m",
            instrument_id="index.csi500",
            asset_type=AssetType.INDEX,
            day="2026-09-04",
        )
        sources = {}
        for instrument_id in RULES.instrument_ids:
            trace = replace(
                base[0].source_trace,
                provider_symbol=f"{instrument_id}.CN",
                raw_path=f"/raw/{instrument_id}.json",
            )
            records = tuple(
                replace(
                    item,
                    instrument_id=instrument_id,
                    symbol=f"{instrument_id}.CN",
                    source_trace=trace,
                )
                for item in base
            )
            sources[instrument_id] = result_for("60m", records)
        return sources

    def test_intraday_prefix_rebuilds_same_day_1130_and_1400_periods(self):
        service = Mock()
        service.registry = InstrumentRegistry.load(ROOT / "config" / "instruments.json")
        assembler = RiskInputAssembler(
            RiskFeatureContract.load(ROOT / "config" / "risk_feature_contract.json")
        )
        builder = HistoricalRiskInputBuilder(service, assembler, RULES)
        sources = self._intraday_sources()

        at_1130 = builder.build_intraday_prefix(
            as_of=datetime.fromisoformat("2026-09-04T11:30:00+08:00"),
            source_results=sources,
        )
        at_1400 = builder.build_intraday_prefix(
            as_of=datetime.fromisoformat("2026-09-04T14:00:00+08:00"),
            source_results=sources,
        )

        self.assertEqual(
            [item.as_of.strftime("%H:%M") for item in at_1130],
            ["10:30", "11:30"],
        )
        self.assertEqual(
            [item.as_of.strftime("%H:%M") for item in at_1400],
            ["10:30", "11:30", "14:00"],
        )
        self.assertTrue(
            all(
                value.last_completed_bar_end == period.as_of.isoformat()
                for period in (*at_1130, *at_1400)
                for value in period.inputs.values()
            )
        )

    def test_reuses_supplied_source_results_without_provider_calls(self):
        service = Mock()
        service.registry = Mock()
        supplied = {item: Mock(normalized=()) for item in RULES.instrument_ids}
        builder = HistoricalRiskInputBuilder(service, Mock(), RULES)
        with self.assertRaises(TrendMonitorError) as raised:
            builder.build(
                start=date(2026, 4, 1),
                end=date(2026, 8, 28),
                source_results=supplied,
            )
        self.assertEqual(raised.exception.category, ErrorCategory.DATA_INCOMPLETE)
        service.get_history_bars.assert_not_called()

    def test_rejects_unexpected_supplied_source_result(self):
        service = Mock()
        builder = HistoricalRiskInputBuilder(service, Mock(), RULES)
        with self.assertRaises(TrendMonitorError) as raised:
            builder.build(
                start=date(2026, 4, 1),
                end=date(2026, 8, 28),
                source_results={"index.not_in_v0_1": Mock()},
            )
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)
        service.get_history_bars.assert_not_called()

    def test_retries_only_wrapped_network_failure(self):
        expected = object()
        service = Mock()
        service.get_history_bars.side_effect = (
            TrendMonitorError(
                ErrorCategory.DATA_INCOMPLETE,
                "temporary provider failure",
                details={
                    "failure_details": (
                        {"provider": "longbridge", "category": "NETWORK_ERROR"},
                    )
                },
            ),
            expected,
        )
        builder = HistoricalRiskInputBuilder(service, Mock(), RULES)
        with patch("trend_monitor.market_risk.replay.sleep") as wait:
            actual = builder._get_history_with_network_retry(
                instrument_id="index.chinext",
                provider="longbridge",
                start=date(2026, 4, 1),
                end=date(2026, 8, 28),
            )
        self.assertIs(actual, expected)
        self.assertEqual(service.get_history_bars.call_count, 2)
        wait.assert_called_once_with(2.0)

    def test_does_not_retry_non_network_failure(self):
        service = Mock()
        error = TrendMonitorError(
            ErrorCategory.DATA_INCOMPLETE,
            "permission denied",
            details={
                "failure_details": (
                    {"provider": "longbridge", "category": "PERMISSION_ERROR"},
                )
            },
        )
        service.get_history_bars.side_effect = error
        builder = HistoricalRiskInputBuilder(service, Mock(), RULES)
        with patch("trend_monitor.market_risk.replay.sleep") as wait:
            with self.assertRaises(TrendMonitorError) as raised:
                builder._get_history_with_network_retry(
                    instrument_id="index.chinext",
                    provider="longbridge",
                    start=date(2026, 4, 1),
                    end=date(2026, 8, 28),
                )
        self.assertIs(raised.exception, error)
        self.assertEqual(service.get_history_bars.call_count, 1)
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
