"""Deterministic human report for 15m internal structure results."""

from __future__ import annotations

from trend_monitor.schemas import InternalPeriodStatus, Market15mInternalResult, MarketInternalState


def render_market_15m_internal_report(result: Market15mInternalResult) -> str:
    counts = result.classification_counts
    groups = {item.group: item.state for item in result.group_states}
    linked = result.linked_60m_risk
    if result.market_internal_state is MarketInternalState.DATA_INCOMPLETE:
        explanation = "有效指数或分组覆盖不足，市场内部状态为DATA_INCOMPLETE，不进行推断。"
    elif result.period_status is InternalPeriodStatus.IN_PROGRESS:
        explanation = "这是未完成60分钟周期的早期观察，不进入完成周期历史结果。"
    elif result.market_internal_state is MarketInternalState.REPAIR_BROADENING:
        explanation = (
            f"60分钟风险灯保持{linked.get('risk_light_symbol') or ''} {linked['risk_light']}，"
            "但本周期内部出现较广泛修复；不修改60分钟Risk Score。"
        )
    elif result.market_internal_state is MarketInternalState.WEAKNESS_BROADENING:
        explanation = (
            f"60分钟风险灯保持{linked.get('risk_light_symbol') or ''} {linked['risk_light']}，"
            "15分钟内部结构显示弱势仍在扩散；不修改60分钟Risk Score。"
        )
    else:
        explanation = (
            f"60分钟风险灯保持{linked.get('risk_light_symbol') or ''} {linked['risk_light']}，"
            "15分钟内部结构分化；不修改60分钟Risk Score。"
        )
    return "\n".join(
        (
            "# 15分钟内部结构辅助",
            "",
            f"周期：{result.period_60m_start} → {result.period_60m_end}",
            f"周期状态：{result.period_status.value}",
            f"已完成15m：{result.completed_15m_count}",
            f"15分钟内部结构：{result.market_internal_state.value}",
            "",
            f"冻结60m风险灯：{linked.get('risk_light_symbol') or ''} {linked['risk_light']}",
            f"冻结60m Risk Score：{linked['risk_score']}",
            f"冻结60m Risk Direction：{linked['risk_direction']}",
            "",
            f"持续走弱：{counts.get('HEALTHY_DOWN', 0)}/8",
            f"后半段转弱：{counts.get('LATE_WEAKENING', 0)}/8",
            f"修复失败：{counts.get('FAILED_REPAIR', 0)}/8",
            f"后半段修复：{counts.get('LATE_REPAIR', 0)}/8",
            f"内部健康上涨：{counts.get('HEALTHY_UP', 0)}/8",
            "",
            f"权重组：{groups.get('LARGE_CAP', 'UNAVAILABLE')}",
            f"广谱组：{groups.get('BROAD_MARKET', 'UNAVAILABLE')}",
            f"中小盘：{groups.get('MID_SMALL', 'UNAVAILABLE')}",
            f"成长组：{groups.get('GROWTH', 'UNAVAILABLE')}",
            "",
            "## 说明",
            "",
            explanation,
            "",
            "## 数据边界",
            "",
            "- 分类只使用Preflight允许的15m System Bar Close。",
            "- High/Low、Index Volume和Turnover不决定分类。",
            "- 本结果没有独立风险灯、Risk Score、买卖或仓位含义。",
            "",
        )
    )
