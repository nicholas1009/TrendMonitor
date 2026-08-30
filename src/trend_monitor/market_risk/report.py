"""Deterministic human-readable report for a Market 60m Risk Result."""

from __future__ import annotations

from trend_monitor.schemas import Market60mRiskResult, RiskChangeDirection


_RISK_ARROW = {
    RiskChangeDirection.RISING: "↑ 风险上升",
    RiskChangeDirection.FLAT: "→ 风险持平",
    RiskChangeDirection.FALLING: "↓ 风险下降",
    RiskChangeDirection.NOT_AVAILABLE: "N/A",
}


def render_market_60m_report(result: Market60mRiskResult) -> str:
    if result.status != "READY":
        return (
            "# 大盘60分钟风险\n\n"
            "状态：BLOCKED / DATA_INCOMPLETE\n\n"
            f"数据原因：{result.data_quality.get('unavailable', {})}\n"
        )
    groups = {item.group: item.group_direction for item in result.group_states}
    risks = []
    if result.broad_selloff_resonance:
        risks.append("市场多数指数同步走弱（BROAD_SELLOFF_RESONANCE）。")
    if result.strong_broad_weakness:
        risks.append("多数指数连续走弱（STRONG_BROAD_WEAKNESS）。")
    if result.weighted_support_distortion:
        risks.append("出现权重强、市场多数偏弱的结构性分化。")
    if result.small_cap_stress:
        risks.append("中小盘压力扩大（SMALL_CAP_STRESS）。")
    if not risks:
        risks.append("本周期未触发v0.1结构性风险Flag。")
    repairs = (
        f"{result.repair_count}/8个指数出现Close层修复；BROAD_REPAIR已触发。"
        if result.broad_repair
        else f"{result.repair_count}/8个指数出现Close层修复，未达到BROAD_REPAIR。"
    )
    return "\n".join(
        (
            "# 大盘60分钟风险",
            "",
            f"大盘60分钟风险：{result.risk_light_symbol} {result.risk_light.value}",
            f"风险变化：{_RISK_ARROW[result.risk_direction]}",
            f"Risk Score：{result.risk_score}",
            f"可信度：{result.signal_confidence.value}",
            "",
            f"上涨：{result.breadth['advancers']}/8",
            f"下跌：{result.breadth['decliners']}/8",
            f"连续走弱：{result.persistent_weakness['count']}/8",
            f"Downside Shock：{result.downside_shocks['count']}",
            f"权重失真：{'是' if result.weighted_support_distortion else '否'}",
            "",
            f"大型权重：{groups['LARGE_CAP']}",
            f"广谱市场：{groups['BROAD_MARKET']}",
            f"中小盘：{groups['MID_SMALL']}",
            f"成长：{groups['GROWTH']}",
            "",
            "## 主要风险",
            "",
            *[f"- {item}" for item in risks],
            "",
            "## 修复信号",
            "",
            f"- {repairs}",
            "",
            "## 数据限制",
            "",
            "- v0.1仅使用Preflight批准的60m Close及其确定性派生Feature。",
            "- High/Low、指数Volume和Turnover不参与评分。",
            "- 该结果是盘中风险预警，不是买卖、仓位或日线交易裁决。",
            "",
        )
    )
