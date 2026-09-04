# 中国船舶历史突破/加仓研究 v0.1

模式：`OFFLINE_RESEARCH + SHADOW_EXPERIMENT`。非投资建议；未修改任何生产规则、Runtime、Schedule、Notification 或 Position。

## 数据与时间隔离

- 正式研究区间：2023-09-01 ～ 2026-09-03
- MA250 必要 Warm-up：2022-08-01 ～ 2023-08-31（不计入正式样本）
- Calibration：2023-09-01 ～ 2025-08-31
- Validation：2025-09-01 ～ 2026-09-03
- TARGET_EVENT：2026-09-04，严格排除在拟合、分桶阈值和历史 Outcome 之外
- Daily：Longbridge DIRECT DAILY / Period.Day / NoAdjust
- ATR：ATR14-SMA；T0 只使用 T0 及以前的 True Range

## 2026-09-04 TARGET_EVENT

| 字段 | 结果 |
| --- | ---: |
| Prev Close / Open / High / Low / Close | 34.32 / 34.50 / 37.75 / 34.50 / 37.47 |
| Volume / Turnover | 3,377,447（Longbridge 原始单位）/ 12,392,923,871 |
| Daily Return / Gap | +9.1783% / +0.5245% |
| True Range / ATR14 | 3.43 / 0.9807 |
| Range / ATR | 3.3139 |
| Volume / prior 20D average | 4.7631 |
| Close Location | 0.9138 |
| MA10 / MA20 / MA60 / MA250 | 34.528 / 34.106 / 34.618 / 35.3789 |
| prior 20D / 40D / 60D High | 35.35 / 38.26 / 38.26 |
| Breakout Distance vs 20D High | +2.1617 ATR |

判断：`STRONG_20D_CLOSE_BREAKOUT_NOT_40D_OR_60D_BREAKOUT`。它是明显的短期放量突破，但不是 40/60 日新高。目标日的日涨幅、Range/ATR 均处于宽事件池约 93.75 分位，Volume Ratio 为最高样本，Close Location 约 71.88 分位。

用户所述“均线金叉”具体组合未在 repo、docs、research 或历史规则中找到：`ENTRY_MA_CROSS = UNKNOWN`。当前仅确认 Close 位于 MA10/20/60/250 之上，四条均线当日斜率为正；这些是探索性事实，不替代原事前金叉定义。

## 宽事件池

事件定义在看 Outcome 前固定为：Daily Close 严格高于 prior 20D、40D 或 60D Daily High。prior High 排除 T0。

- 宽事件数：32（Calibration 24，Validation 8）
- breakout_20：32
- breakout_40：21
- breakout_60：18
- 与 TARGET 相同 `100` 突破结构且 MA250-ready：11（Calibration 7，Validation 4）

TARGET 的相似度使用 16 个透明 T0 特征及 Calibration-only 标准化；任何未来 Outcome 都没有进入 similarity。

## 11 个同结构相似事件

| Rank | 日期 | Segment | T+1 | T+3 | T+5 | T+10 | 5D MAE | 5D MFE | 5D 跌破/收盘跌破 |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 2023-11-30 | Calibration | -0.22% | -0.80% | +1.27% | +3.44% | -1.30% | +2.32% | NO / NO |
| 2 | 2024-09-30 | Calibration | +3.66% | +0.86% | -2.61% | -6.46% | -4.72% | +10.01% | NO / NO |
| 3 | 2026-04-23 | Validation | +0.52% | +7.05% | +8.19% | +4.92% | -1.53% | +12.49% | NO / NO |
| 4 | 2024-10-08 | Calibration | -7.27% | -6.51% | -8.94% | -9.91% | -9.21% | +1.85% | YES / YES |
| 5 | 2026-04-20 | Validation | +2.95% | +9.41% | +8.87% | +15.11% | +0.45% | +11.39% | NO / NO |
| 6 | 2025-06-26 | Calibration | +0.41% | +5.18% | +4.95% | +2.18% | -0.54% | +6.66% | NO / NO |
| 7 | 2026-04-21 | Validation | +0.44% | +6.83% | +13.77% | +13.30% | -1.16% | +15.56% | NO / NO |
| 8 | 2025-06-30 | Calibration | +2.43% | +2.21% | +2.03% | +3.66% | -0.68% | +3.87% | YES / NO |
| 9 | 2023-11-20 | Calibration | -1.01% | -1.28% | -2.44% | +4.88% | -4.39% | +0.75% | YES / YES |
| 10 | 2025-06-25 | Calibration | +0.57% | +3.27% | +4.82% | +3.21% | -0.48% | +7.27% | YES / NO |
| 11 | 2025-10-27 | Validation | +0.19% | -1.40% | -0.33% | -3.61% | -2.24% | +0.82% | YES / YES |

