"""Human rendering for auxiliary industry context without trading advice."""

from __future__ import annotations

from trend_monitor.schemas import StockIndustryContextResult


def render_stock_industry_context_report(value: StockIndustryContextResult, *, stock_name: str) -> str:
    lines = [
        f"# {stock_name} 行业盘中Context",
        "",
        f"个股60分钟风险：{value.stock_risk_light or 'N/A'} / Score {value.stock_risk_score}",
        "",
        f"行业Benchmark：{value.industry_name} ({value.industry_provider_symbol})",
        f"Mapping：{value.industry_mapping_type} / {value.industry_confidence}",
        f"Industry Context：{value.status}",
    ]
    if value.status != "READY":
        lines.extend(
            [
                f"原因：{value.unavailable_reason}",
                "15m：UNAVAILABLE",
                "60m：UNAVAILABLE",
                "",
                "说明：行业身份已确认，但没有可信DIRECT行业分钟K，因此不生成行业收益、共振或相对弱势判断。",
            ]
        )
    else:
        lines.extend(
            [
                f"行业收益：{value.industry_return:.4%}" if value.industry_return is not None else "行业收益：N/A",
                f"个股相对行业：{value.stock_industry_relative_return:.4%}"
                if value.stock_industry_relative_return is not None
                else "个股相对行业：N/A",
                f"三层Context：{value.context_classification}",
            ]
        )
    lines.extend(["", "本结果仅为盘中风险解释，不构成交易建议。", ""])
    return "\n".join(lines)
