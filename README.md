# TrendMonitor Local

TrendMonitor Local 已完成 Phase 1 的同花顺 Financial-API 数据能力验证，并在 TASK_002 建立 provider-independent Instrument Registry、Raw 文件缓存、Source Trace 和显式 Provider fallback 基础。没有交易策略、交易指令、数据库服务、调度或通知。

## 当前状态

结论：`PARTIAL`。

- 最小 Provider、Raw/Normalized 分层、schema、validator、离线测试和真实验证入口已经建立。
- 有效凭据真实验证结果：`PASS 69 / FAIL 0 / UNSUPPORTED 2 / UNKNOWN 1`。
- 股票、8 个指数、3 个已解析板块、510300 ETF、竞价、交易日历和特色数据已真实取得。
- Hithink 的15m/60m实际请求返回 `code=1002`，但TASK_003已验证Longbridge可直接提供两个周期。
- 唯一 `UNKNOWN` 是外部 `BK0437 煤炭` 无法唯一映射到三个官方候选，未使用近似板块冒充。

TASK_002 结论：`SUCCESS`。

- `config/instruments.json` 登记 16 个正式对象；JSON 由标准库直接加载，无新增依赖，且便于人工审查。
- Hithink 已验证对象使用显式 mapping；`sector.coal -> 881105.TI` 仅为 `CANDIDATE_PROXY / LOW`。
- `data/raw/` 使用 Provider/数据类型/日期分层及唯一文件名，新请求不覆盖旧证据；`manifest.jsonl` 保存请求、数据范围和状态。
- Normalized record 包含 `provider / provider_symbol / raw_path / fetched_at` Source Trace。
- fallback 返回 `requested_provider / actual_provider / fallback_used / fallback_reason`，不会静默切源；所有源失败返回 `DATA_INCOMPLETE` 并保留原因。
- TASK_002 真实验证结果：`PASS 19 / FAIL 0`。

TASK_003 当前结论：`PARTIAL`。

- 已安装 Longbridge 官方 Python SDK `longbridge==4.5.0`，只使用 QuoteContext，不创建交易上下文。
- Longbridge Provider、Normalizer、15m/60m 周期接口、统一错误映射和双源比较器已实现。
- Longbridge认证成功；两只股票、两个指数、ETF、15m/60m与真实Fallback均完成调用。
- 15m/60m Raw能力均为 `DIRECT`；长窗口分钟数据存在少量09:30 OHLC关系异常，严格Validator不会自动修正。
- 两只股票64日日线OHLC完全一致；两个指数最大差异0.01点，保持 `REVIEW_REQUIRED`。

TASK_004 当前结论：`PARTIAL`。

- Longbridge历史分钟分窗真实扫描覆盖四标的91–96个完整交易日，未受普通接口1000根上限截断。
- 25个严格OHLC异常全部为指数09:30 opening-only异常；新增窄范围`SOURCE_BOUNDARY_QUIRK`，全局严格Validator未放宽，Source OHLC不修改。
- Derived System Bar稳定形成15m 16根/日、60m 4根/日；最后一根显式合并15:00 Closing Bucket并保存完整Lineage。
- 60日对账中Open/Close全部匹配Daily；股票High/Low高频不一致，两个指数各有3个严重量额异常日，因此暂不进入风险引擎。
- 完整口径和证据见 `docs/MINUTE_DATA_CONVENTION.md`。

TASK_005 结论：`SUCCESS`，Risk Engine Readiness为`YES_WITH_LIMITS`。

- 四标的各60个共同完整交易日完成1m/15m/60m/Daily五条逐字段比较链；完整统计保存于`data/reports/risk_input_quality_latest.json`。
- 五条链合计6,000个Close比较全部精确匹配；最后周期Closing Bucket合并后的Close为`TRUSTED_WITH_TRANSFORMATION`。
- Open、High/Low为`APPROXIMATE`；股票Volume/Turnover为`APPROXIMATE`；指数Volume为`BLOCKED`、Turnover为`ADVISORY_ONLY`。
- Safe Feature Contract只允许可信Close参与精确风险输入资格；High/Low精确结构、指数量能Feature关闭。
- 字段异常采用Feature级降级；核心Close/时间/周期/Lineage/Bar数异常仍返回`DATA_INCOMPLETE`。
- 正式日线系统仍只允许`DIRECT Daily`，分钟聚合Daily明确禁止替代。

TASK_006 结论：`SUCCESS`，Risk Engine Input Readiness为`YES_WITH_LIMITS`。

