# TASK_010｜两只正式个股60m风险与15m内部结构验证 v0.1

本Task只覆盖`stock.hengtong_optic / 600487`与
`stock.wus_printed_circuit / 002463`。正式分析入口限定为Stock Risk Input、冻结的
Market 60m Risk Result和Market 15m Internal Result。

`stock_60m_risk_v0.1`只用可信Close及其确定性派生Feature评分：连续走弱、
自身60个完整交易日绝对收益p95的Downside Shock、相对8指数中位收益的历史p10
明显弱势、市场-个股共振，以及Full Close Repair抵扣。Open/High/Low/Volume/
Turnover不参与评分。

`stock_15m_internal_v0.1`复用TASK_009的单一four-Close分类器，只作60m内部结构解释；
完整周期只输出六种冻结分类，1–3根15m只输出EARLY状态。两个规则版本都不产生
交易信号，不修改Daily、`market_60m_risk_v0.1`、`market_15m_internal_v0.1`或趋势系统v0.3.1。
