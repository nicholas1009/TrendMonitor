# TASK_001｜同花顺 Financial-API 数据接入与能力验证

## 目标与边界

在 TrendMonitorLocal 内完成同花顺 Financial-API 的最小接入实验，只验证数据能力。不得实现趋势交易策略、自动交易、手机通知、定时任务、大盘或个股风险模型、ETF 交易模型。

## 执行要求

1. 先读 Master 与本 Task，检查 Python、uv、Git、Codex CLI，不为追新而升级。
2. 只用同花顺官方 Financial-API GitHub、官方 API 文档、CLI、Python SDK、Agent Skills 和实际调用确认能力。
3. 安装 Codex CLI 可发现的官方 `hithink-finance` Skill，记录路径、版本/commit 和方法；仅使用本 Task 所需的数据能力。
4. 提供 `.env.example`；从环境变量或 `.env` 读取真实 Key；`.env` 加入 `.gitignore`；缺 Key 时报告 `BLOCKED_BY_API_KEY`，不伪造返回。
5. 建立最小 `HithinkProvider`，只负责调用、返回 Raw 和统一错误转换。
6. 实测股票 600487、002463；指定指数 000001、000016、399300、000905、000902、399006、000852、000688；指定板块 BK0475、BK0437、BK0448、BK1036。
7. 验证实时价格/涨跌/成交量/成交额/时间戳、日线 OHLCV/历史长度、指数快照/历史、板块快照/历史/成分、ETF 行情/历史/基础信息、集合竞价、特色数据。
8. 通过官方文档和实际 API/CLI 确认 15m、60m 为 `DIRECT`、`UNSUPPORTED` 或 `LOCAL_AGGREGATION`；不因参数名称推测。
9. 若不能直接提供分钟线，只分析实时采样聚合的技术可行性、采样频率、成交量、午休、交易日和重启缺口，不建立 Daemon。
10. 将少量真实 Raw 样例原样、脱敏保存到 `data/samples/hithink/`。
11. Raw 与 Normalized 分离；至少转换一个股票、一个指数、一个板块/ETF。
12. 实现 symbol、timestamp、OHLC、high/low、volume、空数组等最小完整性检查。
13. 建立 `docs/DATA_CAPABILITY_MATRIX.md`，Result 仅用 `DIRECT`、`LOCAL_AGGREGATION`、`UNSUPPORTED`、`UNKNOWN`。
14. 建立 `uv run python scripts/verify_hithink.py` 入口并输出 PASS/FAIL/UNSUPPORTED/UNKNOWN 汇总。
15. 为 schema、normalization、validation、error mapping 添加无需真实 Key 的自动测试；真实 API 不应成为默认测试前置条件。
16. README 只写已真实验证结果、安装、Key 配置、验证命令与限制。
17. 建立 `docs/PHASE1_RESULT.md`，只根据实验回答数据源稳定性、替代能力、缺失数据、分钟线、实时聚合、Longbridge、Phase 2 最大风险。

## 完成条件

Skill 与认证路径确认；至少完成股票、指数、板块真实行情；15m/60m 已验证；Capability Matrix 和 Phase 1 Result 完成；测试通过；Secret 未泄露。

## 禁止事项

禁止自动交易、券商下单、修改 v0.3.1、分钟交易策略、数据库服务、上云、无关依赖、把 Key 写进 Git 或伪造行情。

## 最终汇报

结论 `SUCCESS / PARTIAL / FAILED`；可直接取得；无法直接取得；15m/60m；数据缺口；创建/修改文件；测试结果；Phase 2 建议。不得自动开始 Phase 2。
