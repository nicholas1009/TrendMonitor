# TASK_007 Market Index Coverage

验证日期：2026-08-30（本地Asia/Tokyo；行情业务时间统一Asia/Shanghai）

## 结论

六个新增候选均通过Longbridge正式凭证逐项验证，Mapping全部为`EXACT / HIGH / VERIFIED`。Quote、Daily、15m和60m接口全部为`DIRECT`。最新完整交易日2026-08-28的六份Risk Input均生成成功；连同中证500和科创50，完整市场Bundle为`FULL_READY (8/8)`，但依据Safe Feature Contract统一为`PASS_WITH_DEGRADATION`。

`FULL_READY`只表示8个指数都有可消费的安全输入，不表示所有源字段无质量问题，也不实现任何风险判断。

## Mapping身份

| Internal ID | Longbridge | static_info中文名 | English | Exchange | Board | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| `index.sse_composite` | `000001.SH` | 上证指数 | SSE Index | SSE | CNIX | EXACT / HIGH / VERIFIED |
| `index.sse50` | `000016.SH` | 上证50 | SSE 50 | SSE | CNIX | EXACT / HIGH / VERIFIED |
| `index.csi300` | `000300.SH` | 沪深300 | CSI 300 | SSE | CNIX | EXACT / HIGH / VERIFIED |
| `index.csi_free_float` | `000902.SH` | 中证流通 | 中证流通 | 空 | CNIX | EXACT / HIGH / VERIFIED |
| `index.chinext` | `399006.SZ` | 创业板指 | ChiNext | SZSE | CNIX | EXACT / HIGH / VERIFIED |
| `index.csi1000` | `000852.SH` | 中证1000 | 中证1000 | SSE | CNIX | EXACT / HIGH / VERIFIED |

中证流通的`exchange`和`currency`由Provider返回空字符串；中文名、Provider Symbol、Board、Quote及三类历史行情共同确认身份。该metadata缺失没有被填造。

## 六指数真实结果

请求窗口为2026-07-20至2026-08-29，实际返回30个完整交易日。每个指数Daily返回110根；每个指数15m返回510根（17根Source/日），60m返回150根（5根Source/日）。

| 指数 | Mapping | Daily | 15m | 60m | 30日System Bar | 最新Preflight |
| --- | --- | --- | --- | --- | --- | --- |
| 上证指数 | EXACT/HIGH/VERIFIED | PASS | DIRECT | DIRECT | 15m 29/30；60m 30/30 | PASS_WITH_DEGRADATION |
| 上证50 | EXACT/HIGH/VERIFIED | PASS | DIRECT | DIRECT | 15m 30/30；60m 30/30 | PASS_WITH_DEGRADATION |
| 沪深300 | EXACT/HIGH/VERIFIED | PASS | DIRECT | DIRECT | 15m 30/30；60m 30/30 | PASS_WITH_DEGRADATION |
| 中证流通 | EXACT/HIGH/VERIFIED | PASS | DIRECT | DIRECT | 15m 30/30；60m 30/30 | PASS_WITH_DEGRADATION |
| 创业板指 | EXACT/HIGH/VERIFIED | PASS | DIRECT | DIRECT | 15m 28/30；60m 30/30 | PASS_WITH_DEGRADATION |
| 中证1000 | EXACT/HIGH/VERIFIED | PASS | DIRECT | DIRECT | 15m 30/30；60m 30/30 | PASS_WITH_DEGRADATION |

通过日期均严格生成16根15m和4根60m System Bar，最后周期为`MERGE_CLOSING_BUCKET`，时间为Asia/Shanghai，并保存Source Bar ID与Raw路径Lineage。

## REVIEW_REQUIRED证据

30日扫描发现三条负Turnover Source Bar：

| Symbol | 日期时间（Asia/Shanghai） | Period | Turnover | Validator |
| --- | --- | --- | ---: | --- |
| `000001.SH` | 2026-07-27 14:45 | 15m | -101,025,788,824.40 | INVALID_DATA |
| `399006.SZ` | 2026-08-25 10:30 | 15m | -121,397,978,597.77 | INVALID_DATA |
| `399006.SZ` | 2026-08-26 10:30 | 15m | -120,074,561,241.69 | INVALID_DATA |

这些异常不是09:30 `SOURCE_BOUNDARY_QUIRK`，也不是OHLC错误。Raw未修改，严格Source Validator未放宽，对应交易日不会生成System Bar。由于指数Turnover在既有Contract中仅为`ADVISORY_ONLY`，本Task不提升其质量，也不自动制定新的全局转换；建议后续将证据提交Provider核查。

最新Risk Input只请求能覆盖当前/最近完整日的有界Source窗口（15m 35根、60m 11根）。历史质量扫描独立保留30日证据，因此旧日非核心字段异常不会错误阻断2026-08-28的有效Close输入；若异常发生在当前核心窗口，严格Validator仍会BLOCK，未被绕过。

## Safe Feature与Preflight

六个指数继续执行TASK_005正式Contract：Open/High/Low为`APPROXIMATE`，Close为`TRUSTED`（Closing Bucket为`TRUSTED_WITH_TRANSFORMATION`），Volume为`BLOCKED`，Turnover为`ADVISORY_ONLY`。因此：

- Close Feature：ENABLED；
- High/Low精确触发：DISABLED；
- Index Volume：DISABLED；
- Turnover：DEGRADED / ADVISORY；
- Preflight：`PASS_WITH_DEGRADATION`。

## Snapshot与Provenance

六个新增指数分别保存append-only Instrument Snapshot；中证500和科创50重新生成当前Snapshot后，保存完整8指数Group Snapshot。JSON回读等于原始`to_dict()`，每个Feature可追溯到System Bar、Normalized记录、Raw路径、Longbridge Provider及Provider Symbol。

机器可读最新报告：`data/reports/market_index_coverage_latest.json`。所有真实Raw位于`data/raw/longbridge/`并登记于`data/raw/manifest.jsonl`。

## 边界

本Task未修改Safe Feature Contract、正式Daily规则、趋势系统v0.3.1或任何风险规则；未实现风险评分、风险灯、调度、通知、交易或LOCAL_AGGREGATION。
