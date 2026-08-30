# TrendMonitor Data Capability Matrix

更新时间：2026-08-30（Asia/Tokyo）

真实验证入口：`uv run python scripts/verify_hithink.py`

状态语义：

- `DIRECT`：官方端点存在，且本次有效凭据真实调用成功；具体数据质量失败另以 `INVALID_DATA` 记录，不把接口能力误写为不支持。
- `LOCAL_AGGREGATION`：源端不提供该周期，只能通过本地实时快照采样构造，并必须标记 `locally_aggregated`。
- `UNSUPPORTED`：官方明确排除，或实际请求被接口拒绝。
- `UNKNOWN`：没有足够证据，或标的映射不能唯一确定。

## 双 Provider 汇总

TASK_003 已通过 Longbridge 正式凭证完成认证调用。15m/60m 的 `DIRECT` 结论同时有 Raw SDK 与完整业务链证据，不再仅依据 SDK 枚举。

| Capability | Hithink | Longbridge | Final | Evidence |
| --- | --- | --- | --- | --- |
| Stock Quote | DIRECT | DIRECT | DIRECT | 两只股票均完成认证 Quote |
| Daily OHLCV | DIRECT | DIRECT | DIRECT | TASK_007新增六指数各取得110根日线；正式8指数均有Longbridge日线来源 |
| 15m | UNSUPPORTED | DIRECT | DIRECT | 正式8指数逐项实调成功；新增六指数30日各510根。上证指数1日、创业板2日因负Turnover被严格Validator标为`REVIEW_REQUIRED`，接口能力仍为DIRECT |
| 60m | UNSUPPORTED | DIRECT | DIRECT | 新增六指数30日各150根并全部形成4根/日System Bar |
| Index | DIRECT | DIRECT | DIRECT | 正式8指数均以`static_info`名称、Quote、Daily、15m、60m真实验证身份和能力 |
| Sector Quote/Daily | DIRECT | UNSUPPORTED / UNMAPPED | DIRECT | Longbridge没有建立同义板块mapping；Hithink行业目录、Quote、Daily可用 |
| Sector 15m/60m | UNSUPPORTED | UNMAPPED | BLOCKED | TASK_011对通信设备881129.TI与印制电路板884092.TI实调15m/60m均返回code=1002；没有跨Provider猜码或Synthetic替代 |
| ETF | DIRECT | DIRECT | DIRECT | Longbridge 510300认证Quote成功 |
| Index Risk Input Readiness | N/A | DIRECT + DERIVED SYSTEM BAR | YES_WITH_LIMITS | 最新完整交易日8/8生成Daily、16根15m、4根60m输入；指数Volume禁用，Preflight均`PASS_WITH_DEGRADATION` |

Longbridge 官方契约另确认：Quote 权限独立于交易权限；CN Basic quote 随 OpenAPI 激活提供；历史 K 单次最多1000根；A股分钟历史文档范围为2022-08至今；历史 K 端点限制60次/30秒，通用 Quote 限制10次/秒且并发不超过5。TASK_003没有做压力测试。

分钟接口能力与分钟数据质量必须分开解释：接口为 `DIRECT`；长窗口1000根扫描发现少量09:30 Bar的 open 不在 high/low 内，具体见 `LONGBRIDGE_MINUTE_CONVENTION.md`。Raw不被修改，严格 Validator 仍会返回 `INVALID_DATA`。

TASK_007新增质量证据同样不改变DIRECT结论：`000001.SH`在2026-07-27 14:45、`399006.SZ`在2026-08-25/26 10:30返回负Turnover。Raw保持原样；长窗口对应日期不生成System Bar并标记`REVIEW_REQUIRED`。最新交易日2026-08-28不含这些异常，8指数Risk Input均可消费。详见`MARKET_INDEX_COVERAGE.md`。

## Hithink 详细矩阵（TASK_001结论不变）

