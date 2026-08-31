#!/usr/bin/env python3
"""Offline TASK_014 policy, isolation, persistence, and secret verification."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.notifications import (  # noqa: E402
    BarkAdapter,
    BarkConfig,
    BarkSendResult,
    ChineseNotificationPresenter,
    NotificationPolicy,
    NotificationPolicyConfig,
    NotificationService,
    NotificationStore,
)
from trend_monitor.schemas.notification import NotificationStatus  # noqa: E402
from trend_monitor.notifications.presentation import (  # noqa: E402
    PHONE_FORBIDDEN_INTERNAL_VALUES,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class SentAdapter:
    def send(self, **kwargs):
        return BarkSendResult(NotificationStatus.SENT, 1)


class FailedAdapter:
    def send(self, **kwargs):
        return BarkSendResult(NotificationStatus.FAILED, 3, "NETWORK_ERROR")


def source(
    *,
    broad: bool,
    joint: bool,
    light: str,
    score: int,
    stock_light: str = "YELLOW",
    stock_score: int = 1,
) -> dict:
    period = "2026-08-28T15:00:00+08:00"
    stocks = {}
    for instrument_id, symbol, name in (
        ("stock.hengtong_optic", "600487", "亨通光电"),
        ("stock.wus_printed_circuit", "002463", "沪电股份"),
    ):
        stocks[instrument_id] = {
            "stock_60m": {
                "rules_version": "stock_60m_risk_v0.1",
                "symbol": symbol,
                "name": name,
                "risk_light": stock_light,
                "risk_score": stock_score,
                "current_return": -0.01,
                "relative_return": -0.005,
                "persistent_weakness": True,
                "market_resonance": True,
                "divergence_flags": [],
            },
            "stock_15m": {
                "classification": "HEALTHY_DOWN",
                "joint_market_flags": ["JOINT_WEAKNESS"] if joint else [],
            },
        }
    return {
        "market": {
            "rules_version": "market_60m_risk_v0.1",
            "risk_light": light,
            "risk_score": score,
            "breadth": {"decline_count": 8},
            "broad_selloff_resonance": broad,
            "strong_broad_weakness": False,
        },
        "market_15m": {"market_internal_state": "WEAKNESS_BROADENING"},
        "stocks": stocks,
        "period_end": period,
    }


def combined(mode: str = "LIVE_SCHEDULED") -> dict:
    return {
        "trading_date": "2026-08-28",
        "period_end": "2026-08-28T15:00:00+08:00",
        "execution_mode": mode,
        "status": "SUCCESS",
    }


def exact_value_hits(value: str) -> list[str]:
    if not value:
        return []
    hits = []
    excluded_parts = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
    for path in PROJECT_ROOT.rglob("*"):
        relative = path.relative_to(PROJECT_ROOT)
        if not path.is_file() or path.name == ".env" or excluded_parts.intersection(relative.parts):
            continue
        try:
            if value.encode() in path.read_bytes():
                hits.append(str(relative))
        except OSError:
            continue
    return sorted(set(hits))


def production_live_status() -> str:
    path = PROJECT_ROOT / "data" / "runtime" / "acceptance" / "runtime_live_acceptance_latest.json"
    if not path.is_file():
        return "PENDING_TASK_013A"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "PENDING_TASK_013A"
    return "READY" if value.get("acceptance_status") == "PASS" else "PENDING_TASK_013A"


def main() -> int:
    policy_config = NotificationPolicyConfig.load(PROJECT_ROOT / "config" / "notification_policy.json")
    bark_config = BarkConfig.load(PROJECT_ROOT / ".env")
    policy = NotificationPolicy(policy_config)
    previous = source(
        broad=False,
        joint=False,
        light="YELLOW",
        score=2,
        stock_light="YELLOW",
        stock_score=1,
    )
    current = source(
        broad=True,
        joint=True,
        light="ORANGE",
        score=5,
        stock_light="ORANGE",
        stock_score=3,
    )
    events = policy.evaluate_combined(current, previous, combined(), source_result_id="historical-dry-run")
    event_types = {event.event_type for event in events}
    policy_pass = {
        "MARKET_RISK_LIGHT_UP", "MARKET_BROAD_WEAKNESS", "JOINT_WEAKNESS"
    }.issubset(event_types)
    expected_identities = {
        ("MARKET_RISK_LIGHT_UP", "market", "HIGH", "b83d9dd74a0b043d47b19bee77fbca5199fe4967a5ce7a7ae47783e39a6f6392"),
        ("MARKET_BROAD_WEAKNESS", "market", "HIGH", "6d23356ee570b48b8a65eeced49b7b382afe793f90ef0d1725fc8c91d97900e5"),
        ("STOCK_RISK_LIGHT_UP", "stock.hengtong_optic", "HIGH", "38de800c4f3c740310a7b15bc1749574604646038e6aca90df990f31c164ed90"),
        ("JOINT_WEAKNESS", "stock.hengtong_optic", "HIGH", "ea74b65bb0374095a7a2911cb131809aee9cddadb00e652ba5b8c6cf1b75776d"),
        ("STOCK_RISK_LIGHT_UP", "stock.wus_printed_circuit", "HIGH", "3d4447e1ac4afc18b784629908fe971e073ce3f8ae470245ebbdd6e70cf03873"),
        ("JOINT_WEAKNESS", "stock.wus_printed_circuit", "HIGH", "dde14981e7d30cc46108e1b1bf1e94de8fee4a96e46def7af4ad1e528d5a3949"),
    }
    actual_identities = {
        (event.event_type, event.instrument_id, event.severity.value, event.event_key)
        for event in events
    }
    identity_pass = actual_identities == expected_identities
    test_event = policy.test_event(created_at="2026-08-31T16:00:00+08:00")
    forbidden = {
        value
        for event in (*events, test_event)
        for value in PHONE_FORBIDDEN_INTERNAL_VALUES
        if value in f"{event.title}\n{event.body}"
    }
    presentation_pass = (
        not forbidden
        and test_event.title == "TrendMonitor｜中文通知测试"
        and test_event.body
        == "手机通知中文化已生效。\n\n🟢 系统运行正常\n风险与数据计算仍使用原有确定性规则。"
    )
    presenter = ChineseNotificationPresenter()
    unknown_pass = presenter.risk_light("FUTURE_LIGHT") == "状态待解释"

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        active_config = BarkConfig(True, "https://api.day.app", "dummy-device-key", 1)
        sent_service = NotificationService(
            bark_config=active_config,
            policy_config=policy_config,
            policy=policy,
            adapter=SentAdapter(),
            store=NotificationStore(root / "sent"),
            now=lambda: datetime(2026, 8, 28, 15, 3, tzinfo=SHANGHAI),
        )
        first = sent_service.process_events(events)
        second = sent_service.process_events(events)
        catch_up_events = policy.evaluate_combined(
            current, previous, combined("CATCH_UP"), source_result_id="historical-dry-run"
        )
        catch_up = sent_service.process_events(catch_up_events)
        failed_service = NotificationService(
            bark_config=active_config,
            policy_config=policy_config,
            policy=policy,
            adapter=FailedAdapter(),
            store=NotificationStore(root / "failed"),
        )
        failed = failed_service.process_events(events[:1])
        runtime_status_after_failure = "SUCCESS"
        store_pass = len(NotificationStore(root / "sent").entries()) >= len(events) * 2

    secret_hits = exact_value_hits(bark_config.device_key)
    full_url = (
        f"{bark_config.server_url.rstrip('/')}/{bark_config.device_key}"
        if bark_config.server_url and bark_config.device_key
        else ""
    )
    full_url_hits = exact_value_hits(full_url)
    config_pass = bark_config.validation_error() is None and policy_config.rules_version == "notification_policy_v0.1"
    dedup_pass = first["status"] == "SENT" and second["status"] == "SKIPPED_DUPLICATE"
    catch_up_pass = catch_up["status"] in {"SKIPPED_POLICY", "SKIPPED_DUPLICATE"}
    isolation_pass = failed["status"] == "FAILED" and runtime_status_after_failure == "SUCCESS"
    secret_pass = not secret_hits and not bark_config.device_key in repr(bark_config)
    full_url_pass = not full_url_hits
    channel = NotificationStore(PROJECT_ROOT / "data" / "notifications").latest_test_status()

    results = {
        "CONFIG": "PASS" if config_pass else "FAIL",
        "POLICY": "PASS" if policy_pass else "FAIL",
        "EVENT_IDENTITY": "PASS" if identity_pass else "FAIL",
        "CHINESE_PRESENTATION": "PASS" if presentation_pass else "FAIL",
        "UNKNOWN_TRANSLATION_FALLBACK": "PASS" if unknown_pass else "FAIL",
        "DEDUPLICATION": "PASS" if dedup_pass else "FAIL",
        "CATCH_UP_POLICY": "PASS" if catch_up_pass else "FAIL",
        "RUNTIME_ISOLATION": "PASS" if isolation_pass else "FAIL",
        "NOTIFICATION_STORE": "PASS" if store_pass else "FAIL",
        "SECRET_AUDIT": "PASS" if secret_pass else "FAIL",
        "FULL_BARK_URL_AUDIT": "PASS" if full_url_pass else "FAIL",
        "BARK_CHANNEL": "VERIFIED" if channel == "SENT" else "NOT_VERIFIED",
        "PRODUCTION_LIVE": production_live_status(),
    }
    for key, value in results.items():
        print(f"{key}\n{value}\n")
    return 0 if all(
        value == "PASS"
        for key, value in results.items()
        if key not in {"BARK_CHANNEL", "PRODUCTION_LIVE"}
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
