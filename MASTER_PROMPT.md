# TrendMonitor Local｜Master Prompt v0.1

## 0. 项目身份

项目名称：TrendMonitor Local

项目定位：趋势跟踪系统的本地数据采集、风险监测与后验学习基础设施。

当前运行环境：本地 macOS + Codex CLI。

未来允许逐步扩展为：数据采集 → 数据校验 → 指标计算 → 大盘风险监测 → 个股风险监测 → 学习样本保存 → 定时无人值守运行 → 手机主动通知。

所有能力必须分阶段建设，不允许一次性扩大范围。

## 1. 当前阶段

当前仅处于 **Phase 1｜同花顺 Financial-API 数据接入实验**。

当前目标不是建立完整趋势交易系统，而是验证同花顺数据是否能够稳定取得、哪些数据可直接取得、哪些无法取得、哪些只能通过实时采样自行构造，以及数据格式和稳定性是否足以支持后续趋势监测系统。

## 2. 正式趋势系统边界

现有正式趋势跟踪系统 v0.3.1 仍然是独立的正式交易规则系统。

本地项目当前不修改趋势、进场、离场、ATR、日线趋势定义，不产生真实交易指令，不调用券商交易接口，不进行真实下单。

未来若接入趋势分析：日线负责正式趋势和交易确认；60 分钟负责风险预警；15 分钟仅作为 60 分钟内部结构辅助观察。Phase 1 不实现这些交易判断。

## 3. 工程执行原则

本项目遵守：不猜、不多做、不乱动、要验证。

- 不猜：无法确认的 API 能力必须通过官方文档和实际调用验证，不得根据名称推测。
- 不多做：Task 未要求的功能不提前实现，包括自动交易、Web UI、数据库服务、云部署、手机通知、复杂 Agent、机器学习模型。
- 不乱动：已有正常代码优先最小修改，不为美化做无关重构。
- 要验证：新增能力必须实际运行、检查返回、适用时自动测试并记录文档；不能以“看起来可以”为完成标准。

## 4. 架构原则

```text
Data Provider
      ↓
Raw Data
      ↓
Normalizer
      ↓
Validated Data
      ↓
Indicator Engine
      ↓
Trend / Risk Engine
      ↓
Report Engine
      ↓
Notification
```

不得把所有职责混入一个脚本。

## 5. AI 与确定性代码分工

能用确定性代码完成的事情，不交给 LLM 猜。

MA、ATR、OHLC 聚合、涨跌幅、成交量、15/60 分钟 bar 构造、时间校验和缺失检查优先由 Python 完成。Codex / LLM 主要负责解释数据、风险分析、规则读取、报告生成和学习样本归纳。

## 6. 数据源设计原则

不得锁死在单一数据源。长期采用 Provider Interface（Hithink、Longbridge、Future Providers）。当前 Hithink 为主要实验对象，未来允许同花顺 + Longbridge 交叉验证与故障降级。

## 7. 原始数据原则

- Raw：API 原始返回，不得擅自修改。
- Normalized：转换成内部统一格式。
- Derived：本地计算生成，例如 MA、ATR、15/60 分钟 bar、风险指标。

任何报告必须能够追溯 Derived → Normalized → Raw。

## 8. 数据完整性优先

数据完整性置于 AI 分析之前。每次未来运行前必须判断数据完整、部分缺失、异常或数据源不可用。数据不足时不允许 LLM 补齐或猜测，必须返回 `DATA_INCOMPLETE` 并明确缺失项目。

## 9. 时间周期原则

- 日线：正式趋势和正式交易判断。
- 60 分钟：风险预警。
- 15 分钟：60 分钟内部结构辅助验证。

若数据源不能直接提供 15m/60m，不得伪造。允许研究通过实时行情采样构造 bar，但必须标记 `locally_aggregated`，不得和源端 K 线混淆。

## 10. Secret 安全规则

所有 API Key、Token、Secret 禁止写入源码，必须使用环境变量或 `.env`，提供 `.env.example`，真实 `.env` 必须加入 `.gitignore`。日志和错误不得泄露 Secret。

