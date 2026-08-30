"""Deterministic human-readable TASK_010 monitor report."""

from __future__ import annotations

from trend_monitor.schemas import StockIntradayMonitorResult


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.3f}%"


def render_stock_intraday_report(result: StockIntradayMonitorResult) -> str:
    risk = result.stock_60m_risk
    internal = result.stock_15m_internal
    lines = [f"# {internal.name} ({result.symbol}) 盘中风险监控", ""]
    if risk is None:
        lines.extend(
            [
                "60分钟风险：未生成（当前60分钟周期未完成）",
                f"15分钟内部：{internal.classification.value}",
                f"已完成15m：{internal.completed_15m_count}/4",
                f"周期状态：{internal.period_status.value}",
            ]
        )
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            f"60分钟风险：{risk.risk_light_symbol or ''} {risk.risk_light.value if risk.risk_light else 'BLOCKED'}",
            f"Risk Score：{risk.risk_score if risk.risk_score is not None else 'N/A'}",
            f"风险变化：{risk.risk_direction.value}",
            f"可信度：{risk.confidence.value}",
            "",
            f"本周期：{_pct(risk.current_return)}",
            f"相对市场：{_pct(risk.relative_return)} ({risk.market_relationship})",
            f"连续走弱：{'是' if risk.persistent_weakness else '否'}",
            f"Downside Shock：{'是' if risk.downside_shock else '否'} ({risk.shock_feature_status})",
            f"明显相对弱势：{'是' if risk.relative_weakness else '否'} ({risk.relative_weakness_status})",
            f"市场共振：{'是' if risk.market_resonance else '否'}",
            f"Close Repair：{risk.repair_state.value}",
            "",
            f"15分钟内部：{internal.classification.value}",
            f"方向：{' '.join(internal.direction_sequence)}",
            f"Joint Flags：{', '.join(internal.joint_market_flags) or 'NONE'}",
            "",
            f"市场背景：{risk.market_context.get('market_risk_light') or 'N/A'} / Score {risk.market_context.get('market_risk_score')}",
            f"Market Internal：{risk.market_context.get('market_internal_state') or 'N/A'}",
            "",
            "说明：本结果仅属于盘中风险预警与内部结构解释，不构成交易指令。",
        ]
    )
    return "\n".join(lines) + "\n"
