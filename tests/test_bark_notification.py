from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from trend_monitor.notifications import (
    BarkAdapter,
    BarkConfig,
    ChineseNotificationPresenter,
    NotificationPolicy,
    NotificationPolicyConfig,
    NotificationService,
    NotificationStore,
)
from trend_monitor.notifications.bark import (
    BarkHttpResult,
    BarkTransportFailure,
)
from trend_monitor.runtime import RuntimeConfig, RuntimeRunner, RuntimeStore
from trend_monitor.runtime.pipeline import PipelineRefreshResult
from trend_monitor.schemas.notification import NotificationSeverity, NotificationStatus


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 8, 28, 15, 3, tzinfo=SHANGHAI)
PHONE_INTERNAL_VALUES = {
    "ORANGE",
    "GREEN",
    "YELLOW",
    "RED",
    "RISING",
    "FALLING",
    "FLAT",
    "WEAKNESS_BROADENING",
    "REPAIR_BROADENING",
    "HEALTHY_UP",
    "HEALTHY_DOWN",
    "SUCCESS_WITH_DEGRADATION",
}


def assert_phone_chinese(test: unittest.TestCase, title: str, body: str) -> None:
    for value in PHONE_INTERNAL_VALUES:
        test.assertNotIn(value, title)
        test.assertNotIn(value, body)


def policy_config(*, catch_up_risk: bool = False) -> NotificationPolicyConfig:
    return NotificationPolicyConfig(
        rules_version="notification_policy_v0.1",
        group="TrendMonitor",
        max_attempts=3,
        backoff_seconds=(0, 0),
        catch_up_risk_notifications=catch_up_risk,
        catch_up_error_notifications=True,
    )


def bark_config(*, enabled: bool = True, key: str = "dummy-device-key") -> BarkConfig:
    return BarkConfig(
        enabled=enabled,
        server_url="https://api.day.app",
        device_key=key,
        timeout_seconds=1,
    )


def make_source(
    *,
    market_light: str = "YELLOW",
    market_score: int = 2,
    broad: bool = False,
    stock_light: str = "YELLOW",
    stock_score: int = 1,
    joint: bool = False,
    independent: bool = False,
    period_end: str = "2026-08-28T15:00:00+08:00",
) -> dict:
    market = {
        "last_completed_bar_end": period_end,
        "rules_version": "market_60m_risk_v0.1",
        "risk_score": market_score,
        "risk_light": market_light,
        "risk_direction": "FLAT",
        "signal_confidence": "HIGH",
        "breadth": {"advance_count": 2, "decline_count": 6},
        "broad_selloff_resonance": broad,
        "strong_broad_weakness": False,
        "data_quality": {
            "valid_index_count": 8,
            "preflight": {str(index): "PASS" for index in range(8)},
        },
    }
    market15 = {
        "60m_period_end": period_end,
        "rules_version": "market_15m_internal_v0.1",
        "market_internal_state": "WEAKNESS_BROADENING" if broad else "INTERNAL_MIXED",
        "data_quality": {"lookahead_safe": True},
    }
    stocks = {}
    for instrument_id, symbol, name in (
        ("stock.hengtong_optic", "600487", "亨通光电"),
        ("stock.wus_printed_circuit", "002463", "沪电股份"),
    ):
        stocks[instrument_id] = {
            "stock_60m": {
                "period_end": period_end,
                "rules_version": "stock_60m_risk_v0.1",
                "symbol": symbol,
                "name": name,
                "risk_score": stock_score,
                "risk_light": stock_light,
                "risk_direction": "FLAT",
                "confidence": "HIGH",
                "current_return": -0.0109,
                "relative_return": -0.0088,
                "persistent_weakness": True,
                "market_resonance": True,
                "divergence_flags": ["STOCK_WEAK_MARKET_STABLE"] if independent else [],
                "data_quality": {"lookahead_safe": True},
            },
            "stock_15m": {
                "rules_version": "stock_15m_internal_v0.1",
                "classification": "HEALTHY_DOWN",
                "direction_sequence": ["DOWN"] * 4,
                "joint_market_flags": ["JOINT_WEAKNESS"] if joint else [],
                "data_quality": {"lookahead_safe": True},
            },
        }
    return {
        "market": market,
        "market_15m": market15,
        "stocks": stocks,
        "source_ids": {
            "market_result_id": "market-result",
            "market_15m_result_id": "market15-result",
            "stock_result_ids": {key: f"{key}-result" for key in stocks},
        },
        "source_safety": {
            "market_lookahead": True,
            "market_15m_lookahead": True,
            "stock_lookahead": True,
            "stock_score_immutable": True,
        },
    }


