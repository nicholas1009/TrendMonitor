# TASK_011｜两只正式个股行业Benchmark与60m共振验证 v0.1

## TASK_011

`PARTIAL`。

两只股票的Canonical Industry Benchmark、Provider行业目录身份与成分关系已由当前真实接口
确认；但Hithink两个行业Benchmark的15m和60m请求均返回业务码`1002`，Longbridge没有可验证
的行业Taxonomy或正式Symbol。按Task约束没有构造Synthetic Benchmark，因此当前行业分钟
Context、Historical Replay和三层共振研究均为`BLOCKED_BY_DATA`。

## 定位与边界

- Industry只属于Stock Intraday Risk的Auxiliary Context。
- `stock_industry_context_v0.1`不持有或修改Stock/Market Risk Score。
- 可执行逻辑只允许Close及其确定性派生值；Open/High/Low/Volume/Turnover不参与Flag。
- 不创建Synthetic行业指数、成分股篮子、ETF代理、行业15m风险灯、交易建议、调度或通知。

## 600487 Industry Benchmark

正式Benchmark：Hithink `通信设备 / 881129.TI`。

- Taxonomy：Hithink `tag=industry`行业目录。
- Mapping：`EXACT / HIGH`。
- 当前目录只有一个同代码同名项。
- 当前成分接口返回91项，其中精确包含`600487.SH / 亨通光电`。
- Quote与Daily均`code=0`。
- Longbridge：`UNMAPPED / LOW`，未猜测或跨Provider复用`881129.TI`。

## 002463 Industry Benchmark

正式Benchmark：Hithink `印制电路板 / 884092.TI`。

- Taxonomy：Hithink `tag=industry`行业目录。
- Mapping：`EXACT / HIGH`。
- 当前目录只有一个同代码同名项。
- 当前成分接口返回47项，其中精确包含`002463.SZ / 沪电股份`。
- 同一Provider上位`元件 / 881270.TI`也包含沪电，但PCB分类更精确，故不采用宽Proxy。
- 没有使用`半导体`。沪电正式披露持续将主营描述为印制电路板业务，当前Provider成分证据与
  该业务身份一致。
- Quote与Daily均`code=0`。
- Longbridge：`UNMAPPED / LOW`，未猜测行业Symbol。

## Mapping Evidence

验证时间：`2026-08-30T09:25:54.252861+00:00`。

| 股票 | 行业 | Hithink ID | Mapping | 成分证据 | 成分数 |
|---|---|---|---|---|---:|
| 600487 | 通信设备 | 881129.TI | EXACT / HIGH | 600487.SH精确命中 | 91 |
| 002463 | 印制电路板 | 884092.TI | EXACT / HIGH | 002463.SZ精确命中 | 47 |

原始业务信封的脱敏证据、Request ID、Quote/Daily样例和分钟失败码保存在：

- `data/reports/stock_industry_benchmark_evidence_latest.json`
- `data/risk_outputs/stock_industry_context/evidence/`

跨Provider不强行等价。Longbridge官方CN Quote Coverage覆盖证券与指数，但没有找到行业
分类目录或行业Symbol发现端点；在没有正式候选Symbol时没有发送猜码请求。

## Minute Capability

| Benchmark | Quote | Daily | DIRECT 15m | DIRECT 60m |
|---|---|---|---|---|
| 通信设备 881129.TI | PASS | PASS | UNSUPPORTED / code 1002 | UNSUPPORTED / code 1002 |
| 印制电路板 884092.TI | PASS | PASS | UNSUPPORTED / code 1002 | UNSUPPORTED / code 1002 |

Hithink当前正式历史契约只文档化`interval=1d`，本Task仍按要求对两个真实行业ID做了最小
15m/60m实调；结果与契约一致。Daily成功不等价于分钟可用。

## Current Industry Context

目标周期：`2026-08-28T15:00:00+08:00`。

| 股票 | TASK_010冻结结果 | Stock Return | Market Return | Industry Context |
|---|---|---:|---:|---|
| 600487 | YELLOW / Score 2 | -1.0942% | -0.2153% | UNAVAILABLE |
| 002463 | YELLOW / Score 2 | -0.4987% | -0.2153% | UNAVAILABLE |

两个结果都关联已有Stock 60m Result、Market 60m Result与本次Benchmark Evidence；
`industry_risk_input=null`，原因是`NO_DIRECT_MINUTE_BENCHMARK`。没有用Daily数据冒充
2026-08-28最后60m周期收益。

## Historical Replay

`BLOCKED_BY_DATA / 0 OBSERVATIONS`。

两个Benchmark都没有DIRECT 60m，故不能执行约160个Stock Observations，也不能建立
严格as-of的`stock_return - industry_return`历史p10。没有重算或修改TASK_010的160个结果。

## Triple Resonance

`UNAVAILABLE`。

纯函数规则与测试覆盖了Stock/Industry Weak Resonance、Triple Weak Resonance、Industry
Persistent Weakness及五类Context Classification；但没有真实行业60m输入时，这些Flag在
正式当前结果中保持`null`，不以股票或市场下跌代替行业下跌证据。

## Independent Weakness Decomposition

`UNAVAILABLE`。

`MARKET_INDEPENDENT_ONLY`与`INDUSTRY_AND_MARKET_INDEPENDENT`的确定性拆分已实现并通过
测试；正式历史分解需要行业相对收益p10，当前没有数据，因此不输出命中数。

