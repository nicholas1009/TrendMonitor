# Instrument Registry

更新时间：2026-08-29

## 为什么需要 Internal ID

Provider symbol 只表示某个数据源里的身份，不能充当业务层永久身份。股票代码可能带不同市场后缀；指数代码格式可能不同；板块的命名、成分与编制口径更可能不等价。因此业务层只使用稳定的 `instrument_id`，Provider 调用前再通过 `config/instruments.json` 解析。

Registry 使用 JSON，因为 Python 标准库可以直接加载、无需新增 YAML 依赖，同时文件可人工审查和版本比较。Mapping 只回答“Provider 中叫什么”；quote、daily、intraday 等 Capability 仍由能力矩阵单独表达。

## 模型

Instrument：

```text
instrument_id / display_name / asset_type / market / currency / enabled
```

Provider Mapping：

```text
instrument_id / provider / provider_symbol / provider_name
mapping_type / confidence / status / notes
```

Mapping Type：

- `EXACT`：已有证据确认是同一标的在该 Provider 中的直接身份。
- `PROXY`：业务意义接近，但不是相同身份。
- `CANDIDATE_PROXY`：候选代理，证据不足，不能当作正式等价标的。
- `UNMAPPED`：没有配置可靠映射；Resolver 动态返回且不猜 symbol。

Confidence 只使用 `HIGH / MEDIUM / LOW / UNKNOWN`，不使用没有统计依据的小数。Status 区分 `VERIFIED / CANDIDATE / NOT_CONFIGURED / UNMAPPED`；Provider 是否已接入与 identity 是否相同是两件事。

## 当前 Internal Instrument

| Internal ID | 名称 | 类型 | 市场 |
| --- | --- | --- | --- |
| `stock.hengtong_optic` | 亨通光电 | stock | SSE |
| `stock.wus_printed_circuit` | 沪电股份 | stock | SZSE |
| `index.sse_composite` | 上证指数 | index | SSE |
| `index.sse50` | 上证50 | index | SSE |
| `index.csi300` | 沪深300 | index | CN |
| `index.csi500` | 中证500 | index | CN |
| `index.csi_free_float` | 中证流通 | index | CN |
| `index.chinext` | 创业板指数 | index | SZSE |
| `index.csi1000` | 中证1000 | index | CN |
| `index.star50` | 科创50 | index | SSE |
| `sector.bank` | 银行 | sector | CN |
| `sector.coal` | 煤炭 | sector | CN |
| `sector.communication_equipment` | 通信设备 | sector | CN |
| `sector.printed_circuit_board` | 印制电路板 | sector | CN |
| `sector.semiconductor` | 半导体 | sector | CN |
| `etf.csi300.example` | 沪深300ETF华泰柏瑞 | etf | SSE |

## 当前 Hithink Mapping