def combined(*, status: str = "SUCCESS", mode: str = "LIVE_SCHEDULED") -> dict:
    return {
        "trading_date": "2026-08-28",
        "period_end": "2026-08-28T15:00:00+08:00",
        "execution_mode": mode,
        "status": status,
    }


def catch_up_reports() -> list[dict]:
    definitions = (
        ("10:30", "ORANGE", 5, "FLAT"),
        ("11:30", "ORANGE", 5, "FLAT"),
        ("14:00", "GREEN", 0, "FALLING"),
        ("15:00", "GREEN", 0, "FLAT"),
    )
    reports = []
    for label, light, score, direction in definitions:
        reports.append(
            {
                "trading_date": "2026-08-31",
                "period_end": f"2026-08-31T{label}:00+08:00",
                "execution_mode": "CATCH_UP",
                "status": "SUCCESS_WITH_DEGRADATION",
                "market": {
                    "risk_light": light,
                    "risk_score": score,
                    "risk_direction": direction,
                    "15m_internal": "REPAIR_BROADENING",
                },
                "stocks": {
                    "stock.hengtong_optic": {
                        "risk_light": "GREEN",
                        "risk_score": 0,
                        "15m_classification": "HEALTHY_UP",
                    },
                    "stock.wus_printed_circuit": {
                        "risk_light": "GREEN",
                        "risk_score": 0,
                        "15m_classification": "MIXED",
                    },
                },
                "data": {"market_index_coverage": "8/8"},
            }
        )
    return reports