- 新增统一Risk Input Schema、Assembler、Preflight Gate和Append-only Snapshot；Risk Input层只通过`MarketDataService`取数。
- 600487、002463、中证500、科创50真实生成最新Daily/60m/15m输入：Daily均PASS，60m固定4根、15m固定16根，分钟层均`PASS_WITH_DEGRADATION`。
- Close类输入ENABLED；High/Low精确Feature及指数Volume禁用；股票量额和指数Turnover保留为DEGRADED。
- 盘中未完成周期显式排除并保留IN_PROGRESS来源时间，不冒充完整周期。
- Snapshot保存在`data/risk_inputs/`且不覆盖，可回读；详细结构见`docs/RISK_INPUT_ASSEMBLY.md`。

TASK_007 结论：`SUCCESS`，完整市场指数覆盖为`FULL_READY`。

- 六个新增指数均以真实`static_info`名称及Quote、Daily、15m、60m调用升级为`EXACT / HIGH / VERIFIED`。
- 最新完整交易日六指数均生成Daily、16根15m、4根60m Risk Input，Preflight均为`PASS_WITH_DEGRADATION`；完整8指数Bundle为8/8可消费。
- 30日质量扫描发现上证指数1日、创业板2日15m负Turnover，严格Validator保留`REVIEW_REQUIRED`；Raw与全局Contract未修改。
- 详细证据见`docs/MARKET_INDEX_COVERAGE.md`。

TASK_008 结论：`SUCCESS`，Risk Engine Readiness为`READY_WITH_DOCUMENTED_DEGRADATION`。

- 固定`market_60m_risk_v0.1`实现8指数、4分组的Close-only 60m风险评分、风险灯、
  风险方向、结构Flag、置信度、机器JSON及人类报告。
- 当前最新完整周期结果为`ORANGE / Score 5 / FLAT / HIGH`；8指数Breadth为0涨8跌。
- 使用60个完整交易日作为Shock基线，回放最近20个完整交易日共80个周期：
  `GREEN 43 / YELLOW 17 / ORANGE 19 / RED 1`。
- 相同输入确定性、look-ahead安全及Current管线/Replay末周期一致性全部通过。
- 中断前已有8指数历史Raw已全部复用；Current、Replay和manifest采用append-only正式存储。
- 详细规则、Resume审计和证据见`docs/MARKET_60M_RISK_ENGINE.md`。

TASK_009 结论：`SUCCESS`，15m Auxiliary Value为`PROMISING`。

- 新增`market_15m_internal_v0.1`，只消费TASK_006 `support_15m` Risk Input和可信Close；
  不建立独立15m风险灯或Risk Score。
- 当前完整周期市场状态为`WEAKNESS_BROADENING`，8指数均为`LATE_WEAKENING`；
  冻结60m结果保持`ORANGE / Score 5 / FLAT`。
- TASK_008最近20日80周期补算完成；弱势前兆对下一周期Score上升的联合命中率为58.06%，
  修复前兆对下一周期Score下降的联合命中率为43.48%，仅作后验观察。
- 2根/3根15m的`IN_PROGRESS`早期状态、append-only Snapshot、确定性、look-ahead和
  60m Score不可变均通过。
- 详细规则与证据见`docs/MARKET_15M_INTERNAL_STRUCTURE.md`。

TASK_010 结论：`SUCCESS`，Stock Intraday Risk Value为`PROMISING`。

- 新增`stock_60m_risk_v0.1`和`stock_15m_internal_v0.1`，只覆盖600487与002463，
  只消费Risk Input及冻结Market 60m/15m结果。
- 当前两股均为`YELLOW / Score 2 / FLAT / HIGH`；15m均为`HEALTHY_DOWN`，
  且在`WEAKNESS_BROADENING`市场中命中`JOINT_WEAKNESS`。
- 最近20个两股共同完整交易日完成2×80=160观察；确定性、look-ahead、
  Stock 15m不修改60m Score及Current/Replay一致性全部通过。
- 详细评分、历史分布、两个Closing Bucket缺失日降级与证据见
  `docs/STOCK_INTRADAY_RISK_ENGINE.md`。

TASK_011 结论：`PARTIAL`，Industry Context Value为`BLOCKED_BY_DATA`。

- 当前Hithink行业目录及成分接口确认600487映射`通信设备 / 881129.TI`、002463映射
  `印制电路板 / 884092.TI`，均为`EXACT / HIGH`；沪电没有使用半导体Benchmark。
- 两个行业Quote与Daily成功，但真实15m/60m请求均返回`code=1002`；Longbridge没有可验证
  行业Symbol，因此没有生成Industry Risk Input、历史Replay或Synthetic Benchmark。
- 2026-08-28当前两股Industry Context显式`UNAVAILABLE / NO_DIRECT_MINUTE_BENCHMARK`，
  TASK_010两股`YELLOW / Score 2`保持不变。
