# Auction Final / Daily Open Bridge v0.1

结论：`AUCTION_OPEN_BRIDGE = PROVISIONAL_CONFIRMED`

Hithink 契约明确区分 `auction_price`（竞价价格）和 `open_price`（开盘价）；Longbridge 历史字段为 `daily_open`。5 个具备日期归属的真实样本中，三个字段逐一相等：

| 日期 | 标的 | auction_price | open_price | daily_open | 差值 |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-09-03 | 600487.SH | 67.77 | 67.77 | 67.77 | 0.00 |
| 2026-09-03 | 002463.SZ | 120.40 | 120.40 | 120.40 | 0.00 |
| 2026-09-04 | 600487.SH | 65.70 | 65.70 | 65.70 | 0.00 |
| 2026-09-04 | 002463.SZ | 118.00 | 118.00 | 118.00 | 0.00 |
| 2026-09-04 | 600150.SH | 34.50 | 34.50 | 34.50 | 0.00 |

限制：样本只有 5 个，因此 Historical `daily_open` 只能作为下一次 Auction 实验的近似训练桥梁，不能被改名为历史 `auction_price`。Hithink Auction snapshot 没有历史日期参数；`HISTORICAL_AUCTION = UNSUPPORTED`。
