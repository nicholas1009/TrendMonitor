# Phase 3 Result

状态：`PARTIAL`

日期：2026-08-29

Longbridge认证、股票/指数/ETF真实行情、双源比较和真实Fallback均已完成。保留 `PARTIAL` 的原因不再是凭证，而是长窗口分钟数据中少量09:30 Bar违反严格OHLC关系；最近120根中证50060m因此明确返回 `INVALID_DATA`。

## Q1｜Longbridge是否已经真实接入？

是。官方 `longbridge==4.5.0` SDK 的 `QuoteContext` 已认证；Provider、Adapter、Raw Cache、Normalizer、Validator和Source Trace均经过真实行情。

## Q2｜能否作为Hithink备用数据源？

可以用于已验证的股票、两个指数和ETF能力。受控 Hithink `NETWORK_ERROR` 后，Longbridge取得真实600487 Quote，结果保留 requested/actual Provider、失败原因和Longbridge Raw路径。

## Q3｜15m是否DIRECT？

`DIRECT`。两只股票和中证500、科创50的 `candlesticks(Period.Min_15)` 均真实成功；两只股票的 `history_candlesticks_by_offset` 也成功。最近120根四标的均通过完整链路。

## Q4｜60m是否DIRECT？

`DIRECT`。四标的 Raw SDK 调用均成功。最近120根中，两只股票和科创50通过完整链路；中证500含一根源端 OHLC 异常，被严格 Validator标记 `INVALID_DATA`。能力为DIRECT不代表所有历史Bar均有效。

## Q5｜股票和指数的分钟能力是否一致？

接口能力一致，均支持15m和60m。数据质量不完全一致：最近1000根扫描中股票异常更少，两个指数在09:30 Bar出现更多 open 位于 high/low 之外的记录。未修改或自动修正源数据。

## Q6｜Hithink与Longbridge日线数据一致性如何？

- 600487：64个共同交易日，OHLC完全一致，`MATCH`。
- 002463：64个共同交易日，OHLC完全一致，`MATCH`。
- 中证500：64日，最大绝对差0.01指数点，`REVIEW_REQUIRED`。
- 科创50：64日，最大绝对差0.01指数点，`REVIEW_REQUIRED`。

双方使用 `none / NoAdjust`。Volume单位仍为 `UNIT_UNKNOWN`。

## Q7｜是否存在明显DATA_CONFLICT？

没有。股票零差异；指数差异最大0.01点。由于正式冲突阈值尚未批准，指数保持 `REVIEW_REQUIRED`，不自动判定冲突。

## Q8｜真实Fallback是否成功？

`PASS`：requested_provider=`hithink`，actual_provider=`longbridge`，fallback_reason=`hithink:NETWORK_ERROR`。

## Q9｜当前趋势系统的数据缺口还剩什么？

- Longbridge分钟09:30 Bar中 open 与 high/low 的源端口径解释。
- 中证500等指数的历史分钟严格质量问题。
- 双源Volume单位确认。
- Longbridge其他六个正式指数仍未映射。
- Longbridge板块体系仍为UNMAPPED；板块继续由Hithink承担。
- 15:00额外收盘Bar如何适配未来“每日4个60m周期”的正式口径尚未决定。

## Q10｜下一步是否还需要LOCAL_AGGREGATION？

当前不需要优先开发。15m和60m均可DIRECT取得。应先解决源端分钟口径与质量策略；TASK_003不实现聚合。

## Scenario

`A`

```text
15m = DIRECT
60m = DIRECT
```

无需优先开发 `LOCAL_AGGREGATION`。但 Direct 只描述接口能力，不消除历史分钟质量风险。

## 15:00额外Bar

最近5个完整交易日、两只股票的15m和60m均有独立15:00 Bar。10/10个标的日样本中，该Bar有正量额、OHLC为同一价格、Close等于日线Close；纳入后量额显著更接近日线。源端仍标记 `TradeSession.Intraday`，没有明确Auction字段，因此当前结论是“高度符合独立收盘成交/最终收盘价桶”，不得自动删除或合并。详见 `LONGBRIDGE_MINUTE_CONVENTION.md`。

## 实际验证

- `uv run python -m unittest discover -v`：47项通过，0失败。
- `uv run python scripts/verify_registry.py`：TASK_002回归19 PASS / 0 FAIL。
- `uv run python scripts/verify_hithink.py`：TASK_001回归69 PASS / 0 FAIL / 2 UNSUPPORTED / 1 UNKNOWN。
- `uv run python scripts/verify_longbridge.py`：认证、四标的Quote/Daily、股票和指数分钟Raw、ETF、四标的交叉比较及真实Fallback均执行；由于中证500最近120根60m中的一根被标记 `INVALID_DATA`，脚本退出码为1。
- 直接SDK探针：两只股票的 `candlesticks` 与 `history_candlesticks_by_offset`，15m/60m共8次调用全部成功。
- Python编译检查通过；Secret扫描只命中空配置名与测试假值。

## Master影响

`NO`。没有修改Master或趋势系统v0.3.1。

## 官方依据

- <https://open.longbridge.com/docs/getting-started>
- <https://open.longbridge.com/docs/quote/overview>
- <https://open.longbridge.com/docs/quote/pull/candlestick>
- <https://open.longbridge.com/docs/quote/pull/history-candlestick>
- <https://open.longbridge.com/docs/error-codes>
