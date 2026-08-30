# TASK_010｜两只正式个股盘中风险引擎 v0.1

## TASK_010

`SUCCESS`。

`stock_60m_risk_v0.1`与`stock_15m_internal_v0.1`已对600487亨通光电、
002463沪电股份完成当前周期、2×80历史观察、前兆后验研究、确定性、
look-ahead、append-only和分数不可变验证。结果只是盘中风险监控，不是交易系统。

## 定位与边界

- Daily仍是正式趋势与交易裁决层。
- Market 60m是大盘风险环境；Stock 60m是个股盘中风险预警。
- Market/Stock 15m只解释对应60m周期的内部结构。
- 不接入行业板块，不产生交易、持仓、调度、通知或自动化动作。

## 输入与Safe Feature Contract

引擎只消费TASK_006 Preflight后的Stock 60m/15m Risk Input、TASK_008 Market 60m
Risk Result及TASK_009 Market 15m Internal Result。引擎不读Provider Raw，验证脚本只负责
把已有Raw Cache重新验证并组装成Risk Input。

评分与分类只使用`TRUSTED / TRUSTED_WITH_TRANSFORMATION` Close。Open、High、
Low、Volume、Turnover保留在溯源与ADVISORY降级中，不增减Risk Score。

## Stock 60m Risk Score

| 组件 | 确定性条件 | 分数 |
|---|---|---:|
| Persistent Weakness | 当前和上一周期Close Return均为负 | +1 |
| Downside Shock | 当前负收益绝对值不低于严格as-of前60完整日p95 | +2 |
| Relative Weakness | 相对8指数中位收益为负，且不高于自身历史p10 | +1 |
| Market Resonance | 个股下跌，且市场橙/红或广谱弱势Flag成立 | +1 |
| Full Close Repair | 上周期负、当前正，且Close修复至两周期前 | -1 |

分数下限为0；风险灯配置固定为`0 GREEN / 1–2 YELLOW / 3–4 ORANGE /
5 RED`。Market YELLOW本身不产生共振分。

## Relative Weakness与Market Resonance

Market Benchmark是同周期8指数Close Return中位数，不是单一指数。两只股票分别使用
自身前60个完整交易日的Relative Return p10，所有参考期严格早于当前周期。
市场不可用时，Relative Weakness与Market Resonance显式`DISABLED`，股票Close完整时
不会整体BLOCK，Confidence降为MEDIUM。

## Stock 15m Internal

股票15m复用TASK_009同一`classify_close_structure`函数，规则版本仍独立为
`stock_15m_internal_v0.1`。完整周期只允许HEALTHY_UP、HEALTHY_DOWN、LATE_REPAIR、
FAILED_REPAIR、LATE_WEAKENING、MIXED；1–3根只允许EARLY分类。

JOINT_WEAKNESS、STOCK_REPAIR_AGAINST_WEAK_MARKET和JOINT_REPAIR是解释Flag，不进入
Stock 60m Score。

## Current 600487

- 周期：`2026-08-28T15:00:00+08:00`。
- Risk：`🟡 YELLOW / Score 2 / FLAT / HIGH`。
- 本周期：`-1.0942%`；市场中位数`-0.2153%`；相对收益`-0.8788%`。
- Persistent Weakness：是；Downside Shock：否；Relative Weakness：否；Market Resonance：是。
- 15m：`HEALTHY_DOWN / ↑ ↓ ↓ ↓ / JOINT_WEAKNESS`，Finish Position `0.0`。

## Current 002463

- 周期：`2026-08-28T15:00:00+08:00`。
- Risk：`🟡 YELLOW / Score 2 / FLAT / HIGH`。
- 本周期：`-0.4987%`；市场中位数`-0.2153%`；相对收益`-0.2834%`。
- Persistent Weakness：是；Downside Shock：否；Relative Weakness：否；Market Resonance：是。
- 15m：`HEALTHY_DOWN / ↑ ↓ ↓ ↓ / JOINT_WEAKNESS`，Finish Position `0.0`。

## Market Context

当前两只股票都关联同一冻结市场结果：`ORANGE / Score 5 / FLAT`，
`BROAD_SELLOFF_RESONANCE=true`、`STRONG_BROAD_WEAKNESS=true`；Market 15m为
`WEAKNESS_BROADENING`。市场与个股关系提供了共振或背离解释，但不替代Daily。

## Historical Replay

使用最近20个两股共同完整交易日，每股80周期，合计160观察。窗口为
`2026-07-30`至`2026-08-28`；由于002463在8月17日、21日缺正15:00 Closing Bucket，
这两日按正式Contract整日排除，用7月30日、31日补足20个共同完整日。没有补价、
插值或改写Raw。新增的两日Market Context使用冻结TASK_008/009规则只读计算，
与TASK_008原80周期重叠部分逐周期一致。

| 股票 | GREEN | YELLOW | ORANGE | RED | risk_up | risk_down |
|---|---:|---:|---:|---:|---:|---:|
| 600487 | 48 | 31 | 1 | 0 | 21 | 18 |
| 002463 | 45 | 34 | 1 | 0 | 24 | 20 |

| 股票 | Persistent | Shock | Relative Weak | Market Resonance | Independent Weak | Counter-market Strong |
|---|---:|---:|---:|---:|---:|---:|
| 600487 | 19 | 1 | 1 | 30 | 0 | 3 |
| 002463 | 18 | 1 | 4 | 31 | 3 | 3 |

## Market Resonance

两股各有1个ORANGE、无RED；这2个个股高风险周期的市场灯都为ORANGE，
且都命中Market Resonance。该高风险子样本很小，不应外推为稳定预测结论。
全窗口内600487共振30次，002463共振31次；002463另识别3次个股独立弱势，
600487未出现满足冻结阈值的独立弱势。

