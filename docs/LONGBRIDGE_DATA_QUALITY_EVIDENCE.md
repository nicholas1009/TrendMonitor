# Longbridge分钟/日线字段差异Evidence Bundle

生成日期：2026-08-29  
用途：供未来向Longbridge官方Support反馈；本Task未向外部发送。  
口径：`NoAdjust`，`Asia/Shanghai`。

## 总体结论

四标的各60个共同完整交易日中，1m、15m、60m和System Bar的Close彼此及与Daily均精确一致。High/Low和量额存在稳定的跨周期语义差异；两个指数各有分钟Volume接近Daily两倍的个别异常日。1m、15m、60m聚合结果在这些异常日相互一致，说明差异来自Longbridge分钟数据体系与Daily的口径，而非TrendMonitor Closing Bucket算法。

完整逐Bar值、差异分布和Lineage见 `data/reports/risk_input_quality_latest.json`。

## 002463.SZ｜2026-08-06

| 来源 | O | H | L | C | Volume | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Daily DIRECT | 112.74 | 121.87 | 112.01 | 117.20 | 589,491 | 6,909,114,239.45 |
| 1m全天聚合 | 112.74 | 121.85 | 112.08 | 117.20 | 592,047 | 6,939,053,023.00 |
| System 15m全天 | 112.74 | 121.85 | 112.08 | 117.20 | 592,047 | 6,939,053,023.00 |
| System 60m全天 | 112.74 | 121.85 | 112.08 | 117.20 | 592,047 | 6,939,053,023.00 |

相对Daily：High低0.02、Low高0.07、Volume多2,556（+0.434%）、Turnover多29,938,783.55（+0.433%）；Close差0。

Raw证据：

- Daily：`data/raw/longbridge/daily/2026-08-29/20260829T144401.327847Z__stock.wus_printed_circuit__002463.SZ__daily__src-1776009600000-1787846400000__3adebe8b.json`
- 1m：`data/raw/longbridge/1m/2026-08-29/20260829T144408.009776Z__stock.wus_printed_circuit__002463.SZ__1m__src-1785720600000-1785999600000__d0f9fb92.json`
- 15m：`data/raw/longbridge/15m/2026-08-29/20260829T144411.335724Z__stock.wus_printed_circuit__002463.SZ__15m__src-1784165400000-1787900400000__9c8ea738.json`
- 60m：`data/raw/longbridge/60m/2026-08-29/20260829T144411.863864Z__stock.wus_printed_circuit__002463.SZ__60m__src-1780277400000-1787900400000__5ddc3e12.json`

运行时处理：Volume与Turnover关闭；Close类Feature继续。

## 000905.SH｜2026-08-05 opening boundary

| 来源 | O | H | L | C | Volume | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Daily DIRECT | 7,567.96 | 7,845.39 | 7,567.96 | 7,809.21 | 222,031,817 | 526,922,799,394.20 |
| 1m全天聚合 | 7,567.96 | 7,845.39 | 7,567.96 | 7,809.21 | 222,020,813 | 526,896,981,517.64 |
| System 15m全天 | 7,567.96 | 7,845.39 | 7,567.96 | 7,809.21 | 222,020,813 | 526,896,981,517.64 |
| System 60m全天 | 7,567.96 | 7,845.39 | 7,567.96 | 7,809.21 | 222,020,813 | 526,896,981,517.64 |

相对Daily：OHLC完全一致；Volume少11,004、Turnover少25,817,876.56。该日的重点不是全天价格差，而是DIRECT 15m/60m 09:30 Source Low高于Open，1m与Daily均支持Open作为当日低点；因此只对Derived envelope作显式标记，不改Source。

Raw证据：

- Daily：`data/raw/longbridge/daily/2026-08-29/20260829T144413.912880Z__index.csi500__000905.SH__daily__src-1776009600000-1787846400000__207a4d0a.json`
- 1m：`data/raw/longbridge/1m/2026-08-29/20260829T144421.549466Z__index.csi500__000905.SH__1m__src-1785720600000-1785999600000__5f061b59.json`
- 15m：`data/raw/longbridge/15m/2026-08-29/20260829T144426.464980Z__index.csi500__000905.SH__15m__src-1784165400000-1787900400000__33c3c13b.json`
- 60m：`data/raw/longbridge/60m/2026-08-29/20260829T144426.646553Z__index.csi500__000905.SH__60m__src-1780277400000-1787900400000__f35d737e.json`