def service(
    root: Path,
    *,
    enabled: bool = True,
    transport=None,
    catch_up_risk: bool = False,
) -> NotificationService:
    pconfig = policy_config(catch_up_risk=catch_up_risk)
    bconfig = bark_config(enabled=enabled)
    adapter = BarkAdapter(
        bconfig,
        pconfig,
        transport=transport or (lambda *_: BarkHttpResult(200, b'{"code":200}')),
        sleeper=lambda _: None,
    )
    return NotificationService(
        bark_config=bconfig,
        policy_config=pconfig,
        policy=NotificationPolicy(pconfig),
        adapter=adapter,
        store=NotificationStore(root),
        now=lambda: NOW,
    )


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = NotificationPolicy(policy_config())

    def event_types(self, current, previous=None, *, status="SUCCESS", mode="LIVE_SCHEDULED"):
        return {
            event.event_type
            for event in self.policy.evaluate_combined(
                current,
                previous,
                combined(status=status, mode=mode),
                source_result_id="combined-result",
            )
        }

    def test_test_notification(self):
        event = self.policy.test_event(created_at=NOW.isoformat())
        self.assertEqual(
            (event.event_type, event.title, event.group),
            ("TEST", "TrendMonitor｜中文通知测试", "TrendMonitor"),
        )
        self.assertEqual(
            event.body,
            "手机通知中文化已生效。\n\n"
            "🟢 系统运行正常\n"
            "风险与数据计算仍使用原有确定性规则。",
        )

    def test_market_risk_light_upgrade(self):
        self.assertIn("MARKET_RISK_LIGHT_UP", self.event_types(make_source(market_light="ORANGE"), make_source()))

    def test_market_score_up(self):
        self.assertIn("MARKET_SCORE_UP", self.event_types(make_source(market_score=3), make_source(market_score=2)))

    def test_broad_weakness_first_appearance(self):
        self.assertIn("MARKET_BROAD_WEAKNESS", self.event_types(make_source(broad=True), make_source(broad=False)))
        self.assertNotIn("MARKET_BROAD_WEAKNESS", self.event_types(make_source(broad=True), make_source(broad=True)))

    def test_market_repair(self):
        self.assertIn("MARKET_REPAIR", self.event_types(make_source(market_light="YELLOW"), make_source(market_light="ORANGE")))

    def test_stock_risk_light_upgrade(self):
        events = self.event_types(make_source(stock_light="ORANGE"), make_source())
        self.assertIn("STOCK_RISK_LIGHT_UP", events)

    def test_stock_score_up(self):
        events = self.event_types(make_source(stock_score=2), make_source(stock_score=1))
        self.assertIn("STOCK_SCORE_UP", events)

    def test_joint_weakness_first_appearance(self):
        events = self.event_types(make_source(joint=True), make_source(joint=False))
        self.assertIn("JOINT_WEAKNESS", events)

    def test_independent_weakness_first_appearance(self):
        events = self.event_types(make_source(independent=True), make_source(independent=False))
        self.assertIn("INDEPENDENT_WEAKNESS", events)

    def test_data_incomplete(self):
        events = self.policy.evaluate_combined(
            make_source(),
            None,
            combined(status="DATA_INCOMPLETE"),
            source_result_id="combined-result",
        )
        self.assertEqual({event.event_type for event in events}, {"DATA_INCOMPLETE"})
        self.assertIn("数据状态：不完整", events[0].body)
        assert_phone_chinese(self, events[0].title, events[0].body)

    def test_stable_state_has_no_notification_event(self):
        stable = make_source()
        self.assertEqual(self.event_types(stable, deepcopy(stable)), set())

    def test_runtime_failed(self):
        record = {
            "run_id": "run",
            "trading_date": "2026-08-28",
            "period_end": "2026-08-28T15:00:00+08:00",
            "execution_mode": "LIVE_SCHEDULED",
            "rules_versions": {"runtime": "intraday_runtime_v0.1"},
            "error_summary": {"stage": "RUNTIME", "error_category": "SCHEMA_OR_CONTRACT_ERROR"},
        }
        event = self.policy.evaluate_runtime_failure(record)[0]
        self.assertEqual(event.event_type, "RUNTIME_FAILED")
        self.assertEqual(event.title, "TrendMonitor｜运行异常")
        self.assertIn("状态：数据或规则契约异常", event.body)
        self.assertIn("阶段：运行流程", event.body)
        assert_phone_chinese(self, event.title, event.body)

    def test_provider_final_failure(self):
        record = {
            "run_id": "run",
            "trading_date": "2026-08-28",
            "period_end": "2026-08-28T15:00:00+08:00",
            "execution_mode": "LIVE_SCHEDULED",
            "rules_versions": {"runtime": "intraday_runtime_v0.1"},
            "error_summary": {"stage": "MARKET_DATA_REFRESH", "error_category": "NETWORK_ERROR"},
        }
        event = self.policy.evaluate_runtime_failure(record)[0]
        self.assertEqual(event.event_type, "PROVIDER_FAILURE")
        self.assertEqual(event.title, "TrendMonitor｜数据源异常")
        self.assertIn("状态：网络异常", event.body)
        self.assertIn("阶段：市场数据", event.body)
        assert_phone_chinese(self, event.title, event.body)

    def test_market_and_stock_phone_text_is_chinese(self):
        current = make_source(
            market_light="ORANGE",
            market_score=5,
            broad=True,
            stock_light="ORANGE",
            stock_score=3,
            joint=True,
        )
        current["market"]["risk_direction"] = "RISING"
        for item in current["stocks"].values():
            item["stock_60m"]["risk_direction"] = "RISING"
        events = self.policy.evaluate_combined(
            current,
            make_source(),
            combined(),
            source_result_id="combined-result",
        )
        market_event = next(event for event in events if event.event_type == "MARKET_RISK_LIGHT_UP")
        stock_event = next(event for event in events if event.event_type == "STOCK_RISK_LIGHT_UP")
        self.assertIn("🟠 橙色 · 风险分 5", market_event.body)
        self.assertIn("风险变化：上升", market_event.body)
        self.assertIn("市场结构：弱势扩散", market_event.body)
        self.assertIn("15分钟结构：持续走弱", stock_event.body)
        self.assertIn("个股与市场共振走弱", stock_event.body)
        for event in events:
            assert_phone_chinese(self, event.title, event.body)

    def test_market_repair_uses_relief_template(self):
        current = make_source(market_light="GREEN", market_score=0)
        current["market_15m"]["market_internal_state"] = "REPAIR_BROADENING"
        previous = make_source(market_light="ORANGE", market_score=5)
        event = next(
            item
            for item in self.policy.evaluate_combined(
                current,
                previous,
                combined(),
                source_result_id="combined-result",
            )
            if item.event_type == "MARKET_REPAIR"
        )
        self.assertEqual(event.title, "TrendMonitor｜市场风险缓解")
        self.assertIn("🟠 橙色", event.body)
        self.assertIn("🟢 绿色", event.body)
        self.assertIn("15分钟结构：修复扩散", event.body)
        assert_phone_chinese(self, event.title, event.body)

    def test_event_identity_and_severity_match_pre_chinese_baseline(self):
        current = make_source(
            market_light="ORANGE",
            market_score=5,
            broad=True,
            joint=True,
            stock_light="ORANGE",
            stock_score=3,
        )
        previous = make_source(
            market_light="YELLOW",
            market_score=2,
            broad=False,
            joint=False,
            stock_light="YELLOW",
            stock_score=1,
        )
        events = self.policy.evaluate_combined(
            current,
            previous,
            combined(),
            source_result_id="combined-result",
        )
        actual = [
            (event.event_type, event.instrument_id, event.severity.value, event.event_key)
            for event in events
        ]
        expected = [
            ("MARKET_RISK_LIGHT_UP", "market", "HIGH", "b83d9dd74a0b043d47b19bee77fbca5199fe4967a5ce7a7ae47783e39a6f6392"),
            ("MARKET_BROAD_WEAKNESS", "market", "HIGH", "6d23356ee570b48b8a65eeced49b7b382afe793f90ef0d1725fc8c91d97900e5"),
            ("STOCK_RISK_LIGHT_UP", "stock.hengtong_optic", "HIGH", "38de800c4f3c740310a7b15bc1749574604646038e6aca90df990f31c164ed90"),
            ("JOINT_WEAKNESS", "stock.hengtong_optic", "HIGH", "ea74b65bb0374095a7a2911cb131809aee9cddadb00e652ba5b8c6cf1b75776d"),
            ("STOCK_RISK_LIGHT_UP", "stock.wus_printed_circuit", "HIGH", "3d4447e1ac4afc18b784629908fe971e073ce3f8ae470245ebbdd6e70cf03873"),
            ("JOINT_WEAKNESS", "stock.wus_printed_circuit", "HIGH", "dde14981e7d30cc46108e1b1bf1e94de8fee4a96e46def7af4ad1e528d5a3949"),
        ]
        self.assertEqual(actual, expected)


