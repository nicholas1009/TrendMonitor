# TASK_012｜行业Benchmark分钟数据方案与Provider可获得性验证 v0.1

本Task只验证行业分钟数据来源与方案，不激活行业Context、不修改任何Market/Stock评分。

Canonical行业身份保持Hithink THS：亨通光电为`881129.TI 通信设备`，沪电股份为
`884092.TI 印制电路板`，均为`EXACT / HIGH`。跨Taxonomy的申万2021候选仅可保持
`CANDIDATE_PROXY`，必须通过成分、历史成员、日收益相似性、分钟质量与实时边界对账后，
才可在后续独立Task提出启用。

禁止Synthetic Basket、ETF替代、网页抓取、自动购买权限、交易信号、调度和通知。
