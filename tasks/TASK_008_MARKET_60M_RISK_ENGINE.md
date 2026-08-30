# TASK_008｜大盘60分钟风险引擎 v0.1｜8指数核心雷达

本Task首次实现实际大盘60分钟风险判断，但不修改Daily正式趋势系统，不实现15m评分、板块/个股风险、调度、通知或交易。

正式输入仅为TASK_006/007通过Preflight的8指数60m Risk Input。v0.1只使用可信Close和其确定性派生Feature；High、Low、Index Volume和Turnover禁止参与评分。

规则版本固定为`market_60m_risk_v0.1`。四组为`LARGE_CAP / BROAD_MARKET / MID_SMALL / GROWTH`。评分由Breadth、Persistent Weakness、历史60m绝对收益p95 Downside Shock、Weighted Support Distortion及Broad Repair offset构成；阈值全部配置化。

输出必须包含机器JSON、人类报告、append-only Snapshot、风险灯、结构Flag、置信度和完整Provenance。历史验证使用60个完整交易日作为Shock基线，随后回放20个完整交易日、最多80个周期，严格禁止look-ahead。详细完成条件和禁止事项以用户提交的TASK_008原文为准。
