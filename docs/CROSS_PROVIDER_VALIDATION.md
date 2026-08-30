# Cross Provider Validation

日期：2026-08-29

状态：`COMPLETED / REVIEW_REQUIRED`

数据源：Hithink REST 与 Longbridge OpenAPI；双方均使用未复权 `none / NoAdjust`，按 `Asia/Shanghai` 交易日对齐。

## 比较结果

| Instrument | Period | Days | Date Range | Price Match | Max Abs Diff | Max Relative Diff | Volume | Conflict |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- |
| 600487 亨通光电 | 1d | 64 | 2026-06-01–08-28 | MATCH | 0 | 0 | UNIT_UNKNOWN | NO |
| 002463 沪电股份 | 1d | 64 | 2026-06-01–08-28 | MATCH | 0 | 0 | UNIT_UNKNOWN | NO |
| 中证500 | 1d | 64 | 2026-06-01–08-28 | REVIEW_REQUIRED | 0.01 | 0.0000013393 | UNIT_UNKNOWN | NO |
| 科创50 | 1d | 64 | 2026-06-01–08-28 | REVIEW_REQUIRED | 0.01 | 0.0000062956 | UNIT_UNKNOWN | NO |

两只股票64个共同交易日的OHLC逐字段完全一致。

中证500有11个非零字段差异、科创50有13个，观察到的最大绝对差均为0.01指数点；最大相对差分别约0.000134%和0.000630%。当前没有经批准的生产冲突阈值，因此按配置返回 `REVIEW_REQUIRED`，没有擅自提升为 `PRICE_CONFLICT`，也没有静默选源。

示例异常：

- 中证500 2026-07-27 low：Hithink 7466.54，Longbridge 7466.55。
- 中证500 2026-08-25 high：Hithink 7755.72，Longbridge 7755.73。
- 科创50 2026-07-30 close：Hithink 1588.41，Longbridge 1588.42。
- 科创50 2026-08-25 high：Hithink 1622.20，Longbridge 1622.21。

这些差异很小且可解释为指数小数精度/源端舍入差异，但这只是基于数值幅度的判断；在阈值正式批准前仍保留 `REVIEW_REQUIRED`。

## Volume

不同 Provider 的股票/指数 Volume 单位尚未通过官方字段单位与实值共同确认，继续标记 `UNIT_UNKNOWN`，不据此判定 `DATA_CONFLICT`。

## 结果证据

机器可读报告：`data/reports/cross_provider_latest.json`。

每条 Longbridge 与 Hithink 结果均经 Registry、Raw Cache、Normalizer 和 Source Trace 取得；没有使用 Mock 行情填充比较。
