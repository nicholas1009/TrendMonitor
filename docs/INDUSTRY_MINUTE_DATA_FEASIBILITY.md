# TASK_012｜行业Benchmark分钟数据方案与Provider可获得性验证 v0.1

研究截止时间：2026-08-30（Asia/Shanghai）。

## TASK_012

`PARTIAL`，最终判断为`BLOCKED_BY_PERMISSION`。

没有找到同时满足同花顺Taxonomy、相同Benchmark身份、历史分钟和盘中分钟的
`EXACT_PROVIDER_CANDIDATE`。Tushare公开契约提供申万历史分钟与申万实时截面，形成了值得继续
实调的方案B候选；但当前工作区没有`TUSHARE_TOKEN`或`TUSHARE_API_TOKEN`，因此没有安装SDK、
没有调用API，也没有把文档能力写成真实验证成功。

TASK_011保持`BLOCKED_BY_DATA`，本Task没有激活Proxy或修改任何Stock/Market规则。

## Canonical Industry Identity

Canonical身份保持冻结，分钟Proxy使用独立字段，不能回写Canonical字段。

| 股票 | Canonical Provider | Taxonomy | Benchmark | Mapping |
|---|---|---|---|---|
| 600487 亨通光电 | Hithink | THS | 881129.TI 通信设备 | EXACT / HIGH |
| 002463 沪电股份 | Hithink | THS | 884092.TI 印制电路板 | EXACT / HIGH |

TASK_011的真实Hithink证据继续复用：两个Benchmark的Quote和Daily为DIRECT，15m与60m均返回
业务码`1002`。没有重复消耗Hithink额度。

## Exact THS Minute Source

结论：`NOT_FOUND`。

- Hithink：当前真实接口只支持这两个行业的Quote/Daily；15m/60m不支持。
- Tushare：`ths_index`提供THS板块身份，`ths_daily`提供板块日线，`ths_member`仅说明“最新”
  板块成分；官方分钟端点`sw_mins`明确属于申万指数，不属于THS。
- Tushare通用`idx_mins`文档面向交易所指数，未正式声明支持`.TI`行业板块代码，不能用字段
  相似或猜码将其视为THS分钟源。
- JoinQuant、RQData、BigQuant的公开契约没有证明其分钟Benchmark与Hithink THS分类及成分
  完全一致，均为`NOT_EXACT`。

