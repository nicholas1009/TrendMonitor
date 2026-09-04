# 600150 数据源责任矩阵 v0.1

状态：`PARTIAL`。Daily 价格与日期双源一致，但 Volume 单位语义仍未解决；未来交易日也不能在 Hithink 日历出现前提前确认。

| 能力 | Primary | Validation | 状态 | 正式语义与限制 |
| --- | --- | --- | --- | --- |
| Trading Calendar | Hithink | — | CONFIRMED | 权威至当前上海日期；不预报未来交易日，缺失时 fail closed。 |
| Auction Final | Hithink | Longbridge Daily Open（事后） | CONFIRMED_FOR_600150 | 只接受 `closed/final`；无历史日期参数。 |
| Historical Auction | — | — | UNSUPPORTED | 历史阶段只能研究 `daily_open`，不得称作 Auction。 |
| Daily OHLCV | Longbridge Direct Daily / NoAdjust | Hithink 15 日抽样 | PARTIAL_VOLUME_UNIT_CONFLICT | 日期、OHLC、Turnover 一致；Volume 原始数值约差 100 倍且本地 Longbridge 契约未声明单位。 |
| 15m / 60m | Longbridge Direct Intraday / NoAdjust | — | CONFIRMED | 只拉相似事件 T0～T+2；Hithink REST 不支持分钟 K。 |
| Latest Quote | Hithink | Longbridge | CONFIRMED_PRICE_FIELDS | 价格字段一致；Volume 仍有同一单位问题。 |
| Volume | Longbridge 同源比率 | Hithink 原始值 | UNIT_SEMANTICS_UNRESOLVED | `volume_ratio_20` 只在 Longbridge 内部计算，不做经验换算。 |
| Turnover | Longbridge | Hithink | CONFIRMED | 15 个抽样日一致到货币金额舍入范围。 |
| Sector / Industry | — | — | DEFERRED | 不临时建立船舶板块风险指标。 |

Hithink Auction 契约只允许 `thscodes` 与 `stage`。`short-term-benchmark` 虽有 `date`，但只返回 `auction_pct/tags`，不能替代历史个股 Auction Final。
