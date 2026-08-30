# TASK_006｜Risk Input Assembly + Preflight Gate

## 目标

在现有MarketDataService、System Bar和Safe Feature Contract之上，建立可追溯的Risk Input、字段级Feature Eligibility、Preflight Gate及Append-only Snapshot。

## 必须保持

- Daily正式输入只允许DIRECT Daily；拒绝minute-derived Daily。
- 60m使用TrendMonitor 4周期System Bar；15m只作为60m内部结构辅助。
- Safe Feature Contract是唯一字段准入依据。
- 非核心字段只降级相关Feature；核心Close、时间、周期、Bar数、Source Trace或Lineage不足时返回DATA_INCOMPLETE/BLOCKED。
- Risk Input层只通过MarketDataService取数，不直接调用Provider SDK/REST。
- 本Task不实现风险评分、风险灯、指标策略、交易判断、调度、通知或交易。

本文件是用户TASK_006指令的工程内摘要；详细要求以本Task请求和`MASTER_PROMPT.md`为准。
