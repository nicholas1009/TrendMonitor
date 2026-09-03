# TASK_017｜沪电股份 Livermore natural_move_k 研究基线 v0.1

状态：SUCCESS

## Scope

仅研究 `002463.SZ｜沪电股份` 的 `natural_move_k`。没有实现或优化
`exit_trailing_k`、`reentry_buffer_k`、`pivot_confirm_k` 或
`secondary_move_k`，也不产生买卖信号。

## Historical Data

- Longbridge `history_candlesticks_by_date`
- `DIRECT_DAILY / Period.Day / NoAdjust`
- 范围：2023-08-01～2026-09-01
- 日线：749 个交易日；ATR14-SMA 可用：736 个交易日
- ATR：当日 True Range 的 14 日简单移动平均；T 日只使用 T 日及以前数据
- Calibration：2023-08-18～2025-08-26，490 日
- Validation：2025-08-27～2026-09-01，246 日
- 时间顺序切分；没有随机拆分

## Minimal State Logic

本基线只做因果 ATR 反转记录：下降方向从持续更新的最低点向上达到
`natural_move_k × 该低点当日ATR` 时进入 Natural Rally；上升方向从持续更新的
最高点向下达到同样幅度时进入 Natural Reaction。ATR 固定在极值形成日。

新极值形成当日不允许同时触发反转，避免借用 Daily OHLC 无法证明的盘中高低顺序。
Natural 状态持续到下一次反向阈值触发。由于 `pivot_confirm_k` 尚未定义，本实验不把
Natural Rally/Reaction 晋升为新的主趋势，也不生成 Secondary 状态。

## Whipsaw Definition

一次 Natural 状态切换后，在 5 或 10 个交易日内出现相反切换，且切换后的有利方向
最大运动不足触发日 1ATR，记为 `WHIPSAW_CANDIDATE`。1ATR 只用于固定的结果评价，
没有参与参数搜索。

“过早打断”定义为：20 日内出现反向切换，且随后在首个切换后的 20 日观察窗内，
价格又突破首个切换前的原方向极值。

## Legacy Replay

`natural_move_k=2.0` 成功逐日恢复 31/31 个 Legacy 状态，并在 2026-08-05
恢复 `下降趋势 → 自然回升`：

- 2026-07-30 Longbridge Low：94.73
- 当日 ATR14-SMA：10.430714，三位小数为 10.431
- Legacy 口径阈值：`94.73 + 2 × 10.431 = 115.592`
- 2026-08-05 Close：115.60，首次满足阈值

Longbridge 精确 ATR 得到的未舍入阈值为 115.591429；不影响触发日期。

数据源存在两个已保留差异：Legacy 的 2026-08-12 记录价为 123.02，Longbridge
NoAdjust Close 为 123.01，因此价格逐日匹配为 30/31；Legacy 侧栏的 2026-08-07
自然回升高点为 127.73，Longbridge Daily High 为 127.77。这些差异没有被改写。

## Natural Move Sensitivity

| k | Rally | Reaction | Median Duration | 5D Whipsaw | 10D Whipsaw | 10D Rate | Median Delay | Legacy Replay | Validation Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1.00 | 78 | 78 | 4.0 | 66 | 75 | 48.1% | 1.0 | FAIL | OUTSIDE |
| 1.25 | 57 | 56 | 5.0 | 33 | 39 | 34.5% | 2.0 | FAIL | OUTSIDE |
| 1.50 | 48 | 47 | 6.5 | 23 | 33 | 34.7% | 3.0 | FAIL | OUTSIDE |
| 1.75 | 40 | 39 | 8.0 | 17 | 28 | 35.4% | 3.0 | PASS | IN REGION |
| 2.00 | 31 | 30 | 10.0 | 11 | 19 | 31.1% | 3.0 | PASS | IN REGION |
| 2.25 | 24 | 23 | 11.0 | 9 | 14 | 29.8% | 4.0 | FAIL | IN REGION |
| 2.50 | 23 | 22 | 11.0 | 8 | 12 | 26.7% | 4.0 | FAIL | IN REGION |
| 2.75 | 17 | 16 | 13.0 | 3 | 9 | 27.3% | 5.0 | FAIL | OUTSIDE |
| 3.00 | 14 | 14 | 12.0 | 2 | 9 | 32.1% | 5.5 | FAIL | OUTSIDE |

