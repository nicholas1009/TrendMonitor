# TASK_003｜Longbridge Provider接入、双源交叉校验与分钟K能力验证

本 Task 将 Longbridge 官方 Python SDK 作为第二数据源接入现有 Registry、MarketDataService、Raw Cache、Normalizer、Source Trace 与 fallback 边界，重点验证股票、指数、ETF、15m/60m 和四个标的的日线一致性。

禁止实现本地分钟聚合、指标、风险模型、调度、通知、交易或任何后续 Phase。Longbridge 能力必须由官方契约与当前账户真实调用共同确认；缺少凭证时只完成安全配置、Provider 和 mock 测试，真实结论标记 `BLOCKED_BY_LONGBRIDGE_CREDENTIALS`，TASK 状态不得高于 `PARTIAL`。
