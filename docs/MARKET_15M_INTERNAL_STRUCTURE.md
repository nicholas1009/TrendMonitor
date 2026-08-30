# TASK_009｜15分钟内部结构辅助模块 v0.1

## TASK_009

`SUCCESS`。

`market_15m_internal_v0.1`已实现8指数Close-only内部结构、市场辅助状态、
IN_PROGRESS早期观察、80周期历史Replay、前兆后验统计、append-only Snapshot和人类报告。

该模块没有独立风险灯或Risk Score；`market_60m_risk_v0.1`保持冻结。

## 固定边界

- 唯一分析入口是TASK_006 Preflight允许消费的15m Risk Input / System Bar。
- 每个完成60m周期严格映射4根15m System Bar：09:30–10:30、10:30–11:30、
  13:00–14:00、14:00–15:00。
- 15:00 Closing Bucket仍是第4根15m System Bar的一部分，不形成第5根。
- 分类只使用`TRUSTED / TRUSTED_WITH_TRANSFORMATION` Close。
- High/Low、Index Volume和Turnover保留Provenance，但不决定分类。
- 输出只解释冻结的60m结果，不修改Daily、60m、交易、仓位或通知逻辑。

## 确定性分类

正式分类限定为：

- `HEALTHY_UP`
- `HEALTHY_DOWN`
- `LATE_REPAIR`
- `FAILED_REPAIR`
- `LATE_WEAKENING`
- `MIXED`

分类优先级固定为：

`LATE_REPAIR → FAILED_REPAIR → LATE_WEAKENING → HEALTHY_UP → HEALTHY_DOWN → MIXED`。

具体实现：

- HEALTHY_UP：至少3/4上涨且最后一根不下降。
- HEALTHY_DOWN：至少3/4下降且最后一根不上涨。
- LATE_REPAIR：前2根至少1根下降、`c2 / previous_60m_close - 1 < 0`，
  后2根均上涨且`c4 > c2`。
- FAILED_REPAIR：前3个变化中存在“先弱后涨”的Close修复，但最后一根重新下降。
- LATE_WEAKENING：前半段累计不弱，后2根均下降且`c4 < c2`。
- 其他为MIXED。

`repair_strength`与`finish_position`均按四个15m Close的Close Range Position计算；
分母为0时为`null / N/A`。两者不进入60m Score。

## Current 15m Internal State

- 60m周期：`2026-08-28 14:00–15:00 +08:00`
- Period Status：`COMPLETED`
- completed_15m_count：`4`
- Market Internal State：`WEAKNESS_BROADENING`
- 冻结60m结果：`ORANGE / Score 5 / FLAT`

解释：60分钟风险仍为橙色，本周期15分钟内部弱势出现广泛扩散；60m结果没有被下调、
上调或重算。

## Index Structures

| 指数 | Classification | Direction Sequence | Finish Position |
|---|---|---|---:|
| 上证指数 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 上证50 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 沪深300 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 中证500 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 中证流通 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 创业板指 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 中证1000 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |
| 科创50 | LATE_WEAKENING | ↑ ↓ ↓ ↓ | 0.0 |

四组均为`LATE_WEAKENING`，因此市场状态满足弱势扩散阈值。

## Historical Replay

TASK_008最近20个完整交易日、80个60m周期均完成15m补算；共640个指数周期观察：

| Classification | Count | Ratio |
|---|---:|---:|
| HEALTHY_UP | 162 | 25.31% |
| HEALTHY_DOWN | 49 | 7.66% |
| LATE_REPAIR | 89 | 13.91% |
| FAILED_REPAIR | 136 | 21.25% |
| LATE_WEAKENING | 65 | 10.16% |
| MIXED | 139 | 21.72% |

市场状态：`REPAIR_BROADENING 30 / WEAKNESS_BROADENING 28 / INTERNAL_MIXED 22`。

### ORANGE / RED关联

20个周期、160个有效指数观察：

- HEALTHY_DOWN：34，21.25%
- LATE_WEAKENING：25，15.63%
- FAILED_REPAIR：51，31.88%
- REPAIR_BROADENING：3/20，15.00%

### GREEN关联

43个周期、344个有效指数观察：

- HEALTHY_DOWN：1，0.29%
- LATE_WEAKENING：36，10.47%
- FAILED_REPAIR：44，12.79%
- REPAIR_BROADENING：24/43，55.81%

这些是解释性关联，不是阈值优化或Risk规则升级。

## Risk-Up Precursors

在80周期内部可直接观察的31个“下一周期Risk Score上升”事件中：

- LATE_WEAKENING提前出现：10，32.26%
- FAILED_REPAIR提前出现：13，41.94%
- WEAKNESS_BROADENING提前出现：10，32.26%
- 任一弱势前兆命中：18，58.06%

TASK_008 Replay的首周期另有一个相对warm-up的RISING状态；由于其前一周期不在TASK_009
80周期窗口中，不纳入前兆分母。

## Risk-Down Precursors

23个“下一周期Risk Score下降”事件中：

- LATE_REPAIR提前出现：9，39.13%
- REPAIR_BROADENING提前出现：8，34.78%
- 任一修复前兆命中：10，43.48%

## Sample Audit

历史真实数据中以下每类均保存3个可追溯Sample：