## 11. 建议项目结构

```text
TrendMonitorLocal/
├── MASTER_PROMPT.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── tasks/
│   └── TASK_001_HITHINK_DATA_VALIDATION.md
├── src/trend_monitor/
│   ├── providers/hithink/
│   ├── schemas/
│   ├── normalization/
│   ├── validation/
│   └── utils/
├── scripts/
├── tests/
├── data/samples/
└── docs/
    ├── DATA_CAPABILITY_MATRIX.md
    └── PHASE1_RESULT.md
```

允许根据实际官方 SDK/CLI 结构做最小调整，但必须说明理由。

## 12. 当前重点监测对象

- 个股：600487 亨通光电、002463 沪电股份。
- 指数：000001 上证指数、000016 上证 50、399300 沪深 300、000905 中证 500、000902 中证流通、399006 创业板指数、000852 中证 1000、000688 科创 50。
- 板块：BK0475 银行、BK0437 煤炭、BK0448 通信设备、BK1036 半导体。
- 同时测试 ETF / 宽基基金数据接口，但当前不进行 ETF 交易。

## 13. Capability Matrix

任何新 Provider 必须维护 `DATA_CAPABILITY_MATRIX.md`，至少明确实时价格、日线 OHLCV、15m/60m K 线、指数、板块、ETF、集合竞价、成交量、资金数据。状态不得模糊，使用 `DIRECT`、`LOCAL_AGGREGATION` 或 `UNSUPPORTED` 等明确定义。

## 14. 错误原则

Provider 错误必须分类，例如 `AUTH_ERROR`、`RATE_LIMIT`、`NETWORK_ERROR`、`UNSUPPORTED`、`EMPTY_DATA`、`INVALID_DATA`、`DATA_INCOMPLETE`、`UNKNOWN_ERROR`。不得 `except Exception: pass` 隐藏问题。

## 15. 测试原则

至少验证正常数据、无效代码、API 认证错误、空数据、数据结构异常、Provider 不可用。网络请求尽可能与业务逻辑分离。

## 16. 日志原则

日志需帮助定位 Provider、时间、标的、接口、成功/失败及原因，但不得包含 Secret。

## 17. Phase 机制

```text
Phase 1  同花顺数据能力验证
Phase 2  稳定数据采集 + 本地缓存
Phase 3  15m / 60m bar 生成与验证
Phase 4  v0.3.1 正式日线指标引擎
Phase 5  大盘 60 分钟风险引擎
Phase 6  亨通 + 沪电风险引擎
Phase 7  无人值守调度
Phase 8  iPhone 通知
Phase 9  学习样本数据库
Phase 10 ETF 纸面交易实验
```

以上仅为 Roadmap，除非具体 Task 要求，不得提前实现。

## 18. Task 执行流程

1. 阅读 `MASTER_PROMPT.md`。
2. 阅读当前 Task 文件。
3. 检查现有工程和环境。
4. 明确 Task 目标和边界。
5. 最小实现。
6. 实际验证。
7. 更新对应文档。
8. 输出完成报告。

## 19. Codex 完成报告格式

每次完成 Task 必须输出：完成内容、修改文件、实际验证、数据验证、未解决问题、是否影响 Master（YES / NO）。如果 YES，必须说明原因；未经用户确认不得自行修改 Master 原则。

## 20. 当前最高目标

当前阶段不是做 AI 炒股机器人，而是建立稳定、可验证、可追溯的数据基础层。数据层可靠之后，才允许逐步迁入趋势系统 v0.3.1、大盘 60 分钟雷达、15 分钟内部结构、个股风险监控、利弗莫尔价格记录和 ETF 实验。

## Environment

兼容 Codex CLI。普通 Chat 可用于设计与复盘，但不能代替本地执行环境。

需要 Shell、Python、网络、Git、同花顺 Financial-API 认证、Codex CLI。

限制：当前不保证同花顺直接提供 15/60 分钟历史 K 线；所有能力必须通过官方接口和实际调用确认；禁止真实交易。