class PresentationMappingTests(unittest.TestCase):
    def setUp(self):
        self.presenter = ChineseNotificationPresenter()

    def test_risk_light_mappings(self):
        expected = {
            "GREEN": "绿色",
            "YELLOW": "黄色",
            "ORANGE": "橙色",
            "RED": "红色",
        }
        for internal, chinese in expected.items():
            with self.subTest(internal=internal):
                self.assertEqual(self.presenter.risk_light(internal), chinese)
                self.assertIn(chinese, self.presenter.risk_line(internal, 2))

    def test_risk_direction_mappings(self):
        expected = {
            "RISING": "风险上升",
            "FALLING": "风险下降",
            "FLAT": "风险持平",
            "N/A": "暂无对比",
        }
        for internal, chinese in expected.items():
            with self.subTest(internal=internal):
                self.assertEqual(self.presenter.risk_direction(internal), chinese)

    def test_market_internal_mappings(self):
        self.assertEqual(self.presenter.market_internal("WEAKNESS_BROADENING"), "弱势扩散")
        self.assertEqual(self.presenter.market_internal("REPAIR_BROADENING"), "修复扩散")
        self.assertEqual(self.presenter.market_internal("INTERNAL_MIXED"), "内部结构分化")

    def test_stock_15m_mappings(self):
        expected = {
            "HEALTHY_UP": "持续走强",
            "HEALTHY_DOWN": "持续走弱",
            "LATE_REPAIR": "后半段修复",
            "FAILED_REPAIR": "修复失败",
            "LATE_WEAKENING": "后半段转弱",
            "MIXED": "多空混合",
        }
        for internal, chinese in expected.items():
            with self.subTest(internal=internal):
                self.assertEqual(self.presenter.stock_15m(internal), chinese)

    def test_joint_flag_mappings(self):
        self.assertEqual(self.presenter.flag("JOINT_WEAKNESS"), "个股与市场共振走弱")
        self.assertEqual(self.presenter.flag("JOINT_REPAIR"), "个股与市场同步修复")

    def test_unknown_enum_falls_back_and_logs(self):
        with self.assertLogs("trend_monitor.notifications.presentation", level="WARNING") as captured:
            value = self.presenter.stock_15m("FUTURE_CLASSIFICATION")
        self.assertEqual(value, "状态待解释")
        self.assertIn("UNKNOWN_TRANSLATION", "\n".join(captured.output))
        self.assertNotIn("FUTURE_CLASSIFICATION", value)

    def test_catch_up_summary_is_chinese_and_distinguishes_safe_degradation(self):
        title, body = self.presenter.catch_up_summary(
            catch_up_reports(),
            final_flags={"stock.hengtong_optic": ("JOINT_REPAIR",)},
        )
        self.assertEqual(title, "TrendMonitor｜8月31日补跑完成")
        self.assertIn("10:30  🟠 橙色 · 风险分 5 · 持平", body)
        self.assertIn("15分钟结构：修复扩散", body)
        self.assertIn("个股与市场同步修复", body)
        self.assertIn("数据状态：正常", body)
        self.assertIn("部分非核心字段仅作参考", body)
        assert_phone_chinese(self, title, body)