- HEALTHY_DOWN：3
- LATE_WEAKENING：3
- LATE_REPAIR：3
- FAILED_REPAIR：3
- HEALTHY_UP：3
- MIXED：3

没有为凑样本修改规则。EARLY分类不属于完成周期Replay，实际IN_PROGRESS验证单独保存。

## In-Progress Support

- 14:30回放视图：2根当前60m周期内的完整15m System Bar，`IN_PROGRESS`。
- 14:45回放视图：3根完整15m System Bar，`IN_PROGRESS`。
- 单指数只输出`EARLY_STRENGTH / EARLY_WEAKNESS / EARLY_MIXED`，不会提前使用正式完整分类。
- IN_PROGRESS结果不进入80个完成周期历史结果。

计数采用TASK_006 System Bar结束语义；例如14:00–15:00周期的当前内部Bar结束点为
14:15、14:30、14:45、15:00，14:00 Close仅作为`previous_60m_close`基线。

## 60m Score Immutability

- Current输入前后60m Risk Score保持`5`。
- 80个冻结TASK_008结果逐项深拷贝比较一致。
- 15m结果只保存`linked_60m_risk`副本和source ID，没有写入60m对象或目录。
- `config/market_60m_risk_rules.json`和`src/trend_monitor/market_risk/`未为TASK_009修改。

## Data Quality

- Current：8/8有效，全部来自`support_15m` Snapshot并通过Preflight；市场状态READY。
- Historical：复用8/8本地15m Raw，经MarketDataService重新验证并由RiskInputAssembler生成Risk Input；
  内部引擎只消费Risk Input，不直接读取Provider Raw。
- 每周期Snapshot关联8个Risk Input、对应冻结60m结果、rules_version、Source Trace和Lineage。
- 某指数Close不可用时分类`UNAVAILABLE`；有效指数少于6或任一四大分组完全缺失时，
  市场状态为`DATA_INCOMPLETE`。
- Index Volume仍为BLOCKED，Turnover仍为ADVISORY_ONLY，High/Low仍不用于硬分类。

## Determinism / Lookahead

- Current与80周期Replay重复执行完全一致。
- 每个历史Risk Input只保留对应`as_of`已经完成的System Bar。
- 80/80历史结果与2/3根IN_PROGRESS视图均通过look-ahead检查。

## Snapshot与报告

- Current/IN_PROGRESS JSON：`data/risk_outputs/market_15m_internal/json/`
- Human Report：`data/risk_outputs/market_15m_internal/markdown/`
- append-only Replay：`data/risk_outputs/market_15m_internal/replay/`
- append-only manifest：`data/risk_outputs/market_15m_internal/manifest.jsonl`
- Historical Risk Input Snapshot：`data/risk_inputs/market_15m_replay/`
- 便利投影：`data/reports/market_15m_internal_latest.json`

## Tests

- `uv run python -m unittest discover -v`：133/133通过。
- 分类、Repair/Weakening、MIXED、平坦分母、2/3根EARLY、部分覆盖、四组覆盖、
  Market State、确定性、Replay关联、append-only、High/Low/量额无影响及60m Score不可变均有单测。
- `verify_risk_input.py`：Daily PASS，60m/15m按既有Contract DEGRADED，Provenance与Snapshot Replay通过。
- `verify_market_60m_risk.py`：冻结上游仍为`ORANGE / Score 5`，80周期、Determinism、
  Lookahead及Pipeline Match通过。
- `verify_market_15m_internal.py`真实验证：Current PASS、80周期Replay PASS、
  Determinism PASS、60m Score Immutability PASS、Lookahead PASS，历史Raw缓存复用8/8。

## 完成条件回答

1. 四15m内部结构可稳定分类：`YES`。
2. 8指数Market Internal State可生成：`YES`。
3. 可识别LATE_WEAKENING：`YES`，当前8/8触发。
4. 可识别LATE_REPAIR：`YES`，历史89个指数周期。
5. 支持IN_PROGRESS早期观察：`YES`，2根/3根均实际验证。
6. 完全不修改60m Risk Score：`YES`。
7. Historical Replay通过：`YES，80/80`。
8. 是否显示潜在提前预警价值：`YES_WITH_LIMITS`；弱势前兆命中58.06%，但仅20日后验样本。
9. 是否值得进入下一阶段：`YES`，优先进入个股风险验证，不直接进入自动调度。

## 15m Auxiliary Value

`PROMISING`。

ORANGE/RED周期的HEALTHY_DOWN、FAILED_REPAIR和LATE_WEAKENING均高于GREEN；GREEN中的
REPAIR_BROADENING比例明显更高；风险上升前一周期弱势前兆联合命中率为58.06%。
这支持“额外解释与候选前兆价值”，不支持修改60m规则。

## 下一阶段建议

只建议一个Task：

> TASK_010｜两只正式个股60m风险与15m内部结构验证 v0.1

先验证亨通光电、沪电股份在冻结大盘风险背景下的只读风险解释；暂不执行自动调度、板块扩展或通知。

## 是否影响Master

`NO`。本Task落实Master既有“15m仅作为60m内部结构辅助观察”原则，没有修改Daily、
正式趋势系统v0.3.1或冻结的60m风险引擎。
