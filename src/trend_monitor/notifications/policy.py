"""Deterministic event-driven notification policy over frozen Risk Results."""

from __future__ import annotations

from typing import Any

from trend_monitor.schemas.notification import NotificationEvent, NotificationSeverity

from .config import NotificationPolicyConfig
from .presentation import ChineseNotificationPresenter, STOCK_NAMES, ensure_phone_text


LIGHT_ORDER = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
PROHIBITED_ADVICE = ("买入", "卖出", "减仓", "加仓", "止损", "平仓")


def _light(value: Any) -> str | None:
    text = str(value) if value is not None else None
    return text if text in LIGHT_ORDER else None


def _flags(source: dict[str, Any] | None, instrument_id: str) -> tuple[set[str], set[str]]:
    if source is None:
        return set(), set()
    item = source.get("stocks", {}).get(instrument_id, {})
    risk = item.get("stock_60m", {})
    internal = item.get("stock_15m", {})
    return set(risk.get("divergence_flags", [])), set(internal.get("joint_market_flags", []))


class NotificationPolicy:
    def __init__(
        self,
        config: NotificationPolicyConfig,
        *,
        presenter: ChineseNotificationPresenter | None = None,
    ):
        self.config = config
        self.presenter = presenter or ChineseNotificationPresenter()

    def _event(
        self,
        *,
        event_type: str,
        instrument_id: str,
        trading_date: str,
        period_end: str,
        rules_version: str,
        severity: NotificationSeverity,
        title: str,
        body: str,
        execution_mode: str,
        source_result_id: str,
    ) -> NotificationEvent:
        if any(word in title or word in body for word in PROHIBITED_ADVICE):
            raise ValueError("notification contains prohibited trading advice")
        ensure_phone_text(title, body)
        return NotificationEvent(
            event_type=event_type,
            instrument_id=instrument_id,
            trading_date=trading_date,
            period_end=period_end,
            rules_version=rules_version,
            severity=severity,
            title=title,
            body=body,
            execution_mode=execution_mode,
            source_result_id=source_result_id,
            group=self.config.group,
        )

    def evaluate_combined(
        self,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
        combined: dict[str, Any],
        *,
        source_result_id: str,
    ) -> tuple[NotificationEvent, ...]:
        execution_mode = str(combined["execution_mode"])
        trading_date = str(combined["trading_date"])
        period_end = str(combined["period_end"])
        if combined.get("status") == "DATA_INCOMPLETE":
            title, body = self.presenter.data_incomplete(period_end)
            return (
                self._event(
                    event_type="DATA_INCOMPLETE",
                    instrument_id="market",
                    trading_date=trading_date,
                    period_end=period_end,
                    rules_version="intraday_runtime_v0.1",
                    severity=NotificationSeverity.ERROR,
                    title=title,
                    body=body,
                    execution_mode=execution_mode,
                    source_result_id=source_result_id,
                ),
            )

        events: list[NotificationEvent] = []
        market = current["market"]
        previous_market = previous.get("market", {}) if previous else {}
        current_light = _light(market.get("risk_light"))
        previous_light = _light(previous_market.get("risk_light"))
        current_score = market.get("risk_score")
        previous_score = previous_market.get("risk_score")
        market_body = self.presenter.market_notification_body(current)
        market_rules = str(market.get("rules_version", "market_60m_risk_v0.1"))

        if current_light and previous_light and LIGHT_ORDER[current_light] > LIGHT_ORDER[previous_light]:
            events.append(
                self._event(
                    event_type="MARKET_RISK_LIGHT_UP",
                    instrument_id="market",
                    trading_date=trading_date,
                    period_end=period_end,
                    rules_version=market_rules,
                    severity=NotificationSeverity.HIGH if current_light in {"ORANGE", "RED"} else NotificationSeverity.WARNING,
                    title="TrendMonitor｜大盘风险上升",
                    body=market_body,
                    execution_mode=execution_mode,
                    source_result_id=source_result_id,
                )
            )
        elif (
            current_light
            and current_light == previous_light
            and current_score is not None
            and previous_score is not None
            and int(current_score) > int(previous_score)
        ):
            events.append(
                self._event(
                    event_type="MARKET_SCORE_UP",
                    instrument_id="market",
                    trading_date=trading_date,
                    period_end=period_end,
                    rules_version=market_rules,
                    severity=NotificationSeverity.HIGH if current_light in {"ORANGE", "RED"} else NotificationSeverity.WARNING,
                    title="TrendMonitor｜大盘风险分数上升",
                    body=market_body,
                    execution_mode=execution_mode,
                    source_result_id=source_result_id,
                )
            )
        if current_light and previous_light and LIGHT_ORDER[current_light] < LIGHT_ORDER[previous_light]:
            repair_body = self.presenter.market_repair_body(current, previous or {})
            events.append(
                self._event(
                    event_type="MARKET_REPAIR",
                    instrument_id="market",
                    trading_date=trading_date,
                    period_end=period_end,
                    rules_version=market_rules,
                    severity=NotificationSeverity.INFO,
                    title="TrendMonitor｜市场风险缓解",
                    body=repair_body,
                    execution_mode=execution_mode,
                    source_result_id=source_result_id,
                )
            )

        broad_now = bool(market.get("broad_selloff_resonance") or market.get("strong_broad_weakness"))
        broad_before = bool(previous_market.get("broad_selloff_resonance") or previous_market.get("strong_broad_weakness"))
        if broad_now and not broad_before:
            events.append(
                self._event(
                    event_type="MARKET_BROAD_WEAKNESS",
                    instrument_id="market",
                    trading_date=trading_date,
                    period_end=period_end,
                    rules_version=market_rules,
                    severity=NotificationSeverity.HIGH,
                    title="TrendMonitor｜市场弱势扩散",
                    body=market_body,
                    execution_mode=execution_mode,
                    source_result_id=source_result_id,
                )
            )

        for instrument_id, item in current.get("stocks", {}).items():
            risk = item.get("stock_60m", {})
            internal = item.get("stock_15m", {})
            previous_item = previous.get("stocks", {}).get(instrument_id, {}) if previous else {}
            previous_risk = previous_item.get("stock_60m", {})
            current_stock_light = _light(risk.get("risk_light"))
            previous_stock_light = _light(previous_risk.get("risk_light"))
            stock_rules = str(risk.get("rules_version", "stock_60m_risk_v0.1"))
            name = STOCK_NAMES.get(instrument_id, str(risk.get("name") or instrument_id))
            divergence_now, joint_now = _flags(current, instrument_id)
            divergence_before, joint_before = _flags(previous, instrument_id)
            stock_body = self.presenter.stock_notification_body(risk, internal)
            if (
                current_stock_light
                and previous_stock_light
                and LIGHT_ORDER[current_stock_light] > LIGHT_ORDER[previous_stock_light]
            ):
                events.append(
                    self._event(
                        event_type="STOCK_RISK_LIGHT_UP",
                        instrument_id=instrument_id,
                        trading_date=trading_date,
                        period_end=period_end,
                        rules_version=stock_rules,
                        severity=NotificationSeverity.HIGH if current_stock_light in {"ORANGE", "RED"} else NotificationSeverity.WARNING,
                        title=f"TrendMonitor｜{name}风险变化",
                        body=stock_body,
                        execution_mode=execution_mode,
                        source_result_id=source_result_id,
                    )
                )
            elif (
                current_stock_light
                and current_stock_light == previous_stock_light
                and risk.get("risk_score") is not None
                and previous_risk.get("risk_score") is not None
                and int(risk["risk_score"]) > int(previous_risk["risk_score"])
            ):
                events.append(
                    self._event(
                        event_type="STOCK_SCORE_UP",
                        instrument_id=instrument_id,
                        trading_date=trading_date,
                        period_end=period_end,
                        rules_version=stock_rules,
                        severity=NotificationSeverity.HIGH if current_stock_light in {"ORANGE", "RED"} else NotificationSeverity.WARNING,
                        title=f"TrendMonitor｜{name}风险变化",
                        body=stock_body,
                        execution_mode=execution_mode,
                        source_result_id=source_result_id,
                    )
                )
            if "JOINT_WEAKNESS" in joint_now and "JOINT_WEAKNESS" not in joint_before:
                events.append(
                    self._event(
                        event_type="JOINT_WEAKNESS",
                        instrument_id=instrument_id,
                        trading_date=trading_date,
                        period_end=period_end,
                        rules_version=stock_rules,
                        severity=NotificationSeverity.HIGH if current_stock_light in {"ORANGE", "RED"} else NotificationSeverity.WARNING,
                        title=f"TrendMonitor｜{name}市场共振走弱",
                        body=stock_body,
                        execution_mode=execution_mode,
                        source_result_id=source_result_id,
                    )
                )
            independent_flag = "STOCK_WEAK_MARKET_STABLE"
            if independent_flag in divergence_now and independent_flag not in divergence_before:
                events.append(
                    self._event(
                        event_type="INDEPENDENT_WEAKNESS",
                        instrument_id=instrument_id,
                        trading_date=trading_date,
                        period_end=period_end,
                        rules_version=stock_rules,
                        severity=NotificationSeverity.WARNING,
                        title=f"TrendMonitor｜{name}独立偏弱",
                        body=stock_body,
                        execution_mode=execution_mode,
                        source_result_id=source_result_id,
                    )
                )
        return tuple(events)

    def evaluate_runtime_failure(self, record: dict[str, Any]) -> tuple[NotificationEvent, ...]:
        error = record.get("error_summary") or {}
        category = str(error.get("error_category") or "RUNTIME_FAILED")
        stage = str(error.get("stage") or "RUNTIME")
        if category == "DATA_INCOMPLETE":
            event_type = "DATA_INCOMPLETE"
            title = "TrendMonitor｜数据不完整"
        elif stage in {"MARKET_DATA_REFRESH", "RISK_INPUT_ASSEMBLY"} and category in {
            "NETWORK_ERROR", "TIMEOUT", "TEMPORARY_PROVIDER_ERROR", "RATE_LIMIT", "PIPELINE_FAILED"
        }:
            event_type = "PROVIDER_FAILURE"
            title = "TrendMonitor｜数据源异常"
        else:
            event_type = "RUNTIME_FAILED"
            title = "TrendMonitor｜运行异常"
        trading_date = str(record.get("trading_date") or "N/A")
        period_end = str(
            record.get("period_end")
            or (f"{trading_date}T00:00:00+08:00" if trading_date != "N/A" else "N/A")
        )
        title, body = self.presenter.runtime_failure(
            record,
            event_type=event_type,
            title=title,
        )
        return (
            self._event(
                event_type=event_type,
                instrument_id="runtime",
                trading_date=trading_date,
                period_end=period_end,
                rules_version=str(record.get("rules_versions", {}).get("runtime", "intraday_runtime_v0.1")),
                severity=NotificationSeverity.ERROR,
                title=title,
                body=body,
                execution_mode=str(record.get("execution_mode") or "UNKNOWN"),
                source_result_id=str(record.get("run_id") or "runtime"),
            ),
        )

    def evaluate_auction_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        source_result_id: str,
    ) -> tuple[NotificationEvent, ...]:
        title, body = self.presenter.auction_snapshot(
            snapshot["items"],
            execution_mode=str(snapshot["execution_mode"]),
        )
        return (
            self._event(
                event_type="AUCTION_FINAL_SNAPSHOT",
                instrument_id="auction.final_snapshot",
                trading_date=str(snapshot["trading_date"]),
                period_end=str(snapshot["scheduled_at"]),
                rules_version=self.config.rules_version,
                severity=NotificationSeverity.INFO,
                title=title,
                body=body,
                execution_mode=str(snapshot["execution_mode"]),
                source_result_id=source_result_id,
            ),
        )

    def evaluate_auction_failure(
        self,
        record: dict[str, Any],
    ) -> tuple[NotificationEvent, ...]:
        title, body = self.presenter.auction_failure(record["incomplete_names"])
        return (
            self._event(
                event_type="AUCTION_FINAL_SNAPSHOT_FAILED",
                instrument_id="auction.final_snapshot",
                trading_date=str(record["trading_date"]),
                period_end=str(record["scheduled_at"]),
                rules_version=self.config.rules_version,
                severity=NotificationSeverity.ERROR,
                title=title,
                body=body,
                execution_mode=str(record["execution_mode"]),
                source_result_id=str(record["run_id"]),
            ),
        )

    def test_event(self, *, created_at: str) -> NotificationEvent:
        title, body = self.presenter.test_notification()
        return self._event(
            event_type="TEST",
            instrument_id="notification.test",
            trading_date=created_at[:10],
            period_end=created_at,
            rules_version=self.config.rules_version,
            severity=NotificationSeverity.INFO,
            title=title,
            body=body,
            execution_mode="MANUAL_TEST",
            source_result_id="manual_bark_test",
        )
