from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.errors import ErrorCategory
from trend_monitor.market_internal import (
    Historical15mRiskInputBuilder,
    InternalReplayPeriod,
    Market15mInternalEngine,
    Market15mInternalRules,
    Market15mInternalStore,
    Market15mRiskInputStore,
    render_market_15m_internal_report,
    run_internal_replay,
)
from trend_monitor.market_risk import Market60mRiskRules
from trend_monitor.schemas import (
    AnalysisPeriod,
    AssetType,
    FeatureEligibility,
    FeatureInput,
    FeatureLineage,
    InternalClassification,
    InternalPeriodStatus,
    MarketInternalState,
    PreflightStatus,
    RiskBar,
    RiskInput,
    RiskInputDataStatus,
    RiskSourceTrace,
)


ROOT = Path(__file__).resolve().parents[1]
RULES = Market15mInternalRules.load(ROOT / "config" / "market_15m_internal_rules.json")
SOURCE_RULES = Market60mRiskRules.load(ROOT / "config" / "market_60m_risk_rules.json")
ENGINE = Market15mInternalEngine(RULES, SOURCE_RULES)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 28)
START = datetime(2026, 8, 28, 9, 30, tzinfo=SHANGHAI)
END = datetime(2026, 8, 28, 10, 30, tzinfo=SHANGHAI)


