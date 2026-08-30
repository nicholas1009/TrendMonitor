# TASK_005｜分钟数据字段可信度分级与风险引擎Safe Feature Contract

## 目标

在TASK_004的Longbridge DIRECT分钟与System Bar基础上，用四标的最近至少60个完整交易日的1m/15m/60m/Daily NoAdjust数据，逐字段诊断OPEN/HIGH/LOW/CLOSE/VOLUME/TURNOVER，并建立字段质量与未来风险引擎输入契约。

## 必须保持

- Daily DIRECT是正式趋势和交易裁决的唯一日线输入；分钟聚合不得替代。
- 60m只可用于风险预警和细节确认；15m只作为60m内部结构辅助。
- Raw和Longbridge返回值不修改；不补High/Low/Volume，不放宽全局OHLC规则。
- 1m聚合只用于Cross-Period诊断，不得成为生产LOCAL_AGGREGATION。
- 本Task不实现实际风险分数、指标、风险灯、分析、调度、通知或交易。

## 输出

- 字段质量枚举：TRUSTED、TRUSTED_WITH_TRANSFORMATION、APPROXIMATE、ADVISORY_ONLY、BLOCKED、UNKNOWN。
- 每根System Bar的`field_quality`。
- `config/risk_feature_contract.json`和Feature-Level Degradation。
- Cross-Period误差分布、字段质量文档、风险输入契约与Provider Evidence Bundle。
- `scripts/verify_risk_input_quality.py`及离线测试。

本文件是用户本轮TASK_005指令的工程内摘要；详细要求以本Task请求和`MASTER_PROMPT.md`为准。