这里的 Legacy Replay `PASS` 表示整个 Legacy 区间状态逐日相同，不只是触发日相同。

## Calibration vs Validation

| k | Cal Events | Val Events | Cal 10D Whipsaw Rate | Val 10D Whipsaw Rate | Cal Median Delay | Val Median Delay |
|---:|---:|---:|---:|---:|---:|---:|
| 1.00 | 99 | 57 | 45.5% | 52.6% | 1.0 | 1.0 |
| 1.25 | 75 | 38 | 32.0% | 39.5% | 2.0 | 1.5 |
| 1.50 | 65 | 30 | 33.8% | 36.7% | 3.0 | 2.0 |
| 1.75 | 53 | 26 | 37.7% | 30.8% | 3.0 | 2.0 |
| 2.00 | 39 | 22 | 33.3% | 27.3% | 4.0 | 2.0 |
| 2.25 | 31 | 16 | 38.7% | 12.5% | 5.0 | 2.5 |
| 2.50 | 29 | 16 | 34.5% | 12.5% | 5.0 | 3.0 |
| 2.75 | 19 | 14 | 36.8% | 14.3% | 5.0 | 4.5 |
| 3.00 | 15 | 13 | 26.7% | 38.5% | 5.0 | 7.0 |

1.75～2.50 的事件数、持续期、Whipsaw 和延迟随 k 平滑变化，没有单点断崖；四者在
Validation 均未出现 Whipsaw 恶化。2.25 和 2.50 的 Validation 事件均只有 16 个，且
Whipsaw 比例与 Calibration 差异较大，因此 2.50 只是候选区间边界，不是已确认的更优值。

3.0 在 Validation 的中位识别延迟升至 7 日、10日 Whipsaw 比例反而升至 38.5%，
说明继续增大 k 并不稳定改善结果。1.0 则产生 156 次切换和 48.1% 的 10日 Whipsaw，
明显过敏。

## Stable Region

候选稳定区间：**1.75～2.50**。

这是粗网格下的研究区间，不是正式参数结论。没有进行局部细化，因为当前结果没有
显示值得提高搜索精度的尖锐边界；继续细化会放大偶然最优风险。

## Current 2ATR Assessment

**REASONABLE**

依据：2.0 位于候选稳定区间内部；完整样本 61 次切换、10日 Whipsaw 19 次
（31.1%）、中位状态持续 10 日、中位识别延迟 3 日；Validation 的 Whipsaw 比例
由 Calibration 的 33.3% 降至 27.3%，且 Legacy 状态 31/31 恢复。

## Coefficient Boundaries

- `natural_move_k`：本 Task 唯一研究参数；当前 2.0 评价为 REASONABLE
- `exit_trailing_k`：仍为 2.0；状态：`FORMAL_CURRENT_NOT_SEPARATELY_OPTIMIZED`；本研究未对其单独优化，也未判断未来是否值得研究
- `reentry_buffer_k`：仍为 0.5；是否修改：NO
- `pivot_confirm_k`：TBD
- `secondary_move_k`：TBD

自然移动与正式离场虽然当前数值都为 2.0，但在研究代码和结论中没有绑定。

## Limitations

- 这是 ATR 反转记录基线，不是完整 Livermore 六栏算法。
- 没有 Pivotal Point、Secondary Rally 或 Secondary Reaction 自动化。
- 没有收益优化、交易信号或交易成本假设。
- 2.25 以上 Validation 事件数只有 13～16，样本较薄。
- Legacy 只有 31 个交易日，不能单独证明长期参数有效。

## Production Impact

NONE

Legacy Excel、正式风险规则、Safe Feature Contract、通知策略、Runtime、LaunchAgent
和 Bark 均未接入或修改。
