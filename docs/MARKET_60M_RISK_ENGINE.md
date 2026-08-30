# TASK_008｜大盘60分钟风险引擎 v0.1

> TASK_008 resumed after quota interruption and machine restart.

## 结论

`TASK_008 SUCCESS`。8指数60分钟核心雷达已按固定规则版本
`market_60m_risk_v0.1` 生成机器结果、人类报告和80周期历史回放。当前结果、
历史回放、确定性、look-ahead检查及当前管线/回放一致性均通过。

本引擎只负责60分钟风险预警，不修改Daily正式趋势系统v0.3.1，不产生交易、
仓位、通知或调度动作。

## Resume恢复记录

### 中断前已有内容

- `config/market_60m_risk_rules.json`：固定规则、8指数和4组配置。
- `schemas/market_risk.py`：机器结果、指数状态、组状态和风险枚举。
- `market_risk/engine.py`：Close-only Feature、评分、风险灯、结构Flag与置信度。
- `market_risk/replay.py`：历史Risk Input切片、60日Shock基线、20日回放和look-ahead检查。
- `market_risk/store.py`、`report.py`：append-only结果存储和确定性人类报告。
- `tests/test_market_60m_risk_engine.py` 与 `scripts/verify_market_60m_risk.py`。
- 8指数约130个自然日窗口的Longbridge 60m Raw历史数据已经下载完成。

### 本次恢复后完成内容

- 修复Longbridge指数分钟归一化中断残留：负Volume/Turnover的已批准字段级降级分支
  改为读取实际 `instrument.asset_type`，恢复原有严格验证边界。
- 验证脚本先读取、重新验证并复用8/8份append-only Raw；只有缺失标的才允许请求Provider。
- 增加缓存断点复用和异常标的拒绝测试。
- 补齐append-only Replay Snapshot及manifest记录；`latest`报告仅作为便利投影。
- 生成当前风险结果、80周期回放、四种风险灯样本审计和本正式文档。

## 固定输入与边界

- 正式对象：上证指数、上证50、沪深300、中证500、中证流通、创业板指、
  中证1000、科创50。
- 分组：`LARGE_CAP / BROAD_MARKET / MID_SMALL / GROWTH`，每组2个指数。
- 正式输入：TASK_006/007 Preflight允许消费的60m System Bar。
- 正式评分字段：`close`，以及由Close确定性派生的方向、连续走弱、修复和收益率。
- 不参与评分：Open、High、Low、Volume、Turnover。
- Index Volume保持`BLOCKED`；Index Turnover保持`ADVISORY_ONLY`；High/Low exact trigger保持关闭。
- 15m不进入v0.1独立评分；Daily正式层仍只允许DIRECT Daily。

## 规则 v0.1

### 评分

- Breadth：下跌0–2个=0分，3–4个=1分，5–6个=2分，7–8个=3分。
- Persistent Weakness：连续两个60m Close下降；0–2个=0分，3–4个=1分，5–8个=2分。
- Downside Shock：使用当前时点之前60个完整交易日的60m绝对收益p95；
  0个=0分，1个=1分，2–8个=2分。
- Weighted Support Distortion：两只大型权重指数均上涨、其余指数至少4只下跌时加1分。
- Broad Repair：至少5只指数出现Close修复时抵扣1分，最终Score下限为0。
- Risk Light：0–1 `GREEN`，2–3 `YELLOW`，4–5 `ORANGE`，6–8 `RED`。

### 结构Flag

- `SMALL_CAP_STRESS`：中小盘组两只均下跌，且成长组至少1只下跌。
- `BROAD_SELLOFF_RESONANCE`：至少6只指数下跌。
- `STRONG_BROAD_WEAKNESS`：至少7只指数下跌且至少5只连续走弱。
- `STYLE_DIVERGENCE_STRONG`：Weighted Support Distortion与Small-Cap Stress同时成立。
- `BROAD_REPAIR`：至少5只指数出现Close层修复。

### Confidence

- 8/8有效：`HIGH`。
- 至少6只有效且4组均有代表：`MEDIUM`。
- 其余：`LOW / DATA_INCOMPLETE`，不输出风险灯。

## Current Risk Result

- 完整周期：`2026-08-28T15:00:00+08:00`
- Risk Score：`5`
- Risk Light：`ORANGE`
- Risk Direction：`FLAT`
- Confidence：`HIGH`
- Breadth：上涨0 / 下跌8 / 持平0
- Persistent Weakness：8/8
- Downside Shock：0
- Score Components：Breadth 3 + Persistent 2 + Shock 0 + Weighted Distortion 0 − Repair 0

### Group States

| Group | Direction | Advancers | Decliners | Median 60m return |
|---|---:|---:|---:|---:|
| LARGE_CAP | ↓ | 0 | 2 | -0.125876% |
| BROAD_MARKET | ↓ | 0 | 2 | -0.193117% |
| MID_SMALL | ↓ | 0 | 2 | -0.247321% |
| GROWTH | ↓ | 0 | 2 | -0.459962% |

### Structural Flags

