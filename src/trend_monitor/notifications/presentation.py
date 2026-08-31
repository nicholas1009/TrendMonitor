"""Simplified-Chinese presentation for phone notification text only."""

from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Any, Iterable, Mapping, Sequence


UNKNOWN_TRANSLATION = "状态待解释"

RISK_LIGHT_LABELS = {
    "GREEN": "绿色",
    "YELLOW": "黄色",
    "ORANGE": "橙色",
    "RED": "红色",
}
RISK_LIGHT_EMOJI = {
    "GREEN": "🟢",
    "YELLOW": "🟡",
    "ORANGE": "🟠",
    "RED": "🔴",
}
RISK_DIRECTION_LABELS = {
    "RISING": "风险上升",
    "FALLING": "风险下降",
    "FLAT": "风险持平",
    "N/A": "暂无对比",
}
RISK_CHANGE_LABELS = {
    "RISING": "上升",
    "FALLING": "下降",
    "FLAT": "持平",
    "N/A": "暂无对比",
}
MARKET_INTERNAL_LABELS = {
    "WEAKNESS_BROADENING": "弱势扩散",
    "REPAIR_BROADENING": "修复扩散",
    "INTERNAL_MIXED": "内部结构分化",
    "DATA_INCOMPLETE": "数据不完整",
}
STOCK_15M_LABELS = {
    "HEALTHY_UP": "持续走强",
    "HEALTHY_DOWN": "持续走弱",
    "LATE_REPAIR": "后半段修复",
    "FAILED_REPAIR": "修复失败",
    "LATE_WEAKENING": "后半段转弱",
    "MIXED": "多空混合",
    "EARLY_STRENGTH": "早段走强",
    "EARLY_WEAKNESS": "早段走弱",
    "EARLY_MIXED": "早段分化",
    "UNAVAILABLE": "暂不可用",
}
FLAG_LABELS = {
    "JOINT_WEAKNESS": "个股与市场共振走弱",
    "JOINT_REPAIR": "个股与市场同步修复",
    "STOCK_REPAIR_AGAINST_WEAK_MARKET": "市场偏弱时个股修复",
    "STOCK_WEAK_MARKET_STABLE": "市场平稳时个股偏弱",
    "STOCK_STRONG_MARKET_WEAK": "市场偏弱时个股较强",
}
RUNTIME_STATUS_LABELS = {
    "DATA_INCOMPLETE": "数据不完整",
    "PROVIDER_FAILURE": "数据源异常",
    "RUNTIME_FAILED": "运行异常",
    "SCHEMA_OR_CONTRACT_ERROR": "数据或规则契约异常",
    "NETWORK_ERROR": "网络异常",
    "TIMEOUT": "请求超时",
    "TEMPORARY_PROVIDER_ERROR": "数据源临时异常",
    "RATE_LIMIT": "请求频率受限",
    "PIPELINE_FAILED": "处理流程失败",
    "CALENDAR_UNAVAILABLE": "交易日历不可用",
}
RUNTIME_STAGE_LABELS = {
    "MARKET_DATA_REFRESH": "市场数据",
    "RISK_INPUT_ASSEMBLY": "风险输入",
    "MARKET_60M_RISK": "大盘60分钟风险",
    "MARKET_15M_INTERNAL": "大盘15分钟结构",
    "STOCK_60M_15M": "个股风险",
    "COMBINED_RUNTIME_REPORT": "汇总结果",
    "TRADING_DAY_GATE": "交易日历",
    "SECURITY_GATE": "安全检查",
    "RUNTIME": "运行流程",
}
STOCK_NAMES = {
    "stock.hengtong_optic": "亨通光电",
    "stock.wus_printed_circuit": "沪电股份",
}

PHONE_FORBIDDEN_INTERNAL_VALUES = frozenset(
    {
        *RISK_LIGHT_LABELS,
        *RISK_DIRECTION_LABELS,
        *MARKET_INTERNAL_LABELS,
        *STOCK_15M_LABELS,
        *FLAG_LABELS,
        "SUCCESS_WITH_DEGRADATION",
        "PROVIDER_FAILURE",
        "RUNTIME_FAILED",
    }
)
INTERNAL_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]*\b")


def ensure_phone_text(title: str, body: str) -> None:
    """Reject accidental presentation leakage without inspecting stored data."""

    leaked = PHONE_FORBIDDEN_INTERNAL_VALUES.intersection(
        INTERNAL_TOKEN.findall(f"{title}\n{body}")
    )
    if leaked:
        raise ValueError(f"phone notification contains internal enum: {sorted(leaked)}")