- 详细Mapping、能力证据、降级Schema及下一数据方案建议见`docs/STOCK_INDUSTRY_CONTEXT.md`。

## 环境

- Python 3.11+；本机检查值为 3.13.0。
- uv；本机检查值为 0.12.5。
- TASK_001/002 的 Hithink REST 路径不依赖第三方 Python 包；TASK_003 新增官方 `longbridge==4.5.0` SDK。
- 官方 CLI 要求 Node.js 22.12+。本机 Node.js 18.20.3 不兼容，因此本 Task 未升级 Node、未安装 CLI，采用 REST Provider。

## 官方资料与 Skill

- 官方仓库：<https://github.com/HiThink-Tech/Financial-API>
- 官方 API 文档：<https://fuyao.aicubes.cn/docs/>
- API Key 管理：<https://fuyao.aicubes.cn/admin/>
- 本次 Skill 安装路径：`~/.codex/skills/hithink-finance`
- 安装方法：官方推荐的仓库 Skill 目录通过 Codex `skill-installer` 安装，完整保留 `references/`。
- 安装来源 commit：`765513c2616030803ad80915ed65b205f425a942`（2026-08-27）。
- 校验：安装后的 `SKILL.md` 与该 commit 归档 SHA-256 一致；24 个 reference 文件可读。新安装 Skill 在下一次 Codex turn 进入动态发现列表。

官方面向一般 Agent 的推荐命令是：

```bash
npx skills add HiThink-Tech/Financial-API --skill hithink-finance -g --yes
```

官方 CLI 安装与调用方式（本机当前 Node 版本不满足要求，未执行）：

```bash
npm install -g @hithink-tech/hithink-finance-cli
hithink-finance auth login
hithink-finance capabilities --format json
```

Python 官方 toolkit 位于仓库 `python/` 子项目；本项目的 Phase 1 Provider 使用零第三方依赖 REST，以保持接入面最小。

## API Key

复制示例文件并用真实值填写本地 `.env`，或直接设置环境变量：

```bash
cp .env.example .env
```

```text
HITHINK_FINANCE_API_KEY=
```

`.env` 已被 `.gitignore` 排除。Provider 兼容旧的 `HITHINK_API_KEY`，但新配置使用官方统一名称。代码、测试、文档和日志都不会打印 Key。

## 执行

离线测试：

```bash
uv run python -m unittest discover -v
```

真实能力验证：

```bash
uv run python scripts/verify_hithink.py
```

Registry、Raw Cache、Source Trace 与真实 Hithink 验证：

```bash
uv run python scripts/verify_registry.py
```

Longbridge 真实能力验证：

```bash
uv run python scripts/verify_longbridge.py
```

Longbridge分钟Source/System Bar口径验证：

```bash
uv run python scripts/verify_minute_convention.py
```

分钟字段可信度与Safe Feature Contract真实验证：

```bash
uv run python scripts/verify_risk_input_quality.py
```

Risk Input Assembly与Preflight真实验证：

```bash
uv run python scripts/verify_risk_input.py
```

完整8指数Longbridge Mapping与Risk Input覆盖验证：

```bash
uv run python scripts/verify_market_index_coverage.py
```

8指数大盘60分钟风险引擎、当前结果与历史Replay验证：

```bash
uv run python scripts/verify_market_60m_risk.py
```

15分钟内部结构辅助、IN_PROGRESS视图及80周期关联验证：

```bash
uv run python scripts/verify_market_15m_internal.py
```

两只正式个股的60m风险、15m内部结构及2×80回放验证：

```bash
uv run python scripts/verify_stock_intraday_risk.py
```

两只正式个股行业Benchmark、分钟能力与Score不可变验证：

```bash
uv run python scripts/verify_stock_industry_context.py
```

行业Benchmark分钟数据Provider可获得性、Canonical/Proxy隔离与Boundary Snapshot方案验证：

```bash
uv run python scripts/verify_industry_minute_feasibility.py
```

无人值守60m统一Runner、历史no-network回放与Health Check：

```bash
uv run python scripts/run_intraday_monitor.py --dry-run
uv run python scripts/run_intraday_monitor.py --as-of 2026-08-28T15:03:00+08:00 --no-network
uv run python scripts/check_runtime_health.py
```

真实交易日launchd / Restart / Sleep-Wake生产验收（不触发Risk Run）：

```bash
uv run python scripts/verify_runtime_live_acceptance.py
```

TASK_013A当前状态及Operator验收顺序见
[`docs/RUNTIME_LIVE_ACCEPTANCE.md`](docs/RUNTIME_LIVE_ACCEPTANCE.md)。