class DeliveryTests(unittest.TestCase):
    def test_bark_disabled(self):
        with TemporaryDirectory() as tmp:
            result = service(Path(tmp), enabled=False).process_test(send=False)
            self.assertEqual(result["status"], NotificationStatus.SKIPPED_DISABLED.value)

    def test_invalid_config_fails_without_retry(self):
        with TemporaryDirectory() as tmp:
            pconfig = policy_config()
            bconfig = BarkConfig(False, "invalid", "", 1, "INVALID_ENABLED_VALUE")
            target = NotificationService(
                bark_config=bconfig,
                policy_config=pconfig,
                policy=NotificationPolicy(pconfig),
                adapter=BarkAdapter(bconfig, pconfig),
                store=NotificationStore(tmp),
                now=lambda: NOW,
            )
            result = target.process_test(send=True)
            self.assertEqual((result["status"], result["records"][0]["attempts"]), ("FAILED", 0))

    def test_live_scheduled_allowed(self):
        with TemporaryDirectory() as tmp:
            current, previous = make_source(broad=True), make_source(broad=False)
            result = service(Path(tmp)).process_combined(current, previous, combined(), source_result_id="result")
            self.assertEqual(result["status"], NotificationStatus.SENT.value)

    def test_catch_up_stale_suppression(self):
        with TemporaryDirectory() as tmp:
            current, previous = make_source(broad=True), make_source(broad=False)
            result = service(Path(tmp)).process_combined(
                current, previous, combined(mode="CATCH_UP"), source_result_id="result"
            )
            self.assertEqual(result["status"], NotificationStatus.SKIPPED_POLICY.value)

    def test_catch_up_error_is_allowed(self):
        with TemporaryDirectory() as tmp:
            result = service(Path(tmp)).process_combined(
                make_source(), None, combined(status="DATA_INCOMPLETE", mode="CATCH_UP"), source_result_id="result"
            )
            self.assertEqual(result["status"], NotificationStatus.SENT.value)

    def test_duplicate_suppression(self):
        with TemporaryDirectory() as tmp:
            target = service(Path(tmp))
            current, previous = make_source(broad=True), make_source(broad=False)
            first = target.process_combined(current, previous, combined(), source_result_id="result")
            second = target.process_combined(current, previous, combined(), source_result_id="result")
            self.assertEqual(first["status"], NotificationStatus.SENT.value)
            self.assertEqual(second["status"], NotificationStatus.SKIPPED_DUPLICATE.value)

    def test_same_terminal_runtime_failure_is_sent_only_once(self):
        with TemporaryDirectory() as tmp:
            deliveries = []
            target = service(
                Path(tmp),
                transport=lambda *_: (
                    deliveries.append(1) or BarkHttpResult(200, b'{"code":200}')
                ),
            )
            record = {
                "run_id": "first-failure",
                "trading_date": "2026-09-01",
                "period_end": "2026-09-01T15:00:00+08:00",
                "execution_mode": "CATCH_UP",
                "rules_versions": {"runtime": "intraday_runtime_v0.1"},
                "error_summary": {
                    "stage": "MARKET_15M_INTERNAL",
                    "error_category": "PIPELINE_FAILED",
                    "recoverable": False,
                },
            }

            first = target.process_runtime_failure(record)
            second = target.process_runtime_failure(
                {**record, "run_id": "duplicate-failure"}
            )

            self.assertEqual(first["status"], NotificationStatus.SENT.value)
            self.assertEqual(second["status"], NotificationStatus.SKIPPED_DUPLICATE.value)
            self.assertEqual(len(deliveries), 1)

    def test_notification_dry_run(self):
        with TemporaryDirectory() as tmp:
            current, previous = make_source(broad=True), make_source(broad=False)
            result = service(Path(tmp)).process_combined(
                current, previous, combined(), source_result_id="result", dry_run=True
            )
            self.assertEqual(result["status"], NotificationStatus.WOULD_SEND.value)

    def test_test_does_not_affect_production_dedup(self):
        with TemporaryDirectory() as tmp:
            target = service(Path(tmp))
            self.assertEqual(target.process_test(send=True)["status"], NotificationStatus.SENT.value)
            current, previous = make_source(broad=True), make_source(broad=False)
            result = target.process_combined(current, previous, combined(), source_result_id="result")
            self.assertEqual(result["status"], NotificationStatus.SENT.value)

    def test_http_success(self):
        result = BarkAdapter(
            bark_config(), policy_config(),
            transport=lambda *_: BarkHttpResult(200, b'{"code":200}'), sleeper=lambda _: None,
        ).send(title="TrendMonitor", body="test", group="TrendMonitor")
        self.assertEqual((result.status, result.attempts), (NotificationStatus.SENT, 1))

    def test_timeout_retry(self):
        calls = []
        def transport(*_):
            calls.append(1)
            if len(calls) < 3:
                raise BarkTransportFailure("TIMEOUT")
            return BarkHttpResult(200, b'{"code":200}')
        result = BarkAdapter(bark_config(), policy_config(), transport=transport, sleeper=lambda _: None).send(
            title="TrendMonitor", body="test", group="TrendMonitor"
        )
        self.assertEqual((result.status, result.attempts), (NotificationStatus.SENT, 3))

    def test_http_5xx_retry(self):
        calls = []
        def transport(*_):
            calls.append(1)
            return BarkHttpResult(503 if len(calls) < 3 else 200, b'{"code":200}')
        result = BarkAdapter(bark_config(), policy_config(), transport=transport, sleeper=lambda _: None).send(
            title="TrendMonitor", body="test", group="TrendMonitor"
        )
        self.assertEqual((result.status, result.attempts), (NotificationStatus.SENT, 3))

    def test_http_4xx_does_not_retry(self):
        calls = []
        def transport(*_):
            calls.append(1)
            return BarkHttpResult(401, b'{}')
        result = BarkAdapter(bark_config(), policy_config(), transport=transport, sleeper=lambda _: None).send(
            title="TrendMonitor", body="test", group="TrendMonitor"
        )
        self.assertEqual((result.status, result.attempts, len(calls)), (NotificationStatus.FAILED, 1, 1))

    def test_secret_redaction_and_safe_config_repr(self):
        key = "dummy-device-key"
        config = bark_config(key=key)
        self.assertNotIn(key, repr(config))
        self.assertNotIn(key, json.dumps(config.safe_summary()))

    def test_notification_store_is_append_only_and_secret_free(self):
        with TemporaryDirectory() as tmp:
            target = service(Path(tmp))
            target.process_test(send=True)
            target.process_test(send=True)
            manifest = Path(tmp) / "manifest.jsonl"
            text = manifest.read_text(encoding="utf-8")
            self.assertEqual(len(text.splitlines()), 2)
            self.assertNotIn("dummy-device-key", text)