## 相似事件汇总

| Horizon | N | Positive Rate | Median Return | Median MAE | Median MFE | Worst MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| T+1 | 11 | 72.73% | +0.44% | -0.74% | +1.14% | -7.46% |
| T+3 | 11 | 63.64% | +2.21% | -1.30% | +3.87% | -8.08% |
| T+5 | 11 | 63.64% | +2.03% | -1.30% | +6.66% | -9.21% |
| T+10 | 11 | 72.73% | +3.44% | -1.53% | +7.27% | -12.89% |

5D 盘中重新跌破突破线为 45.45%，5D 收盘重新落到突破线下为 27.27%。样本仅 11，故 `SIMILAR_EVENT_STUDY = SAMPLE_THIN`。

## T+1 Opening Gap

Gap 桶只使用 Calibration 的同结构样本三分位确定：

- LOW_OPEN：`daily_open gap <= -0.3174%`
- NEUTRAL_OPEN：`-0.3174% < gap < +0.1537%`
- HIGH_OPEN：`gap >= +0.1537%`

Validation 样本分别只有 1、2、1 个，三类全部为 `SAMPLE_THIN → NO_SIGNAL`。

- HIGH_OPEN：Calibration 的 T+5 median -2.44%，Validation 单样本 +8.87%；没有跨阶段稳定方向。2024-09-30 的 +10.01% gap 随后首 15m -4.57%、T+5 -2.61%，说明存在 gap-and-fade 个案，但不足以证明“高开越多越差”。
- LOW_OPEN：Calibration T+3 median -0.80%、T+5 median +1.27%，Validation 单样本 T+5 +13.77%；说明低开后继续趋势的个案存在，但没有稳定样本证据。
- NEUTRAL_OPEN：Calibration 1、Validation 2，同样不足。

局部分钟数据严格只取 11 个相似事件的 T0～T+2。T+1 首 15m positive rate 54.55%、median return +0.31%、median MAE -0.44%；首 60m positive rate 54.55%、median return +0.03%、median MAE -0.58%。这些是 `POST_OPEN_PATH_STUDY`，不能作为 09:25 Feature。

## MA250 / 35.01 与 37.47 成本

- 2026-09-04 MA250：35.3789
- MA250 - 35.01：0.3689，约 0.3762 ATR；`MA250_NEAR_35 = CONFIRMED`，`KEY_LEVEL_35_01 = APPROXIMATE`
- 37.47 成本等于 T0 Close，低于 Day High 0.74%，高于 20D breakout reference 2.1617 ATR，高于 MA250 2.1322 ATR
- 11 个相似事件中，MA250 五日内触发 4/11，且 3/11 属于触发后 T+5 仍上涨的 washout；`MA250_DEFENSE_REFERENCE = INCONCLUSIVE`

成本结论：`EVIDENCE_INSUFFICIENT`。它是收盘确认价而非日内最高价，但目标日的量比、涨幅和波幅均极端，11 个样本不足以判为“合理加仓价”或“明显追高”。

## 结论

第一版历史研究不能为下一 Auction 提供通过 Calibration/Validation 的 ADD、HOLD 或 DEFENSIVE Gap 场景。正确实验输出是 `NO_SIGNAL`，等待更多同结构事件或预先注册的更宽研究问题；不得为了产生信号放宽事件定义。
