# Risk Input Assembly + Preflight Gate

验证日期：2026-08-30  
TASK_006：`SUCCESS`  
Risk Engine Input Readiness：`YES_WITH_LIMITS`

本层只负责把经过合同约束的数据交到未来风险引擎门口，不包含风险评分、风险灯、指标策略、买卖判断或交易。

## 数据流

```text
MarketDataService
  → Provider Result + Raw Cache + Normalized Records
  → Completed System Bars
  → Field Quality
  → risk_feature_contract.json
  → Risk Input Assembly
  → Preflight Gate
  → Snapshot
```

`RiskInputService`是唯一取数入口。它调用现有`MarketDataService`，因此Registry mapping、Provider fallback、Raw Cache、Normalizer和Source Trace继续由既有层负责。Assembler不导入或调用Hithink REST、Longbridge SDK。

## Risk Input Schema

Schema版本为1。每个周期输入包含：

```text
instrument_id, asset_type, analysis_period,
as_of, trading_date,
source_provider, source_trace,
system_bars,
feature_inputs, disabled_features, degraded_features,
data_status, preflight_status,
last_completed_bar_end, data_fetched_at,
layer_role, in_progress_source_bars, preflight_reasons
```

每个Feature Input包含：

```text
feature_name, value, field_source,
quality, eligibility, reason, lineage
```

`eligibility`仅允许`ENABLED`、`DEGRADED`和`DISABLED`。未来风险引擎只能把`feature_inputs`中的`ENABLED`项目作为可执行输入；辅助数据单独位于`degraded_features`，禁用数据单独位于`disabled_features`。

## Period层级

### DAILY

- 只接受`ProviderDataResult.data_type=DAILY`且每条Normalized记录`period=1d`；
- `source_kind`必须为`DIRECT`；
- `minute_derived_daily`或其他非DIRECT标记立即拒绝为`INVALID_DATA`；
- Daily `RiskBar.transformation=DIRECT_DAILY`；
- 仍是正式趋势和交易裁决的唯一日线输入。

Daily字段放在统一`system_bars`数组中只是为了保持消费Schema一致；它们是DIRECT Daily RiskBar，不是分钟聚合System Bar。

### 60M

- 主要风险预警周期；
- 完整交易日必须由4根TrendMonitor System 60m Bar构成；
- Longbridge原始5根Source Bar不能直接进入上层；
- 14:00 Source只有在15:00 Closing Bucket到达后才形成最后一根完整System Bar。

### 15M

- 只作为60m内部结构辅助；
- 完整交易日必须为16根System 15m Bar；
- 不得形成独立交易系统。

## Current Completed Period和As-Of

所有时间判断强制使用`Asia/Shanghai`。Risk Input保存：

- `as_of`；
- `last_completed_bar_end`；
- `data_fetched_at`；
- 未完成Source时间戳列表`in_progress_source_bars`。

盘中只构造已完成周期。例如14:20运行时：

- 60m只允许09:30–10:30、10:30–11:30、13:00–14:00三根；
- 14:00–15:00保留为IN_PROGRESS来源，不进入Feature；
- Preflight按当时应完成的3根检查，不把未来周期冒充已完成周期。

这一过程仅应用TASK_004已验证的1:1/Closing Bucket System Bar转换，不是Quote采样或`LOCAL_AGGREGATION`。

## Safe Feature Assembly

以下准入规则全部来自`config/risk_feature_contract.json`，没有第二套质量硬编码。

| Feature | Value语义 | 当前资格 |
| --- | --- | --- |
| `current_period_close` | 最新完整周期Close | `ENABLED` |
| `previous_period_close` | 前一完整周期Close | `ENABLED` |
| `close_change` | Current - Previous | `ENABLED` |
| `close_change_pct` | `(Current - Previous) / Previous`；0.01表示1% | `ENABLED` |
| `consecutive_close_direction` | 末端同方向Close变化及连续transition数 | `ENABLED` |
| `close_repair` | Current/Previous两项安全输入；不在本Task计算修复真假 | `ENABLED` |
| `high_low_range_description` | 当前High/Low辅助值 | `DEGRADED` |
| `stock_volume_context` | 股票当前Volume | `DEGRADED` |
| `turnover_context` | 当前Turnover | `DEGRADED` |
| `precise_high_low_break` | Exact Trigger候选 | `DISABLED` |
| `intraday_high_low_structure` | Exact结构候选 | `DISABLED` |
| `index_volume_signal` | 指数量能候选 | `DISABLED` |