def epoch(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def risk_bar(instrument_id: str, start: datetime, end: datetime, close: float, *, noisy: float = 0) -> RiskBar:
    source_id = f"longbridge:{instrument_id}:15m:{epoch(start)}"
    return RiskBar(
        instrument_id=instrument_id,
        period="15m",
        start=epoch(start),
        end=epoch(end),
        open=close + noisy,
        high=close + 1000 + noisy,
        low=close - 1000 - noisy,
        close=close,
        volume=-999999 + noisy,
        turnover=-999999999 + noisy,
        source_provider="longbridge",
        provider_symbol="test.CN",
        source_bar_ids=(source_id,),
        source_raw_paths=(f"/raw/{instrument_id}.json",),
        fetched_at="2026-08-30T00:00:00+08:00",
        source_timestamp=epoch(start),
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


def risk_input(
    instrument_id: str,
    closes: tuple[float, ...],
    *,
    as_of: datetime = END,
    noisy: float = 0,
    close_quality: str = "TRUSTED",
) -> RiskInput:
    baseline = risk_bar(
        instrument_id,
        START - timedelta(minutes=15),
        START,
        100.0,
        noisy=noisy,
    )
    bars = [baseline]
    for index, close in enumerate(closes):
        bar = risk_bar(
            instrument_id,
            START + timedelta(minutes=15 * index),
            START + timedelta(minutes=15 * (index + 1)),
            close,
            noisy=noisy,
        )
        if close_quality != "TRUSTED":
            bar = RiskBar(**{**bar.to_dict(), "source_bar_ids": bar.source_bar_ids, "source_raw_paths": bar.source_raw_paths, "field_quality": {**bar.field_quality, "close": close_quality}})
        bars.append(bar)
    current = bars[-1]
    lineage = FeatureLineage(
        period="15m",
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
        quality={"close": close_quality},
        eligibility=FeatureEligibility.ENABLED,
        reason="ELIGIBLE",
        lineage=(lineage,),
    )
    return RiskInput(
        instrument_id=instrument_id,
        asset_type=AssetType.INDEX,
        analysis_period=AnalysisPeriod.MIN_15,
        as_of=as_of.isoformat(),
        trading_date=DAY.isoformat(),
        source_provider="longbridge",
        source_trace=RiskSourceTrace(
            requested_provider="longbridge",
            actual_provider="longbridge",
            provider_symbol="test.CN",
            fallback_used=False,
            fallback_reason=None,
            raw_path=f"/raw/{instrument_id}.json",
            fetched_at="2026-08-30T00:00:00+08:00",
            source_timestamp=current.source_timestamp,
        ),
        system_bars=tuple(bars),
        feature_inputs=(feature,),
        disabled_features=(),
        degraded_features=(),
        data_status=RiskInputDataStatus.DEGRADED,
        preflight_status=PreflightStatus.PASS_WITH_DEGRADATION,
        last_completed_bar_end=as_of.isoformat(),
        data_fetched_at="2026-08-30T00:00:00+08:00",
        layer_role="internal_structure_support_for_60m_only",
    )


def source_60m(end: datetime = END, score: int = 5) -> dict[str, object]:
    return {
        "rules_version": "market_60m_risk_v0.1",
        "last_completed_bar_end": end.isoformat(),
        "risk_score": score,
        "risk_light": "ORANGE",
        "risk_light_symbol": "🟠",
        "risk_direction": "FLAT",
    }


def evaluate_pattern(
    closes: tuple[float, ...],
    *,
    as_of: datetime = END,
    source_end: datetime | None = None,
    noisy: float = 0,
    included: tuple[str, ...] | None = None,
):
    instrument_ids = included or SOURCE_RULES.instrument_ids
    inputs = {item: risk_input(item, closes, as_of=as_of, noisy=noisy) for item in instrument_ids}
    sources = {item: f"snapshot:{item}" for item in instrument_ids}
    return ENGINE.evaluate(
        inputs,
        as_of=as_of,
        period_start=START,
        period_end=END,
        source_risk_input_ids=sources,
        source_60m_risk_result=source_60m(source_end or END),
        source_60m_risk_result_id="risk60:snapshot",
    )


class Market15mInternalClassificationTests(unittest.TestCase):
    def assert_classification(self, closes, expected):
        result = evaluate_pattern(closes)
        self.assertEqual(result.index_internal_states[0].classification, expected)
        self.assertEqual(result.completed_15m_count, 4)
        return result

    def test_four_up_is_healthy_up(self):
        self.assert_classification((101, 102, 103, 104), InternalClassification.HEALTHY_UP)

    def test_four_down_is_healthy_down(self):
        self.assert_classification((99, 98, 97, 96), InternalClassification.HEALTHY_DOWN)

    def test_down_down_up_up_is_late_repair(self):
        self.assert_classification((99, 98, 99, 100), InternalClassification.LATE_REPAIR)

    def test_up_up_down_down_is_late_weakening(self):
        self.assert_classification((101, 102, 101, 100), InternalClassification.LATE_WEAKENING)

    def test_repair_then_final_down_is_failed_repair(self):
        self.assert_classification((99, 100, 101, 100), InternalClassification.FAILED_REPAIR)

    def test_mixed(self):
        self.assert_classification((101, 100, 100, 101), InternalClassification.MIXED)

    def test_flat_denominator_is_na(self):
        result = self.assert_classification((100, 100, 100, 100), InternalClassification.MIXED)
        state = result.index_internal_states[0]
        self.assertIsNone(state.repair_strength)
        self.assertIsNone(state.finish_position)

    def test_high_low_volume_turnover_never_change_classification(self):
        normal = evaluate_pattern((99, 98, 99, 100))
        noisy = evaluate_pattern((99, 98, 99, 100), noisy=999999999)
        self.assertEqual(
            [item.classification for item in normal.index_internal_states],
            [item.classification for item in noisy.index_internal_states],
        )


class Market15mInternalMarketTests(unittest.TestCase):
    def test_two_bar_early_state(self):
        as_of = START + timedelta(minutes=30)
        result = evaluate_pattern((99, 98), as_of=as_of, source_end=START)
        self.assertEqual(result.period_status, InternalPeriodStatus.IN_PROGRESS)
        self.assertEqual(result.completed_15m_count, 2)
        self.assertEqual(result.index_internal_states[0].classification, InternalClassification.EARLY_WEAKNESS)

    def test_three_bar_early_state(self):
        as_of = START + timedelta(minutes=45)
        result = evaluate_pattern((101, 102, 101), as_of=as_of, source_end=START)
        self.assertEqual(result.completed_15m_count, 3)
        self.assertEqual(result.index_internal_states[0].classification, InternalClassification.EARLY_STRENGTH)

    def test_market_repair_broadening(self):
        result = evaluate_pattern((101, 102, 103, 104))
        self.assertEqual(result.market_internal_state, MarketInternalState.REPAIR_BROADENING)
        self.assertEqual(result.classification_counts["HEALTHY_UP"], 8)

    def test_market_weakness_broadening(self):
        result = evaluate_pattern((99, 98, 97, 96))
        self.assertEqual(result.market_internal_state, MarketInternalState.WEAKNESS_BROADENING)
        self.assertEqual(result.classification_counts["HEALTHY_DOWN"], 8)

    def test_incomplete_data_below_six(self):
        result = evaluate_pattern((99, 98, 97, 96), included=SOURCE_RULES.instrument_ids[:5])
        self.assertEqual(result.status, ErrorCategory.DATA_INCOMPLETE.value)
        self.assertEqual(result.market_internal_state, MarketInternalState.DATA_INCOMPLETE)

    def test_group_fully_missing_is_incomplete(self):
        included = tuple(item for item in SOURCE_RULES.instrument_ids if item not in SOURCE_RULES.groups["GROWTH"])
        result = evaluate_pattern((99, 98, 97, 96), included=included)
        self.assertEqual(result.market_internal_state, MarketInternalState.DATA_INCOMPLETE)

    def test_close_unavailable_degrades_one_index(self):
        inputs = {item: risk_input(item, (99, 98, 97, 96)) for item in SOURCE_RULES.instrument_ids}
        inputs[SOURCE_RULES.instrument_ids[0]] = risk_input(
            SOURCE_RULES.instrument_ids[0], (99, 98, 97, 96), close_quality="BLOCKED"
        )
        result = ENGINE.evaluate(
            inputs,
            as_of=END,
            period_start=START,
            period_end=END,
            source_risk_input_ids={item: f"snapshot:{item}" for item in SOURCE_RULES.instrument_ids},
            source_60m_risk_result=source_60m(),
            source_60m_risk_result_id="risk60:snapshot",
        )
        self.assertEqual(result.data_quality["valid_index_count"], 7)
        self.assertEqual(result.status, "READY")

    def test_deterministic_and_60m_score_immutable(self):
        frozen = source_60m()
        before = deepcopy(frozen)
        inputs = {item: risk_input(item, (99, 98, 99, 100)) for item in SOURCE_RULES.instrument_ids}
        kwargs = dict(
            as_of=END,
            period_start=START,
            period_end=END,
            source_risk_input_ids={item: f"snapshot:{item}" for item in SOURCE_RULES.instrument_ids},
            source_60m_risk_result=frozen,
            source_60m_risk_result_id="risk60:snapshot",
        )
        first = ENGINE.evaluate(inputs, **kwargs)
        second = ENGINE.evaluate(inputs, **kwargs)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(frozen, before)
        self.assertEqual(first.linked_60m_risk["risk_score"], 5)

    def test_report_and_append_only_store_have_no_trade_advice(self):
        result = evaluate_pattern((99, 98, 97, 96))
        report = render_market_15m_internal_report(result)
        self.assertNotIn("应该卖出", report)
        self.assertIn("不修改60分钟Risk Score", report)
        with TemporaryDirectory() as directory:
            store = Market15mInternalStore(directory)
            first, _ = store.save_result(result, report)
            second, _ = store.save_result(result, report)
            self.assertNotEqual(first, second)
            self.assertEqual(store.load(first), result.to_dict())

    def test_identical_period_risk_input_snapshot_is_reused(self):
        inputs = {item: risk_input(item, (99, 98, 99, 100)) for item in SOURCE_RULES.instrument_ids}
        with TemporaryDirectory() as directory:
            store = Market15mRiskInputStore(directory)
            first = store.save_period(as_of=END.isoformat(), inputs=inputs, rules_version=RULES.rules_version)
            second = store.save_period(as_of=END.isoformat(), inputs=inputs, rules_version=RULES.rules_version)
            self.assertEqual(first, second)
            self.assertEqual(len(Path(directory, "manifest.jsonl").read_text().splitlines()), 1)

    def test_replay_linkage_and_score_immutability(self):
        inputs = {item: risk_input(item, (99, 98, 99, 100)) for item in SOURCE_RULES.instrument_ids}
        period = InternalReplayPeriod(
            as_of=END,
            period_start=START,
            period_end=END,
            inputs=inputs,
            source_risk_input_ids={item: f"snapshot:{item}" for item in SOURCE_RULES.instrument_ids},
            previous_60m_closes={},
            source_60m_risk_result=source_60m(),
            source_60m_risk_result_id="risk60:replay#period",
        )
        replay = run_internal_replay(ENGINE, tuple(period for _ in range(80)))
        self.assertEqual(replay.periods, 80)
        self.assertTrue(replay.deterministic)
        self.assertTrue(replay.lookahead_safe)
        self.assertTrue(replay.score_immutable)
        self.assertEqual(replay.results[0].source_60m_risk_result_id, "risk60:replay#period")


if __name__ == "__main__":
    unittest.main()