class ChineseNotificationPresenter:
    """Translate internal values at the final human-readable boundary."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger(__name__)

    def _translate(
        self,
        value: Any,
        mapping: Mapping[str, str],
        *,
        category: str,
    ) -> str:
        key = "N/A" if value is None else str(value)
        translated = mapping.get(key)
        if translated is not None:
            return translated
        self.logger.warning(
            "UNKNOWN_TRANSLATION category=%s value=%s",
            category,
            key,
        )
        return UNKNOWN_TRANSLATION

    def risk_light(self, value: Any) -> str:
        return self._translate(value, RISK_LIGHT_LABELS, category="risk_light")

    def risk_direction(self, value: Any) -> str:
        return self._translate(
            value,
            RISK_DIRECTION_LABELS,
            category="risk_direction",
        )

    def risk_change(self, value: Any) -> str:
        return self._translate(value, RISK_CHANGE_LABELS, category="risk_direction")

    def market_internal(self, value: Any) -> str:
        return self._translate(
            value,
            MARKET_INTERNAL_LABELS,
            category="market_internal_state",
        )

    def stock_15m(self, value: Any) -> str:
        return self._translate(
            value,
            STOCK_15M_LABELS,
            category="stock_15m_classification",
        )

    def flag(self, value: Any) -> str:
        return self._translate(value, FLAG_LABELS, category="risk_flag")

    def runtime_status(self, value: Any) -> str:
        return self._translate(
            value,
            RUNTIME_STATUS_LABELS,
            category="runtime_status",
        )

    def runtime_stage(self, value: Any) -> str:
        return self._translate(
            value,
            RUNTIME_STAGE_LABELS,
            category="runtime_stage",
        )

    def risk_line(self, light: Any, score: Any) -> str:
        key = "N/A" if light is None else str(light)
        emoji = RISK_LIGHT_EMOJI.get(key, "⚪")
        if key not in RISK_LIGHT_EMOJI:
            self.logger.warning(
                "UNKNOWN_TRANSLATION category=risk_light_emoji value=%s",
                key,
            )
        score_text = "暂无" if score is None else str(score)
        return f"{emoji} {self.risk_light(light)} · 风险分 {score_text}"

    @staticmethod
    def percent(value: Any) -> str:
        if value is None:
            return "暂无数据"
        try:
            return f"{float(value):+.2%}"
        except (TypeError, ValueError):
            return "暂无数据"

    @staticmethod
    def yes_no(value: Any) -> str:
        return "是" if bool(value) else "否"

    @staticmethod
    def _breadth_value(value: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in value:
                return value[key]
        return "暂无数据"

    def translated_flags(self, values: Iterable[Any]) -> list[str]:
        output: list[str] = []
        for value in values:
            translated = self.flag(value)
            if translated not in output:
                output.append(translated)
        return output

    def market_notification_body(self, current: Mapping[str, Any]) -> str:
        market = current.get("market", {})
        market_15m = current.get("market_15m", {})
        breadth = market.get("breadth", {})
        advancers = self._breadth_value(breadth, "advance_count", "advancers")
        decliners = self._breadth_value(breadth, "decline_count", "decliners")
        lines = [
            self.risk_line(market.get("risk_light"), market.get("risk_score")),
            f"风险变化：{self.risk_change(market.get('risk_direction'))}",
            "",
            "8个指数：",
            f"上涨 {advancers} / 下跌 {decliners}",
            "",
            f"市场结构：{self.market_internal(market_15m.get('market_internal_state'))}",
        ]
        for instrument_id, item in current.get("stocks", {}).items():
            risk = item.get("stock_60m", {})
            internal = item.get("stock_15m", {})
            name = STOCK_NAMES.get(
                str(instrument_id),
                str(risk.get("name") or "关注个股"),
            )
            flags = self.translated_flags(
                [
                    *risk.get("divergence_flags", []),
                    *internal.get("joint_market_flags", []),
                ]
            )
            lines.extend(
                [
                    "",
                    f"{name}：",
                    self.risk_line(risk.get("risk_light"), risk.get("risk_score")),
                    *flags,
                ]
            )
        body = "\n".join(lines)
        ensure_phone_text("", body)
        return body

    def market_repair_body(
        self,
        current: Mapping[str, Any],
        previous: Mapping[str, Any],
    ) -> str:
        market = current.get("market", {})
        previous_market = previous.get("market", {})
        internal = current.get("market_15m", {})
        body = "\n".join(
            [
                "风险由：",
                self.risk_line(previous_market.get("risk_light"), previous_market.get("risk_score")).split(" · ")[0],
                "下降至：",
                self.risk_line(market.get("risk_light"), market.get("risk_score")).split(" · ")[0],
                "",
                f"当前风险分：{market.get('risk_score', '暂无')}",
                f"15分钟结构：{self.market_internal(internal.get('market_internal_state'))}",
            ]
        )
        ensure_phone_text("", body)
        return body

    def stock_notification_body(
        self,
        risk: Mapping[str, Any],
        internal: Mapping[str, Any],
    ) -> str:
        flags = self.translated_flags(
            [
                *risk.get("divergence_flags", []),
                *internal.get("joint_market_flags", []),
            ]
        )
        lines = [
            self.risk_line(risk.get("risk_light"), risk.get("risk_score")),
            f"风险变化：{self.risk_change(risk.get('risk_direction'))}",
            "",
            f"本周期：{self.percent(risk.get('current_return'))}",
            f"相对市场：{self.percent(risk.get('relative_return'))}",
            "",
            f"连续走弱：{self.yes_no(risk.get('persistent_weakness'))}",
            f"市场共振：{self.yes_no(risk.get('market_resonance'))}",
            "",
            f"15分钟结构：{self.stock_15m(internal.get('classification'))}",
        ]
        if flags:
            lines.extend(["", *flags])
        body = "\n".join(lines)
        ensure_phone_text("", body)
        return body

    @staticmethod
    def data_incomplete(period_end: str) -> tuple[str, str]:
        label = period_end[11:16] if len(period_end) >= 16 else "当前"
        return (
            "TrendMonitor｜数据不完整",
            f"{label}周期未能生成完整风险结果。\n\n"
            "数据状态：不完整\n"
            "请检查数据源或运行状态。",
        )

    def runtime_failure(
        self,
        record: Mapping[str, Any],
        *,
        event_type: str,
        title: str,
    ) -> tuple[str, str]:
        error = record.get("error_summary") or {}
        period_end = str(record.get("period_end") or "")
        period_label = period_end[11:16] if len(period_end) >= 16 else "当前"
        category = error.get("error_category") or event_type
        stage = error.get("stage") or "RUNTIME"
        retries = error.get("retry_count", 0)
        body = "\n".join(
            [
                f"{period_label}周期未能生成完整风险结果。",
                "",
                f"状态：{self.runtime_status(category)}",
                f"阶段：{self.runtime_stage(stage)}",
                f"重试：{retries}次",
                "",
                "请检查数据源或运行状态。",
            ]
        )
        ensure_phone_text(title, body)
        return title, body

    @staticmethod
    def test_notification() -> tuple[str, str]:
        return (
            "TrendMonitor｜中文通知测试",
            "手机通知中文化已生效。\n\n"
            "🟢 系统运行正常\n"
            "风险与数据计算仍使用原有确定性规则。",
        )

    def catch_up_summary(
        self,
        reports: Sequence[Mapping[str, Any]],
        *,
        final_flags: Mapping[str, Sequence[Any]] | None = None,
    ) -> tuple[str, str]:
        ordered = sorted(reports, key=lambda item: str(item.get("period_end", "")))
        if not ordered:
            raise ValueError("catch-up summary requires at least one report")
        final = ordered[-1]
        trading_date = str(final.get("trading_date") or final.get("period_end", "")[:10])
        try:
            parsed_date = datetime.fromisoformat(trading_date)
            date_label = f"{parsed_date.month}月{parsed_date.day}日"
        except ValueError:
            date_label = "当日"
        title = f"TrendMonitor｜{date_label}补跑完成"
        lines = ["【今日60分钟风险】", ""]
        for item in ordered:
            market = item.get("market", {})
            period_end = str(item.get("period_end", ""))
            label = period_end[11:16] if len(period_end) >= 16 else "--:--"
            lines.append(
                f"{label}  {self.risk_line(market.get('risk_light'), market.get('risk_score'))}"
                f" · {self.risk_change(market.get('risk_direction'))}"
            )

        market = final.get("market", {})
        stocks = final.get("stocks", {})
        lines.extend(
            [
                "",
                "【15:00最终状态】",
                "",
                "大盘：",
                self.risk_line(market.get("risk_light"), market.get("risk_score")),
                f"15分钟结构：{self.market_internal(market.get('15m_internal'))}",
            ]
        )
        flag_values = final_flags or {}
        for instrument_id in (
            "stock.hengtong_optic",
            "stock.wus_printed_circuit",
        ):
            stock = stocks.get(instrument_id, {})
            name = STOCK_NAMES[instrument_id]
            values = [
                *stock.get("divergence_flags", []),
                *stock.get("joint_market_flags", []),
                *flag_values.get(instrument_id, ()),
            ]
            lines.extend(
                [
                    "",
                    f"{name}：",
                    self.risk_line(stock.get("risk_light"), stock.get("risk_score")),
                    f"15分钟结构：{self.stock_15m(stock.get('15m_classification'))}",
                    *self.translated_flags(values),
                ]
            )
        coverage = final.get("data", {}).get("market_index_coverage", "暂无数据")
        lines.extend(["", f"数据完整度：{coverage}", "运行模式：断电后补跑"])
        statuses = {str(item.get("status")) for item in ordered}
        if "DATA_INCOMPLETE" in statuses:
            lines.extend(["", "数据状态：不完整"])
        elif "SUCCESS_WITH_DEGRADATION" in statuses:
            lines.extend(["", "数据状态：正常", "部分非核心字段仅作参考"])
        else:
            lines.extend(["", "数据状态：正常"])
        body = "\n".join(lines)
        ensure_phone_text(title, body)
        return title, body