- `SMALL_CAP_STRESS = true`
- `BROAD_SELLOFF_RESONANCE = true`
- `STRONG_BROAD_WEAKNESS = true`
- `WEIGHTED_SUPPORT_DISTORTION = false`
- `STYLE_DIVERGENCE_STRONG = false`
- `BROAD_REPAIR = false`

## Historical Replay

- Shock warm-up基线：此前60个完整交易日。
- 正式回放：最近20个完整交易日，共80个完整60m周期。
- 回放范围：`2026-08-03T10:30:00+08:00` 至 `2026-08-28T15:00:00+08:00`。
- `GREEN 43 / YELLOW 17 / ORANGE 19 / RED 1`。
- `risk_up 32 / risk_down 23 / flat 25`，分别对应机器枚举
  `RISING / FALLING / FLAT`。
- `weighted_support_distortion 1`。
- `broad_selloff_resonance 32`。

规则和阈值没有根据分布或样本数量调整。

## Sample Audit

| Light | Sample period | Score | Main components | Breadth |
|---|---|---:|---|---|
| GREEN | 2026-08-03 11:30 | 0 | Breadth 0, Persistent 0, Shock 0, Repair offset 1 | 7↑ / 1↓ |
| YELLOW | 2026-08-03 14:00 | 3 | Breadth 3 | 0↑ / 8↓ |
| ORANGE | 2026-08-06 14:00 | 4 | Breadth 2 + Persistent 2 | 2↑ / 6↓ |
| RED | 2026-08-03 10:30 | 7 | Breadth 3 + Persistent 2 + Shock 2 | 0↑ / 8↓ |

完整审计在Replay JSON中；GREEN/YELLOW/ORANGE各保留3例，RED按真实分布保留1例。

## Data Degradation

- 8/8当前输入均为`PASS_WITH_DEGRADATION`，核心Close均可信；15:00 Closing Bucket为
  `TRUSTED_WITH_TRANSFORMATION`且保留完整Lineage。
- High/Low为`APPROXIMATE`，不参与精确触发。
- Index Volume为`BLOCKED`，Turnover为`ADVISORY_ONLY`，两者均不参与评分。
- 因8只指数Close、周期、Preflight和Provenance全部有效，Signal Confidence仍为`HIGH`。

## Determinism

- 相同Current Risk Input连续执行两次，`to_dict()`完全一致。
- 相同历史周期Replay连续计算结果完全一致。
- append-only存储中的同源重复Current机器结果逐字节一致；统一回归刷新上游Snapshot后，
  业务判断字段仍保持一致且Provenance显式更新。
- Current管线与Replay最后周期的周期、Score、Light和8指数Close一致。

## Lookahead Safety

- Historical builder按每个 `as_of` 切片Source Bar，再构造System Bar。
- 每个Risk Input中的所有System Bar结束时间均不晚于该周期 `as_of`。
- 引擎拒绝任何结束时间不早于当前目标周期的历史Risk Input。
- Shock p95只使用当前周期之前的数据；本次80/80周期检查通过。

## 产物

- Current机器结果：`data/risk_outputs/market_60m/json/`
- Current人类报告：`data/risk_outputs/market_60m/markdown/`
- append-only Replay：`data/risk_outputs/market_60m/replay/`
- append-only manifest：`data/risk_outputs/market_60m/manifest.jsonl`
- Replay便利投影：`data/reports/market_60m_replay_latest.json`
- 固定规则：`config/market_60m_risk_rules.json`

## Tests

- `uv run python -m unittest discover -v`：113/113通过。
- `verify_registry.py`：19 PASS / 0 FAIL。
- `verify_hithink.py`：69 PASS / 0 FAIL / 2 UNSUPPORTED / 1 UNKNOWN，均为既有正式结论。
- `verify_longbridge.py`：连接、Quote、Daily、15m DIRECT、60m DIRECT及Fallback通过；
  Cross Provider指数仍为既有`REVIEW_REQUIRED`。
- `verify_risk_input_quality.py`：Close 0 mismatch，`YES_WITH_LIMITS`；既有分钟与Daily
  `SEMANTIC_DIFFERENCE`保持不变。
- `verify_risk_input.py`：Daily PASS，60m/15m DEGRADED，Contract、Provenance与Snapshot Replay通过。
- `verify_market_index_coverage.py`：8/8 `FULL_READY`。
- `verify_market_60m_risk.py`：Current、80周期Replay、Determinism、Lookahead、Pipeline Match通过，
  并显示`HISTORICAL RAW CACHE REUSED 8/8`。

## Risk Engine Readiness

`READY_WITH_DOCUMENTED_DEGRADATION`。

v0.1已满足8指数60m风险预警用途。它不能替代Daily正式趋势裁决，不能扩展解释为
个股、行业、ETF、仓位或交易信号。

## 下一阶段建议

TASK_009如需推进，应保持本任务规则冻结，把本引擎的机器结果作为显式、只读的上游输入；
调度、通知、个股风险和交易能力仍需各自独立Task授权。

## 是否影响Master

`NO`。本任务落实Master中“60分钟负责风险预警”的既有边界，没有修改正式趋势系统v0.3.1。