Tushare THS官方契约：[`ths_index`](https://tushare.pro/document/2?doc_id=259)、
[`ths_daily`](https://tushare.pro/document/2?doc_id=260)、
[`ths_member`](https://tushare.pro/document/2?doc_id=261)。

## Tushare SW Candidate

候选身份保持为：

```text
canonical_benchmark:
  provider: hithink
  taxonomy: THS

minute_proxy:
  provider: tushare
  taxonomy: SW2021
  mapping_type: CANDIDATE_PROXY
```

| 股票 | SW2021候选 | 层级 | 当前状态 |
|---|---|---|---|
| 600487 | 801102.SI 通信设备 | L2 | CANDIDATE_PROXY / DISABLED |
| 002463 | 850822.SI 印制电路板 | L3 | CANDIDATE_PROXY / DISABLED |

名称相同只构成调查入口，不能证明Taxonomy、成分或收益方向一致。

## Membership与历史成分

Tushare `index_member_all`的正式Schema包含L1/L2/L3代码与名称、`ts_code`、`in_date`、
`out_date`及`is_new`，从契约设计上支持历史成分变动研究；接口要求2000积分。
[官方接口文档](https://tushare.pro/document/2?doc_id=335)

当前状态为`BLOCKED_BY_TUSHARE_CREDENTIALS`，以下项目均未实调：

- 600487.SH是否属于801102.SI；
- 002463.SZ是否属于850822.SI；
- `in_date/out_date/is_new`真实覆盖长度与历史完整性；
- 目标日期as-of成分是否足以避免Survivorship Bias。

所以两个候选都不能升级到`PROXY_HIGH/MEDIUM/LOW`。

## Constituent Overlap

`NOT_COMPUTED`。

Hithink当前成分已存在（通信设备91项、印制电路板47项），但SW成分未取得，故没有伪造：

- intersection_count / union_count；
- jaccard_similarity / overlap_coefficient；
- hithink_only / sw_only名单。

## Daily Price Proxy Validation

`NOT_COMPUTED`。

Hithink两个Canonical Benchmark已有Daily DIRECT；Tushare `sw_daily`正式支持SW2021日线且文档
要求5000积分。[官方接口文档](https://tushare.pro/document/2?doc_id=327)

由于当前无凭证，没有取得120个共同交易日的SW Daily，也没有输出相关系数、方向一致率、
平均/中位绝对收益差、p95/max差异或Market ORANGE/RED日期方向一致率。缺失指标不会用名称
相同或行业常识替代。

## Historical Minute

Tushare `sw_mins`官方契约支持申万指数的1/5/15/30/60分钟，字段包含`trade_time`、OHLC、
amount和vol，单次上限按端点文档为5000条。[官方接口文档](https://tushare.pro/document/2?doc_id=469)

当前15m与60m均为：

```text
status = NOT_API_VERIFIED
reason = BLOCKED_BY_TUSHARE_CREDENTIALS
raw_evidence_saved = false
```

因此最近20交易日、午休/Closing结构、每日日内Bar数、Asia/Shanghai时间语义及SW分钟对SW
Daily的OHLC/Volume/Amount对账均未执行。即使未来字段齐全，Industry Context v0.1仍只允许
Close，不会因数据源新增而扩大Feature Contract。

## Realtime Capability

Tushare `rt_sw_k`的官方描述是“申万行业指数的最新截面数据”，字段为`trade_time`、现价、
昨收、日内OHLC、vol与amount。[官方接口文档](https://tushare.pro/document/2?doc_id=417)

因此它只能标记为：

```text
REALTIME_INDEX_SNAPSHOT
```

不能标记为`DIRECT_15M`或`DIRECT_60M_BAR`。当前API实调状态为
`BLOCKED_BY_TUSHARE_CREDENTIALS`。

## Boundary Snapshot Close Feasibility

候选方案仅用于未来研究：在10:30、11:30、14:00、15:00附近取得官方SW实时截面的Close，
保存为`BOUNDARY_SNAPSHOT_CLOSE`，而不是伪造OHLC或本地合成指数。

本Task已建立严格Schema，要求保存：requested boundary、provider trade_time、fetched_at、
close、provider、raw path与delay，并拒绝`DIRECT_60M_BAR`标签。

2026-08-30为周日，A股休市，且没有Tushare凭证，所以：

```text
LIVE_BOUNDARY_VALIDATION_PENDING
```

尚未回答的实证问题包括：

1. `trade_time`实际精度与延迟；
2. 四个边界附近能否稳定取得对应Close；
3. 延迟时能否区分当前分钟；
4. `rt_sw_k`是否存在可用回补路径（当前文档未说明）；
5. 程序离线是否永久丢失边界Close；
6. Snapshot Close与`sw_mins`完成周期Close能否逐周期一致。

离线丢数是主要风险。在没有正式回补证据前，该方案不能进入无人值守盘中监控。

## Provider Scorecard

| Provider | Taxonomy | Exact/Proxy | Hist 15m | Hist 60m | Live | Hist Members | Automation | Cost |
|---|---|---|---|---|---|---|---|---|
| Hithink | THS | EXACT identity | UNSUPPORTED | UNSUPPORTED | Quote snapshot | 未文档化历史变动 | REST可自动化 | 现有权限 |
| Tushare | THS + SW2021 | THS仅日线；SW Candidate Proxy | 文档支持/需权限 | 文档支持/需权限 | SW最新截面 | SW含in/out日期 | SDK/REST可自动化 | 积分+独立权限 |
| JoinQuant | Provider行业分类 | NOT_EXACT | 行业指数未确认 | 行业指数未确认 | 未确认 | `date`成分查询有文档 | 需账户 | 账户/询价 |
| RQData | 国家统计局 + MSCI | NOT_EXACT | 目标行业指数未文档化 | 同左 | 同左 | 自有Taxonomy | 需账户 | 询价 |
| BigQuant | SW2021 + 平台派生 | NOT_EXACT | 目标L2/L3未确认 | 同左 | 未确认 | SW字段有文档 | 需账户 | 套餐/询价 |

JoinQuant文档确认`get_industry_stocks(industry_code, date)`可按日期取行业成分，但行情Bar接口
面向证券/指数，未证明本Task两个行业Benchmark具有直接分钟Symbol：
[行业接口](https://www.joinquant.com/help/data/stock?f=home&m=footer)、
[Bar接口](https://www.joinquant.com/help/api/doc?id=9875&name=JQDatadoc)。

RQData公开文档说明行业分类采用国家统计局体系、Sector采用MSCI，不能视为THS Exact：
[行业分类](https://www.ricequant.com/doc/rqdata/python/stock-mod.html)、
[通用行情](https://www.ricequant.com/doc/rqdata/python/generic-api)。BigQuant可见SW2021行业字段，
但没有确认这两个L2/L3目标指数的历史与实时分钟：
[官方数据源说明](https://bigquant.com/data/datasources/cn_stock_factors_industry)。

## Cost / Permission

没有购买、订阅或开通任何权限。Tushare当前官方个人权限表显示：

| 能力 | 要求 | 当前文档价格 |
|---|---|---|
| `index_member_all` | 2000积分 | CNY 200/年积分档 |
| `sw_daily` | 5000积分 | CNY 500/年积分档 |
| 申万历史分钟 | 独立权限 | CNY 2000/年 |
| 申万实时行情 | 独立权限 | CNY 200/月 |

机构条款可能不同，最终应以购买时官方页面为准。
[Tushare权限表](https://tushare.pro/document/2?doc_id=290)

本地状态：`BLOCKED_BY_TUSHARE_CREDENTIALS`。凭证不得发送到Chat；未来只能由用户在本机
环境或`.env`中配置。验证脚本只判断凭证是否存在，不打印值，并对Provider错误进行Token
脱敏。

## Recommended Data Scheme

当前只推荐继续验证方案B，不批准启用：

```text
Historical Replay
  -> Tushare SW sw_mins DIRECT 15m/60m

Live Monitoring
  -> Tushare SW rt_sw_k
  -> BOUNDARY_SNAPSHOT_CLOSE

Identity
  -> Canonical: Hithink THS
  -> Minute Proxy: Tushare SW2021 CANDIDATE_PROXY
```

启用前必须完成五道Gate：目标股票归属与历史成员、当前成分重合、120日Daily Proxy指标、
20日分钟质量与Daily对账、真实交易日四边界捕获及历史/实时Close统一对账。未通过前不能称为
`PROXY_SCHEME_PROMISING`，也不能重新运行TASK_011行业共振Replay。

## Determinism / Safety

- 同一配置、凭证状态和`evaluated_at`重复生成完全相同机器结果：`PASS`。
- Canonical/Proxy身份分离、跨Taxonomy禁止EXACT：`PASS`。
- Credential redaction与结构化Permission错误：`PASS`。
- Boundary Snapshot时区、延迟、来源与类型约束：`PASS`。
- Synthetic Benchmark：`NOT CREATED / PASS`。
- `stock_60m_risk_v0.1`配置没有修改，机器报告保存其SHA-256：`PASS`。

机器结果：`data/reports/industry_minute_feasibility_latest.json`。

验证入口：

```bash
uv run python scripts/verify_industry_minute_feasibility.py
```

## Industry Context Readiness

`BLOCKED`。

当前证据不足以将TASK_011的`Industry Context Value = BLOCKED_BY_DATA`升级。已经找到正式的
SW历史分钟/实时截面契约，但Proxy质量和实际权限尚未验证。

## 下一阶段建议

只建议一个Task：

> TASK_013｜Tushare SW行业Proxy凭证化实调与边界Close交易日验证 v0.1

该Task在用户自行完成本地凭证与权限配置后，执行两股归属、历史成员、成分重合、120日Daily、
20日15m/60m、Daily Reconciliation和至少一个真实交易日四边界捕获；不得自动购买权限。

## 是否影响Master

`NO`。本Task只增加可行性证据、非生产Schema和离线验证，不改变Daily、趋势系统v0.3.1、
Market/Stock Risk规则或TASK_011状态。