| Capability | Stock | Index | Sector | ETF | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Quote | DIRECT | DIRECT | DIRECT | DIRECT | DIRECT | 两只股票、8 个指数、3 个已解析板块、510300 ETF 均 `code=0`；核心字段完整 |
| Current price / change | DIRECT | DIRECT | DIRECT | DIRECT | DIRECT | `last_price`、`price_change`、`price_change_ratio_pct` 全部非空 |
| Volume / turnover | DIRECT | DIRECT | DIRECT | DIRECT | DIRECT | 快照 `volume`、`turnover` 全部非空；单位沿用源端契约 |
| Source timestamp | DIRECT | DIRECT | DIRECT | DIRECT | DIRECT | 四类快照均返回非空毫秒时间戳；样例时间约 2026-08-29 12:58–12:59 CST |
| Daily OHLCV | DIRECT | DIRECT | DIRECT | DIRECT | DIRECT | 股票、全部 8 个指数、3 个板块、ETF 历史日线成功；抽样 Normalized 各 246 行且必填字段零缺失 |
| Historical window | DIRECT | DIRECT | DIRECT | DIRECT | DIRECT | 600487 单次 10 年窗口成功，2405 行（2016-09-01 至 2026-08-28）；指数/板块/ETF 实测约 1 年 |
| Direct 15m Kline | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | 股票历史实际传 `interval=15m` 返回 `code=1002`；官方契约四类历史均只支持 `1d` |
| Direct 60m Kline | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | 股票历史实际传 `interval=60m` 返回 `code=1002`；官方契约四类历史均只支持 `1d` |
| 15m locally sampled bar | LOCAL_AGGREGATION | LOCAL_AGGREGATION | LOCAL_AGGREGATION | LOCAL_AGGREGATION | LOCAL_AGGREGATION | 快照已实测有价格、时间戳、累计量额；本 Task 未实现采集器，精确 OHLC 不保证 |
| 60m locally sampled bar | LOCAL_AGGREGATION | LOCAL_AGGREGATION | LOCAL_AGGREGATION | LOCAL_AGGREGATION | LOCAL_AGGREGATION | 同上；必须标记 `locally_aggregated`，停机缺口不得回填 |
| Constituents | UNSUPPORTED | DIRECT | DIRECT | UNKNOWN | DIRECT | 银行/通信设备/半导体成分端点实际成功；ETF 定期持仓本次未调用 |
| Auction | DIRECT | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | DIRECT | 两只股票 `stage=final` 返回 2 项，`auction_phase=closed`、`data_status=final` |
| ETF basic info | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | DIRECT | DIRECT | 510300.SH profile 成功，返回基金名称、成立日等原始资料 |
| Limit-up / limit-down / break pools | DIRECT | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | DIRECT | 最近交易日实际返回涨停 81、跌停 1、炸板 16 项；大列表仅保存第一页 |
| Anomaly | DIRECT | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | DIRECT | today-only 异动端点 `code=0`；验证日为非交易日，实际返回 0 项 |
| Dragon-tiger list | DIRECT | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | DIRECT | 最新交易日 2026-08-28 返回 51 只股票、57 条记录 |
| Dedicated capital flow | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | 官方 59 个公开端点中无独立全市场/个股资金流端点；龙虎榜金额不等价于通用资金流 |
| Direct market breadth | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | 无直接市场宽度端点；未来可由全市场数据派生，但不属于本 Task |

## 标的验证结果

### 股票

- `600487.SH` 亨通光电：快照、日线、10 年历史成功。
- `002463.SZ` 沪电股份：快照、日线成功。
- 无效代码 `999999.SH`：实际返回 `code=1002`，正确映射为 `INVALID_DATA`。
- 无效 API Key：实际映射为 `AUTH_ERROR`。

### 指数

以下代码均通过真实快照与日线验证：

- `000001.SH` 上证指数
- `000016.SH` 上证50
- `399300.SZ` 沪深300
- `000905.SH` 中证500
- `000902.SZ` 中证流通
- `399006.SZ` 创业板指
- `000852.SH` 中证1000
- `000688.SH` 科创50

`000902` 未出现在元信息搜索结果中；脚本对官方允许的 `.SH/.SZ` 做有限实际探针，仅 `000902.SZ` 成功，因此不是猜测后缀。

### 板块

- `BK0475 银行` → 官方目录唯一匹配 `881155.TI`：快照、历史、成分成功。
- `BK0448 通信设备` → `881129.TI`：快照、历史、成分成功。
- `BK1036 半导体` → `881121.TI`：快照、历史、成分成功。
- `600487 亨通光电`行业Benchmark → `881129.TI 通信设备`：TASK_011当前成分接口精确命中；Quote/Daily成功，15m/60m不支持。
- `002463 沪电股份`行业Benchmark → `884092.TI 印制电路板`：TASK_011当前成分接口精确命中；优先于上位`881270.TI 元件`，未使用半导体；Quote/Daily成功，15m/60m不支持。
- `BK0437 煤炭` → `UNKNOWN`：官方行业目录同时返回 `881105.TI 煤炭开采加工`、`884014.TI 煤炭开采`、`884281.TI 煤化工`，无法证明哪个与外部 `BK0437` 等价，因此未擅自选择。

### ETF

- `510300.SH` 沪深300ETF华泰柏瑞：基础资料、快照、日线全部成功。

## 15m / 60m 实时采样可行性

源端分钟 K 为 `UNSUPPORTED`；实时快照采样构造属于 `LOCAL_AGGREGATION`，并非 `DIRECT`。

- 采样频率：可从 5–10 秒开始做限流/延迟实验，但本次只做了 3 次、间隔 0.5 秒的有界稳定性调用，不能据此承诺全天频率。
- 价格：轮询 `last_price` 可构成采样 OHLC；任何有限频率均可能漏掉区间内真实高低点，不能冒充交易所分钟 K。
- 量额：本次确认快照有累计 `volume`、`turnover`。若 Phase 3 验证其交易日内单调性，可用相邻边界差分；遇到回退、跨日归零或缺口必须报错。
- 午休：11:30 封存上午区间，13:00 建立下午新段，不生成午休连续 bar。
- 交易日：官方日历实际返回 242 个近一年交易日，最新为 2026-08-28。
- 重启缺口：停机期间的价格路径无法从当前 API 回补；必须标记 `DATA_INCOMPLETE`，不得插值。

## Raw 与 Normalized 证据

- Raw：`data/samples/hithink/`
- Normalized：`data/samples/normalized/`
- 股票、指数、板块、ETF 日线 Normalized 均有同名 Raw 来源；各 246 行，时间范围 2025-08-25 至 2026-08-28，OHLCV 必填字段零缺失。
- Raw 保留完整 API 信封和原始字段；保存器只对潜在敏感键做递归脱敏。本次 Secret 扫描无命中。

## 官方证据

- <https://github.com/HiThink-Tech/Financial-API/blob/main/README.md>
- <https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-prices.md>
- <https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-index.md>
- <https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-fund.md>
- <https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-auction.md>
- <https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-special-data.md>