| Internal ID | Hithink symbol | Mapping | Confidence | Evidence |
| --- | --- | --- | --- | --- |
| `stock.hengtong_optic` | `600487.SH` | EXACT | HIGH | TASK_001 quote/daily；TASK_002 Registry quote |
| `stock.wus_printed_circuit` | `002463.SZ` | EXACT | HIGH | TASK_001 quote/daily；TASK_002 Registry quote |
| `index.sse_composite` | `000001.SH` | EXACT | HIGH | TASK_001 quote/daily |
| `index.sse50` | `000016.SH` | EXACT | HIGH | TASK_001 quote/daily |
| `index.csi300` | `399300.SZ` | EXACT | HIGH | TASK_001 实际 symbol lookup 与 quote/daily |
| `index.csi500` | `000905.SH` | EXACT | HIGH | TASK_001；TASK_002 quote 与 32 条 daily |
| `index.csi_free_float` | `000902.SZ` | EXACT | HIGH | TASK_001 `.SH` 失败、`.SZ` 成功 |
| `index.chinext` | `399006.SZ` | EXACT | HIGH | TASK_001 quote/daily |
| `index.csi1000` | `000852.SH` | EXACT | HIGH | TASK_001 quote/daily |
| `index.star50` | `000688.SH` | EXACT | HIGH | TASK_001；TASK_002 quote 与 32 条 daily |
| `sector.bank` | `881155.TI` | EXACT | HIGH | TASK_001 同名且 quote/history/constituents 通过 |
| `sector.coal` | `881105.TI` | CANDIDATE_PROXY | LOW | 多候选，未验证成分与收益等价性 |
| `sector.communication_equipment` | `881129.TI` | EXACT | HIGH | TASK_001 同名三类调用；TASK_002 Registry quote |
| `sector.printed_circuit_board` | `884092.TI` | EXACT | HIGH | TASK_011 `tag=industry`目录同名且002463.SZ成分命中；Quote/Daily通过，15m/60m不支持 |
| `sector.semiconductor` | `881121.TI` | EXACT | HIGH | TASK_001 同名且三类调用通过 |
| `etf.csi300.example` | `510300.SH` | EXACT | HIGH | TASK_001 profile/quote/daily |

## BK0437 特殊情况

`sector.coal` 在 Eastmoney 中保存为 `BK0437 煤炭 / EXACT / NOT_CONFIGURED`。这里的 EXACT 只表示该代码就是 Eastmoney 自身的该板块身份，不表示它与其他 Provider 的板块等价。

TASK_001 找到 Hithink 候选 `881105.TI 煤炭开采加工`、`884014.TI 煤炭开采`、`884281.TI 煤化工`，但没有完成成分股与收益相关性验证。Registry 仅把 `881105.TI` 登记为当前首要 `CANDIDATE_PROXY / LOW`，其余候选保留在证据说明中；不存在 `BK0437 -> 881105.TI` 字符串替换，也不存在 Hithink EXACT 映射。

## Resolver 行为

```python
registry.resolve("index.csi500", "hithink")
# 000905.SH / EXACT / HIGH / VERIFIED

registry.resolve("index.csi500", "longbridge")
# 000905.SH / EXACT / HIGH / VERIFIED
```

未知 Internal ID 抛出统一 `UNMAPPED` 错误；已知 Internal ID 但 Provider 未映射时返回显式 UNMAPPED 对象。

## TASK_003 / TASK_007 Longbridge状态

当前已认证验证以下 identity：

| Internal ID | Longbridge symbol | Mapping | Confidence | Status |
| --- | --- | --- | --- | --- |
| `stock.hengtong_optic` | `600487.SH` | EXACT | HIGH | VERIFIED |
| `stock.wus_printed_circuit` | `002463.SZ` | EXACT | HIGH | VERIFIED |
| `index.sse_composite` | `000001.SH` | EXACT | HIGH | VERIFIED |
| `index.sse50` | `000016.SH` | EXACT | HIGH | VERIFIED |
| `index.csi300` | `000300.SH` | EXACT | HIGH | VERIFIED |
| `index.csi500` | `000905.SH` | EXACT | HIGH | VERIFIED |
| `index.csi_free_float` | `000902.SH` | EXACT | HIGH | VERIFIED |
| `index.chinext` | `399006.SZ` | EXACT | HIGH | VERIFIED |
| `index.csi1000` | `000852.SH` | EXACT | HIGH | VERIFIED |
| `index.star50` | `000688.SH` | EXACT | HIGH | VERIFIED |
| `etf.csi300.example` | `510300.SH` | EXACT | HIGH | VERIFIED |

两只股票、ETF和正式8指数均通过认证行情调用。8指数均由`static_info`返回匹配中文名称，并逐项通过Quote、Daily、15m和60m Raw调用；因此不是按代码格式猜测。中证流通的`exchange/currency`元数据为空，但名称、Board及四类行情证据明确，缺失事实已记录。Longbridge全部板块仍保持`UNMAPPED`。