`close_repair`只装配输入原料，避免在没有正式规则时猜测“已修复”。同理，本Task没有计算任何风险结论。

## Closing Bucket和Opening Quirk

- `MERGE_CLOSING_BUCKET`最后周期的Close为`TRUSTED_WITH_TRANSFORMATION`，可进入Close Feature；Feature Lineage同时记录常规Source与15:00 Bucket。
- `SOURCE_BOUNDARY_ENVELOPE`不阻断整根Bar；Close仍可用，High/Low保持`APPROXIMATE`并关闭Exact Trigger。
- Raw和Normalized Source均不修改。

## Preflight Gate

### PASS

核心数据、Trace和要求Feature均完整，且没有降级/禁用项目。当前真实Daily输入属于此状态。

### PASS_WITH_DEGRADATION

核心Close和周期完整，可继续未来风险分析，但部分Feature为辅助或禁用。当前真实15m/60m输入属于此状态。

### BLOCKED

以下任一条件返回`data_status=DATA_INCOMPLETE`和`preflight_status=BLOCKED`：

- 当前核心Close缺失；
- 应完成System Bar数量不足；
- 时间倒序或重复；
- 交易日未知；
- Source Trace或Lineage缺失；
- 核心Bar为INVALID；
- 当前周期未完成；
- 所有Provider均失败；
- 没有任何安全核心Feature。

指数Volume为`BLOCKED`不会触发整体BLOCK：Close Feature继续`ENABLED`，Volume Feature进入`disabled_features`，Preflight为`PASS_WITH_DEGRADATION`。

## Provider Trace

顶层Source Trace保存：requested provider、actual provider、provider symbol、fallback状态/原因、Raw路径、fetch时间和source timestamp。

每个Feature的Lineage保存：

```text
Feature
  → Risk/System Bar ID + transformation
  → Normalized record identity
  → Raw path
  → actual Provider + symbol
```

受控fallback测试确认`requested=hithink / actual=longbridge / fallback_used=true`不会丢失。

## Snapshot

Append-only Snapshot目录：`data/risk_inputs/`。

- `instrument/`：单标的Daily + 60m + 15m Bundle；
- `group/`：市场指数和股票Bundle manifest；
- `manifest.jsonl`：Snapshot身份、as-of和路径；
- 文件名包含as-of和随机request suffix，新Snapshot不覆盖旧证据；
- 读取时检查路径边界、JSON、schema_version及敏感字段。

Snapshot回读与原始`to_dict()`逐字段一致，可用于未来测试和回放。

## 真实验证

真实报告：`data/reports/risk_input_latest.json`。

| Instrument | Daily | 60m | 15m | 日期 | Snapshot |
| --- | --- | --- | --- | --- | --- |
| 600487 | PASS | PASS_WITH_DEGRADATION / 4 bars | PASS_WITH_DEGRADATION / 16 bars | 2026-08-28 | PASS |
| 002463 | PASS | PASS_WITH_DEGRADATION / 4 bars | PASS_WITH_DEGRADATION / 16 bars | 2026-08-28 | PASS |
| 中证500 | PASS | PASS_WITH_DEGRADATION / 4 bars | PASS_WITH_DEGRADATION / 16 bars | 2026-08-28 | PASS |
| 科创50 | PASS | PASS_WITH_DEGRADATION / 4 bars | PASS_WITH_DEGRADATION / 16 bars | 2026-08-28 | PASS |

四个Snapshot均通过Contract、Feature Degradation、Provenance和JSON回读检查。

### Market Risk Input Bundle

TASK_007已用真实`static_info`、Quote、Daily、15m和60m证据补齐六个Mapping。当前8个正式指数均进入最新Group manifest，全部为`DEGRADED`而非`BLOCKED`：Daily为PASS，15m固定16根，60m固定4根，分钟Preflight为`PASS_WITH_DEGRADATION`。降级来自既有Safe Feature Contract（尤其Index Volume禁用），不代表Mapping不可用。

### Stock Risk Input Bundle

亨通光电和沪电股份均包含：DIRECT Daily、最新完整4根60m、16根支持15m、Feature级质量和完整Source Trace。

## 当前限制

- Risk Engine Input已经可用，但只能按Safe Contract有限使用；
- High/Low精确结构和指数Volume仍被禁用；
- 30日长窗口发现上证指数1日、创业板2日15m负Turnover；严格Validator继续拒绝对应日期，当前完整日输入不受影响，详见`MARKET_INDEX_COVERAGE.md`；
- Snapshot是本地文件结构，不是数据库或调度系统；
- 没有实现任何实际风险规则。
