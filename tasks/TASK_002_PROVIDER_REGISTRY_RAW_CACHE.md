# TASK_002｜Provider Registry + 标的标准化 + Raw缓存与数据源容错基础

本 Task 在不改变 TASK_001 结论的前提下，建立 provider-independent Instrument、显式 Provider Mapping、文件 Raw Cache、Source Trace 和非静默 fallback 基础。

范围仅包括数据身份、映射、缓存和容错边界；不实现 15m/60m 构造、指标、趋势或风险模型、交易、调度、通知及后续 Phase。

关键约束：

- TASK_001 已验证的 Hithink 能力保持不变，15m/60m DIRECT 仍为 UNSUPPORTED。
- `BK0437 煤炭` 与 Hithink 候选不能用字符串替换或定义为 EXACT；未经成分/收益验证仅可为 `CANDIDATE_PROXY`。
- Mapping 与 Provider Capability 分离。
- Raw 按 Provider 与数据类型保存，新请求不覆盖历史证据，并由 Manifest 追溯。
- fallback 必须公开 requested/actual Provider、原因和是否发生切换；全部失败返回 `DATA_INCOMPLETE`。
- 默认测试不依赖真实 API；真实验证使用 Registry 调用既有 Hithink Provider。