用户级LaunchAgent管理：

```bash
uv run python scripts/manage_launchd.py --install
uv run python scripts/manage_launchd.py --status
uv run python scripts/manage_launchd.py --uninstall
```

缺少凭证时退出码为 2，并逐项输出 UNKNOWN/BLOCKED。需要在本地 `.env` 配置：

```text
LONGBRIDGE_APP_KEY=
LONGBRIDGE_APP_SECRET=
LONGBRIDGE_ACCESS_TOKEN=
```

这些是 Longbridge Legacy API Key 凭证。官方对新交互接入推荐 OAuth 2.0；本项目当前为了非交互本地验证只实现环境变量路径。

无 Key 时脚本明确返回 `BLOCKED_BY_API_KEY`（退出码 2），不会生成或伪造行情。配置 Key 后脚本会顺序验证指定股票、指数、板块、一个宽基 ETF、集合竞价、特色数据及 15m/60m 无效周期，并把少量脱敏 Raw 返回写入 `data/samples/hithink/`。

本次真实运行成功退出：

```text
PASS: 69
FAIL: 0
UNSUPPORTED: 2
UNKNOWN: 1
```

完整离线回归（TASK_006）：

```text
Ran 87 tests
OK
```

TASK_002 验证脚本通过内部 ID 取得两只股票快照、中证500与科创50快照/日线、通信设备快照；中证500和科创50各返回 32 条日线，并验证 8 份 Raw 缓存回读与 Source Trace：

```text
PASS: 19
FAIL: 0
```

## 已知限制

- 本次只证明短时、有界调用成功，不能代表多交易日或无人值守稳定性。
- 本次四类快照实际返回非空源时间戳；Normalizer 仍保留缺失检查，不会用本机时间伪造源时间。
- `BK0475`、`BK0448`、`BK1036` 已映射并验证；`BK0437` 有三个官方候选，保持 `UNKNOWN`。
- 本 Task 不实现分钟采集 Daemon，也不把轮询快照冒充源端分钟 K 线。
- Longbridge已完成认证真实调用；Eastmoney API尚未接入。
- `DATA_CONFLICT` 已有最小OHLC日线比较器；已有64日双源样本，但正式阈值仍未批准，不会自动选源。
- Longbridge分钟Raw为DIRECT；确认的09:30 opening-only异常返回`SOURCE_BOUNDARY_QUIRK`，其他OHLC异常仍为`INVALID_DATA`。
- 15:00 Closing Bucket已在Derived System Bar中显式合并；Raw不修改，详见 `docs/MINUTE_DATA_CONVENTION.md`。
- 股票分钟High/Low与Daily高频不一致，指数少数日期存在严重分钟/日线量额差异；当前只允许Close类风险输入资格，其他字段按`docs/RISK_INPUT_CONTRACT.md`降级或禁用。
- 8个正式指数均具备已验证Longbridge分钟Mapping和最新完整日Risk Input；指数Volume仍按Safe Contract禁用，且上证指数/创业板历史15m负Turnover日期保持`REVIEW_REQUIRED`。
- TASK_006只把数据交到Preflight Gate，没有实现风险评分、风险灯、策略或交易判断。
- TASK_008只实现8指数60m风险预警；High/Low、Index Volume、Turnover和15m均不参与正式评分，
  且结果不是Daily趋势、交易或仓位裁决。
- TASK_009的15m结果只解释60m内部结构，不修改冻结Risk Score；其后验前兆统计不能解释为
  独立风险灯、预测模型或交易信号。
- TASK_010只实现两只股票的盘中风险监控；不含行业板块、交易信号、自动调度或通知。
- TASK_011只增加行业Mapping和Auxiliary Context槽位；当前行业分钟数据不可用，不得用Daily、
  股票篮子、ETF或跨Provider猜码替代，也不修改任何冻结Score。
  002463历史上有两个15:00 Closing Bucket缺失日，回放按Contract排除而不补值。
- TASK_012只验证Provider与分钟数据方案。Canonical行业身份仍为Hithink THS；Tushare SW2021
  只保持未激活的`CANDIDATE_PROXY`。当前无Tushare凭证，成分重合、120日Daily相关性、20日
  分钟质量和实时边界Close均未实调，TASK_011仍为`BLOCKED_BY_DATA`。
- TASK_013建立launchd无人值守60m编排，不调用Codex CLI、不改变Risk规则、不接入Notification。
  当前历史Catch-up、周日Gate、实际LaunchAgent加载与Health Check通过；真实交易日上午/下午、
  Mac重启及Sleep-Wake验收仍PENDING，因此Readiness为`READY_WITH_LIMITS`。