## 15m Internal Structure

| 股票 | HEALTHY_UP | HEALTHY_DOWN | LATE_REPAIR | FAILED_REPAIR | LATE_WEAKENING | MIXED |
|---|---:|---:|---:|---:|---:|---:|
| 600487 | 21 | 7 | 9 | 18 | 7 | 18 |
| 002463 | 18 | 10 | 10 | 21 | 7 | 14 |

## Risk-Up Precursors

对下一周期Stock Score上升的样本：

- 600487：21个事件，LATE_WEAKENING/FAILED_REPAIR/HEALTHY_DOWN联合命中6，`28.57%`；
  JOINT_WEAKNESS命中3，`14.29%`。
- 002463：24个事件，弱势分类联合命中12，`50.00%`；JOINT_WEAKNESS命中5，
  `20.83%`。
- 合并观察：45个事件中弱势分类命中18，`40.00%`。

这支持15m存在“候选提前解释价值”，但两股差异明显，样本仅20日，不支持调参。

## Risk-Down Precursors

- 600487：18个Score下降事件，LATE_REPAIR/HEALTHY_UP联合命中2，`11.11%`。
- 002463：20个Score下降事件，修复分类联合命中3，`15.00%`。

当前修复前兆支持度较弱，只记录为后验观察。

## Sample Audit

两股的GREEN、YELLOW、LATE_WEAKENING、LATE_REPAIR、FAILED_REPAIR和JOINT_WEAKNESS
均保存3个可追溯样本；ORANGE各只存在1个，RED均不存在。Independent Weakness在
002463保存3个，600487不存在。未为凑样本调整阈值。

## In-Progress Support

14:30和14:45的缓存回放分别对两股生成2根、3根15m的`IN_PROGRESS`视图；
只使用EARLY分类，不生成Stock 60m Risk Result，不进入160个完成周期样本。

## Data Quality

- 当前两股Preflight均为`PASS_WITH_DEGRADATION`，Close为
  `TRUSTED_WITH_TRANSFORMATION`，Confidence均为HIGH。
- Shock和Relative参考均为严格当前周期之前60个完整交易日。
- 002463的两个Closing Bucket缺失日保持`DATA_INCOMPLETE`事实，不补值。
- 一次验证中断留下的无Market Context Risk Input Snapshot不删除；manifest以
  `SUPERSEDED_INCOMPLETE_VERIFICATION_ATTEMPT`追加状态标记，正式Replay不消费它们。

## Determinism / Lookahead / Immutability

- 同一Stock Risk Input、Market Result、历史Reference重复执行，JSON业务结果完全一致。
- 历史Reference、Previous Risk Result、Market Result和15m Bar均早于或完成于当前边界；
  160/160通过。
- TASK_008/009上游对象在计算前后逐字节一致；Stock 15m不持有修改Stock Score的入口。
- Current与Replay末周期的Close、Score和Light一致。

## Snapshot与报告

- Stock 60m：`data/risk_outputs/stocks_60m/`
- Stock 15m：`data/risk_outputs/stocks_15m_internal/`
- Combined Monitor / Replay：`data/risk_outputs/stock_intraday_monitor/`
- 精确Replay Risk Input：`data/risk_inputs/stock_intraday_replay/`
- 便利投影：`data/reports/stock_intraday_risk_latest.json`

## Tests

- `uv run python -m unittest discover -v`：153/153通过。
- TASK_010定向20项测试通过：评分组件、分位数基线、灯号、降级、不评分字段、
  两种15m结构、Joint Flag、IN_PROGRESS、确定性、look-ahead、append-only及Score不可变。
- `verify_stock_intraday_risk.py`：160 Observations、Determinism、Lookahead、Score Immutability、
  Current/Replay Match均PASS。
- TASK_008/009冻结上游回归分别通过；共享four-Close分类器抽取后，市场15m历史
  分布和当前8指数结果不变。
- TASK_010范围234个JSON、4个manifest及160条Replay Market Result源路径完整性检查通过。

## 完成条件回答

1. 两只股票60m风险引擎稳定：`YES`。
2. Risk Score确定性：`YES`。
3. Relative Weakness可稳定计算：`YES`，当前两股参考均AVAILABLE。
4. Market Resonance可稳定识别：`YES`。
5. 个股独立弱势可识别：`YES`，002463历史2例。
6. 15m内部结构可稳定分类：`YES`。
7. JOINT_WEAKNESS可识别：`YES`，当前两股均命中。
8. 15m对下一周期风险变化有候选提前价值：`YES_WITH_LIMITS`，上升前兆合并40.00%，
   修复前兆弱，且两股差异大。
9. 个股+Market Risk有额外解释价值：`YES`，可区分共振、独立弱势与逆市偏强。
10. 值得进入下一阶段：`YES`，但应先验证行业板块共振，不直接扩展到调度或通知。

## Stock Intraday Risk Value

`PROMISING`。

风险评分、相对弱势、市场共振和两层15m结构均可确定性生成；市场组合明显增加了
当前风险的解释能力。但ORANGE/RED样本只有2个且无RED，因此结论仍是小样本候选价值。

## 下一阶段建议

只建议一个Task：

> TASK_011｜两只正式个股的通信设备/印制电路板行业60m共振验证 v0.1

先检验通信设备与印制电路板行业能否在不改动冻结Stock/Market Score的前提下增加解释价值。

## 是否影响Master

`NO`。本Task实现Master既定Phase 6个股风险监控，没有修改Daily、趋势系统v0.3.1、
冻结Market规则或任何交易边界。
