# TASK_004｜Longbridge分钟数据口径验证与TrendMonitor System Bar标准化

## 目标

在TASK_003已确认Longbridge 15m/60m均为DIRECT的基础上，严格区分：

1. Longbridge Raw Source Bar（原样保存）；
2. 只统一字段、类型和Asia/Shanghai时区的Normalized Source Bar；
3. 可100%追溯Source Bar的Derived System Bar。

本Task验证09:30 OHLC边界异常、15:00 Closing Bucket以及固定16根15m/4根60m System Bar口径。禁止修改Raw、猜测Source OHLC、开发LOCAL_AGGREGATION，以及实现任何指标、风险模型、调度、通知或交易能力。

## 必须验证

- 600487、002463、中证500、科创50；
- 15m和60m至少60个交易日的总Bar、09:30异常、非09:30异常及异常类型；
- 15:00 Closing Bucket至少20日×多个标的，与Daily的Close/Volume/Turnover对账；
- 15m将14:45 Source Bar与15:00 Bucket合并，正常日形成16根System Bar；
- 60m将14:00 Source Bar与15:00 Bucket合并，正常日形成4根System Bar；
- 每根System Bar保存source_bar_ids、source_raw_paths、transformation和quality_status；
- 缺Bar、重复、午休异常或日线对账失败不得静默通过。

## 质量原则

- 普通Bar的OHLC错误仍为`INVALID_DATA`；
- 只有证据稳定支持的09:30 opening-only异常可分类为`SOURCE_BOUNDARY_QUIRK`；
- Source Bar始终不修改；Derived变换必须显式记录；
- Closing Bucket或日线对账证据不足时不得自动删除、补齐或扩大容差。

## 输出

- `docs/MINUTE_DATA_CONVENTION.md`
- `scripts/verify_minute_convention.py`
- Source质量分类、System Bar、Lineage、完整性与Daily reconciliation测试
- 完成状态以及是否可以进入风险引擎阶段的明确结论

本文件是用户本轮TASK_004指令的工程内摘要；详细边界以本Task请求和`MASTER_PROMPT.md`为准。