class RuntimeIsolationTests(unittest.TestCase):
    def test_bark_failure_does_not_fail_risk_runtime(self):
        class Calendar:
            def is_trading_day(self, value, *, allow_network, observed_at):
                return True, "TEST"

        class Reader:
            def load_period(self, period_end):
                return make_source(period_end=period_end)

            def load_previous_period(self, period_end):
                previous = deepcopy(make_source(period_end="2026-08-28T14:00:00+08:00"))
                previous["market"]["risk_light"] = "GREEN"
                previous["market"]["risk_score"] = 0
                return previous

        class Pipeline:
            def refresh(self, *, as_of):
                return PipelineRefreshResult(1, ({"status": "PASS"},))

        class BrokenNotifier:
            def process_combined(self, *args, **kwargs):
                raise OSError("dummy-device-key")

            def process_runtime_failure(self, *args, **kwargs):
                raise OSError("dummy-device-key")

        config = RuntimeConfig.load(ROOT / "config" / "runtime_schedule.json", project_root=ROOT)
        with TemporaryDirectory() as tmp:
            logger = logging.getLogger(f"notification-isolation-{tmp}")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            runner = RuntimeRunner(
                project_root=ROOT,
                config=config,
                calendar=Calendar(),
                store=RuntimeStore(Path(tmp) / "runtime"),
                reader=Reader(),
                pipeline=Pipeline(),
                logger=logger,
                lock_path=Path(tmp) / "runner.lock",
                notifier=BrokenNotifier(),
            )
            result = runner.run(as_of=NOW)
            self.assertEqual(result["results"][0]["status"], "SUCCESS")
            self.assertEqual(result["results"][0]["notification"]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
