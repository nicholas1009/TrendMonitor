# Risk Engine Safe Input Contract

状态：`YES_WITH_LIMITS`  
可执行配置：`config/risk_feature_contract.json`

本契约只定义未来风险引擎允许接收的数据，不实现任何风险分数、风险灯、MACD规则、交易判断或下单。

## 层级边界

| 层级 | 唯一职责 | 数据限制 |
| --- | --- | --- |
| Daily | 正式趋势、正式交易裁决 | 只允许`DIRECT Daily` |
| 60m | 风险预警、细节确认 | 只使用本契约允许字段 |
| 15m | 60m内部结构辅助 | 不得形成第三套独立交易系统 |

分钟聚合出的Daily只用于质量诊断，禁止替代正式Daily。MA10、MA60、ATR14-SMA、日线趋势、保护线和所有进出场裁决继续由`DIRECT Daily`提供。

## 字段资格规则

- `EXACT_TRIGGER`只接受`TRUSTED`或`TRUSTED_WITH_TRANSFORMATION`。
- `APPROXIMATE`和`ADVISORY_ONLY`只能用于辅助描述。
- `BLOCKED`和`UNKNOWN`禁止任何Feature使用。
- `MERGE_CLOSING_BUCKET`的Close从`TRUSTED`变为`TRUSTED_WITH_TRANSFORMATION`，不丢Lineage。
- `SOURCE_BOUNDARY_ENVELOPE`不把High/Low提升为可信精确值。

## Safe Features

当前仅表示数据资格，不表示这些Feature已经实现：

- 周期Close涨跌；
- 连续Close走弱；
- Close修复；
- 相对指数/板块的Close强弱；
- 未来基于Close的MACD辅助输入。

这些Feature允许在15m和60m层使用，但只能作为风险预警，不能替代Daily正式判断。

## Disabled / Advisory Features

| Feature | Stock | Index | 原因 |
| --- | --- | --- | --- |
| 精确High/Low突破或跌破 | `DISABLED` | `DISABLED` | High/Low为`APPROXIMATE` |
| 更高/更低高点、低点硬结构 | `DISABLED` | `DISABLED` | 不允许由近似值产生精确条件 |
| High/Low波动范围描述 | `ADVISORY` | `ADVISORY` | 可描述，不报未经证实的精确突破 |
| Volume量能信号 | `ADVISORY` | `DISABLED` | 股票近似；指数Volume为`BLOCKED` |
| Turnover背景 | `ADVISORY` | `ADVISORY` | 指数为`ADVISORY_ONLY`，股票为`APPROXIMATE` |

## Runtime Degradation

`annotate_system_bar()`按资产类型和周期写入六个字段的`field_quality`，再应用转换状态和已知日期override。`evaluate_risk_input()`逐Feature判断，不因单一非核心字段异常关闭全部分析。

每个禁用Feature保留：

```text
feature_disabled
reason
affected_fields
quality_status
quality_reasons
source
```

已验证例子：

- `002463 / 2026-08-06`：Volume、Turnover为`BLOCKED`；Close类Feature继续；
- `中证500 / 2026-08-07`：Volume为`BLOCKED`；Close类Feature继续；
- `科创50 / 2026-08-21`：Volume、Turnover为`BLOCKED`；Close类Feature继续；
- `中证500 / 2026-08-05`：opening envelope的High/Low保持`APPROXIMATE`，Lineage保留。

输出`data_status=DEGRADED`和`readiness=YES_WITH_LIMITS`，不会静默宣称全字段有效。

## Hard Block

以下核心问题不是字段降级，而是输入整体阻断：

- Close缺失；
- 时间戳错误；
- 周期缺失；
- 严重重复Bar；
- Source Trace / Lineage缺失；
- 无法确认交易日；
- 15m/60m核心Bar数量不足。

此时`data_status=DATA_INCOMPLETE`、`readiness=NO`，所有Feature关闭并保存`hard_block_reasons`。系统不得补价格、换成Daily或让LLM猜测。

## Source Trace

风险输入必须保留`source_provider`、`source_bar_ids`、`source_raw_paths`、转换类型和字段质量。任何System Bar均可回溯到Longbridge Raw；Derived结果不得冒充Provider Source Bar。