运行时处理：09:30 `SOURCE_BOUNDARY_ENVELOPE`显式记录，High/Low仍为`APPROXIMATE`，Raw不修改。

## 000905.SH｜2026-08-07

| 来源 | O | H | L | C | Volume | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Daily DIRECT | 7,828.79 | 7,983.17 | 7,763.53 | 7,980.13 | 206,575,632 | 525,281,484,431.50 |
| 1m全天聚合 | 7,828.79 | 7,983.17 | 7,763.53 | 7,980.13 | 408,787,661 | 525,253,884,605.51 |
| System 15m全天 | 7,828.79 | 7,983.17 | 7,763.53 | 7,980.13 | 408,787,661 | 525,253,884,605.51 |
| System 60m全天 | 7,828.79 | 7,983.17 | 7,763.53 | 7,980.13 | 408,787,661 | 525,253,884,605.51 |

相对Daily：OHLC完全一致；Volume多202,212,029（+97.89%），Turnover少27,599,825.99；Close差0。1m、DIRECT 15m和DIRECT 60m的全天量额结论一致。

Raw证据：

- Daily：`data/raw/longbridge/daily/2026-08-29/20260829T144413.912880Z__index.csi500__000905.SH__daily__src-1776009600000-1787846400000__207a4d0a.json`
- 1m：`data/raw/longbridge/1m/2026-08-29/20260829T144422.082839Z__index.csi500__000905.SH__1m__src-1786066200000-1786518000000__b44d46aa.json`
- 15m、60m：同上方中证500窗口Raw。

运行时处理：指数Volume关闭；Close类Feature继续。分钟Volume比Daily高约97.89%。

## 000688.SH｜2026-08-21

| 来源 | O | H | L | C | Volume | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Daily DIRECT | 1,649.28 | 1,675.82 | 1,640.62 | 1,653.56 | 6,975,365 | 77,573,154,799.00 |
| 1m全天聚合 | 1,649.28 | 1,675.82 | 1,640.62 | 1,653.56 | 13,787,375 | 77,569,619,135.00 |
| System 15m全天 | 1,649.28 | 1,675.82 | 1,640.62 | 1,653.56 | 13,787,375 | 77,569,619,135.00 |
| System 60m全天 | 1,649.28 | 1,675.82 | 1,640.62 | 1,653.56 | 13,787,375 | 77,569,619,135.00 |

相对Daily：OHLC完全一致；Volume多6,812,010（+97.66%），Turnover少3,535,664；Close差0。1m、DIRECT 15m和DIRECT 60m的全天量额结论一致。

Raw证据：

- Daily：`data/raw/longbridge/daily/2026-08-29/20260829T144428.671707Z__index.star50__000688.SH__daily__src-1776009600000-1787846400000__820a6fb0.json`
- 1m：`data/raw/longbridge/1m/2026-08-29/20260829T144437.081307Z__index.star50__000688.SH__1m__src-1787103000000-1787554800000__6383ba76.json`
- 15m：`data/raw/longbridge/15m/2026-08-29/20260829T144438.698475Z__index.star50__000688.SH__15m__src-1784165400000-1787900400000__679b910c.json`
- 60m：`data/raw/longbridge/60m/2026-08-29/20260829T144439.240946Z__index.star50__000688.SH__60m__src-1780277400000-1787900400000__9b086f47.json`

运行时处理：Volume、Turnover关闭；Close类Feature继续。分钟Volume比Daily高约97.67%。

## 建议给Provider的问题

1. A股指数分钟Volume与Daily Volume的精确定义、单位和成分范围是什么？
2. 为什么个别指数交易日的1m/15m/60m汇总Volume接近Daily的两倍，而Turnover并未同比例翻倍？
3. 为什么股票分钟High/Low无法稳定重构同源Daily High/Low？
4. 09:30指数Bar中Open位于Source High/Low区间外是否为预期边界语义？

证据包仅记录事实，不自动修正Raw，也不自动发送。