## 15m Industry Auxiliary

`NOT_IMPLEMENTED_BY_CAPABILITY_GATE`。

两个行业15m都不是可信DIRECT数据，所以没有启用`industry_15m_internal_v0.1`、没有计算
行业four-Close结构，也没有生成Joint 15m Flag。该结果遵守“只有真实DIRECT 15m才实现”的
硬门槛。

## Data Quality

- 行业身份：`VERIFIED`；Quote/Daily：`DIRECT`。
- 15m/60m：`UNSUPPORTED`；正式Industry Risk Input：未生成。
- 当前结果逐Feature记录`DISABLED / NO_DIRECT_MINUTE_BENCHMARK`、受影响字段、Evidence源。
- Synthetic Benchmark：`NOT CREATED`。
- 未使用行业Volume、Turnover或High/Low进行任何正式判断。

## Determinism

同一Stock Result、空Industry Risk Input、Benchmark Evidence重复执行得到完全相同业务JSON：
`PASS`。规则版本固定为`stock_industry_context_v0.1`。

## Lookahead

当前降级结果不消费行业行情历史，周期对齐标记为`NOT_APPLICABLE`且不引入未来数据：
`PASS`。已实现的可用路径会拒绝当前或未来Reference，并要求Stock与Industry同一60m结束点。

## Stock Score Immutability

`PASS`。TASK_010当前两股在加入Industry Context前后均为`YELLOW / Score 2`；新Schema只复制
冻结分数用于显示，没有评分入口。Market与Stock v0.1配置均未修改。

## Snapshot与验证

- 当前JSON与Human Report：`data/risk_outputs/stock_industry_context/`
- append-only manifest：`data/risk_outputs/stock_industry_context/manifest.jsonl`
- 最新机器报告：`data/reports/stock_industry_context_latest.json`
- 最新Benchmark证据：`data/reports/stock_industry_benchmark_evidence_latest.json`
- 验证入口：`uv run python scripts/verify_stock_industry_context.py`
- 重新做有界真实能力探针：增加`--refresh-evidence`；普通验证复用最新Evidence，避免反复消耗API。

## Tests

TASK_011定向测试覆盖：EXACT、PROXY、UNMAPPED、无分钟Benchmark、行业持续弱势、两层/三层
共振、相对行业p10、逆行业偏强、行业逆市场偏强、独立弱势拆分、数据降级、不评分字段、
确定性、look-ahead、append-only与Stock Score不可变。

行业15m Joint Weakness测试没有伪造为正式可用：能力门槛测试确认无DIRECT 15m时字段为空且
规则不启用。

- `uv run python -m unittest discover -v`：167/167通过；TASK_011定向14项通过。
- `uv run python scripts/verify_registry.py`：PASS 20 / FAIL 0，16个正式对象全部加载。
- `uv run python scripts/verify_stock_intraday_risk.py`：160 Observations、冻结两股Score、
  Determinism、Lookahead与Score Immutability全部PASS，且只复用现有Raw Cache。
- `uv run python scripts/verify_stock_industry_context.py`：Mapping、Determinism、Lookahead、
  Stock Score Immutability与禁止Synthetic全部PASS；Historical Replay按真实能力明确BLOCKED。

## Completion判断

1. 亨通正式行业Benchmark：`通信设备 / 881129.TI`。
2. 沪电正式行业Benchmark：`印制电路板 / 884092.TI`，不是半导体。
3. 两者Mapping：均`EXACT / HIGH`。
4. 可信DIRECT 15m：`NO`。
5. 可信DIRECT 60m：`NO`。
6. 行业Context稳定生成：`NO / BLOCKED_BY_DATA`；显式降级结果可稳定生成。
7. 三层共振可识别：逻辑与测试`YES`，真实当前/历史`NO_DATA`。
8. 行业性与个股独立弱势可进一步分辨：逻辑与测试`YES`，真实统计`NO_DATA`。
9. 行业15m增加解释价值：`NOT_EVALUABLE`。
10. 是否保留正式盘中Industry Context：目前仅保留Schema、Mapping和降级槽位，不激活行情层。

## Industry Context Value

`BLOCKED_BY_DATA`。

行业身份本身高置信，但目标是验证分钟共振的额外解释价值；缺少DIRECT行业15m/60m使该目标
无法用真实样本完成，不能据纯函数测试评为PROMISING或NEUTRAL。

## 下一阶段建议

只建议一个Task：

> TASK_012｜行业Benchmark分钟数据方案与Provider可获得性验证 v0.1

先调查具有正式Taxonomy、历史成分和DIRECT分钟数据的合法来源；如果只能构造Synthetic
Benchmark，必须在该独立Task中定义成分、权重、历史成员、缺失处理与Survivorship Bias，
不得直接进入自动调度。

## 是否影响Master

`NO`。TASK_011没有修改Daily、趋势系统v0.3.1或任何冻结的Market/Stock规则；结果只增加
可审计的行业Mapping与数据不可用事实。

## 外部契约证据

- Hithink Financial API行业/指数端点：<https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-index.md>
- Longbridge OpenAPI Quote Coverage：<https://open.longbridge.com/docs>
- 沪电股份2024年度报告（深交所披露）：<https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-11-28/22941b9e-5afb-4d32-8d63-dbf686f6ef2f.PDF>
