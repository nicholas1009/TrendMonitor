# Learning Review Policy v0.1

状态：`ACTIVE_POLICY / DESIGNED_NOT_SCHEDULED`

适用范围：TrendMonitor Local 风险监控、09:25 集合竞价信号研究与参数候选晋级

不适用范围：生产风险评分、生产阈值、正式交易参数、Runtime 调度和通知策略

## 1. 目的与边界

本政策建立长期样本积累、周期复盘、离线学习和参数晋级的统一协议。它不授权任何程序
自动修改生产参数，也不改变现有风险或 Auction 语义。

强制边界：

- 研究只能使用当时点可得数据生成信号；未来数据只能生成事后标签。
- Production 参数不得在线学习、自动漂移或自动晋级。
- 参数研究必须保留当前 Production 作为基线。
- 任何生产变更必须生成独立的 `PARAMETER_PROMOTION_REPORT` 并经人工确认。
- 在 TASK_013A LIVE_SCHEDULED 验收完成前，不得把周期复盘接入 Runtime 或 LaunchAgent。

## 2. 系统目标

### 2.1 风险监控

正式目标：`DRAWDOWN_AVOIDANCE_FIRST`。

系统优先减少重大回撤发生前完全没有预警的情况。允许少抓上涨、较早降低风险暴露和
一定程度的误报；但不得为了降低误报率而显著增加重大回撤漏报。

参数候选按以下顺序评价：

1. Major Drawdown Miss Rate
2. Major Drawdown Recall
3. Lead Time
4. MAE
5. Severe False Alarm
6. Normal False Alarm
7. 平均收益等辅助指标

平均收益或信号数量不能单独构成晋级依据。当候选参数减少重大下跌漏报、但平均收益较低
时，原则上优先保护回撤，前提是误报代价没有恶化到不可接受程度。

### 2.2 09:25 集合竞价信号

正式目标：`PRECISION_FIRST`。

正T、倒T、开盘卖出、开盘买入必须分别评估。系统明确允许 `ABSTAIN / NO_SIGNAL`；
NO_SIGNAL 是正常决策，不是预测失败。目标是发现高 Precision 的信号子集，而不是覆盖
所有交易日。

## 3. 研究标签与事实契约

### 3.1 MAJOR_DRAWDOWN_EVENT

`MAJOR_DRAWDOWN_EVENT` 是研究层标签，不在 v0.1 锁定唯一阈值。评估框架必须支持：

- 未来 1 个 60m 周期收益 `<= -X%`
- 未来 2 个 60m 周期 Maximum Adverse Excursion `<= -X%`
- 未来 1 个交易日 Daily drawdown `<= -X%`
- 未来 N 个周期从当前价格至局部低点的最大回撤 `<= -X%`

`X`、`N` 和价格基准均为 `TBD / RESEARCH_REQUIRED`，必须依据历史分布研究确定。研究
报告必须声明所用标签版本和参数，禁止在看过候选结果后隐式改标签。

### 3.2 每日事实样本

LEVEL 1 每个交易日积累以下 append-only 事实，不执行参数优化：

- Market：Auction、15m、60m、Daily、Risk Score、Risk Light、Components。
- Stock：Auction、15m、60m、Daily、Stock Risk、Market Context、Relative Strength；
  仅在 PT / Price Structure 已有可靠记录时纳入。
- Provenance：symbol、trading date、period end、as-of、execution mode、规则版本、数据源、
  质量状态和源输出标识。

信号事实和未来标签必须分层保存。任何事后生成的 Forward Path 不得回写或覆盖原始信号。

### 3.3 Forward Path 标签

标签成熟后至少记录后续 Open、High、Low、Close、MFE、MAE、最大回撤、最大上涨，以及
数据足以确认时的 High / Low 时间顺序。若时间顺序无法从直接分钟数据确认，必须标记
`UNAVAILABLE`，不得依据 Daily High / Low 猜测。

所有 Forward Path 仅用于事后评价；不得进入相同 as-of 的信号计算。

## 4. 风险系统正式评估指标

- **Major Drawdown Recall**：重大回撤事件中，事件前观察窗出现 ORANGE/RED 的比例；
  同时报告 YELLOW+ 捕获率。
- **Miss Rate**：重大回撤事件前完全没有 YELLOW/ORANGE/RED 预警的比例。
- **Lead Time**：首次有效风险信号领先事件的 15m 和 60m 周期数；报告分布，不只报均值。
- **MAE**：信号后指定窗口内的 Maximum Adverse Excursion。
- **MFE**：信号后指定窗口内的 Maximum Favorable Excursion，用于辅助判断是否过早报警。
- **False Alarm Rate**：ORANGE/RED 后未发生目标风险事件的比例。
- **Severe False Alarm**：误报同时伴随显著机会代价的子集；定义保持可参数化并版本化。

每项指标必须同时报告事件数和分母。样本不足时输出 `INSUFFICIENT_SAMPLE`，不得用百分比
掩盖小样本。不同 Major Drawdown 标签定义的结果必须分开呈现。

## 5. Auction 正式评估指标

每种信号独立报告：

- Signal Count、NO_SIGNAL Count、Signal Precision
- MFE、MAE
- 平均正确收益空间、平均错误损失空间
- 最大错误损失、盈亏比、尾部损失

正T样本记录 Auction Price、Open、High、Low、Close、卖出后最低价及时间、理论最大回补
空间、可执行回补空间和错误方向 MAE。倒T对称记录买入后最高价及时间、理论最大卖出空间、
可执行卖出空间和错误方向 MAE。开盘买入和开盘卖出使用各自独立的成功标签及代价指标。

Daily High / Low 只能产生理论上界，不能冒充可执行结果。框架必须保留
`EXECUTABLE_WINDOW` 接口；窗口定义当前为 `TBD / RESEARCH_REQUIRED`，确定后必须版本化。

高胜率不能抵消不可接受的尾部错误损失。Precision 优先于 Coverage，但任何候选仍必须
同时通过错误代价和尾部损失检查。

## 6. 三层复盘机制

### LEVEL 1｜Daily Sample Accumulation

每个交易日积累事实和成熟的事后标签；不搜索参数、不修改规则。

### LEVEL 2｜Periodic Review

基线频率为每 20 个 A 股交易日一次 `SYSTEM_REVIEW`。当前状态为
`DESIGNED_NOT_SCHEDULED`，只允许人工触发的离线复盘。输出结论只能为：

- `KEEP`：当前参数仍在稳定区间，未发现有实际意义的退化。
- `STUDY`：证据提示需要进一步研究，但尚未形成候选参数。
- `CANDIDATE`：满足候选形成条件，可进入正式历史研究。

`KEEP_CURRENT_PARAMETERS` 是成功复盘结果；复盘不要求产生参数变化。

SYSTEM_REVIEW 至少包含：

- Market Risk：重大回撤事件、Miss Rate、Recall、Lead Time、False Alarm、MAE/MFE。
- Stock Risk：分别报告 600487.SH、002463.SZ 的 Risk Light 分布、大回撤捕获、独立弱势、
  Market Resonance 和 PT 未确认样本。
- Auction：分别报告两只股票各类信号的 Signal/NO_SIGNAL 数、Precision、MAE/MFE、
  平均及最大错误损失。

### LEVEL 3｜Parameter Promotion

参数候选必须依次经过历史 Calibration/Validation、Walk-forward 和真实未来行情 Shadow。
Shadow 不影响正式信号。通过全部门槛后只能进入 `PROMOTION_READY`，不能自动进入
Production。

## 7. 参数状态机

正式学习状态及允许迁移如下：

1. `BASELINE`：尚未完成充分验证的初始值。
2. `VALIDATED`：现有参数已按既定研究协议验证；不等同于 Production 授权。
3. `STUDY`：存在跨样本证据，正在进行受控研究。
4. `CANDIDATE`：候选参数或稳定区间已形成，等待严格验证。
5. `SHADOW`：候选随真实未来行情运行，但不影响正式输出。
6. `PROMOTION_READY`：全部晋级门槛通过，等待人工决策。
7. `PRODUCTION`：经人工确认后采用的正式参数。
8. `REJECTED`：候选未通过门槛；保留失败证据，不能静默重开。

正常路径为：

`BASELINE → VALIDATED → STUDY → CANDIDATE → SHADOW → PROMOTION_READY → PRODUCTION`

任一研究状态均可进入 `REJECTED`。回到 STUDY 必须记录新证据和新的研究版本。状态迁移
只追加记录，不覆盖旧结论。

## 8. 候选形成与晋级门槛

只有以下至少一项出现跨样本证据时，才可建立 `PARAMETER_STUDY_CANDIDATE`：

- 重大回撤漏报明显增加
- Auction Precision 明显下降
- 参数稳定区间发生可重复漂移
- Validation 明显劣化
- 新样本呈现稳定的结构差异

最近 3～5 个样本表现不佳不能单独触发调参。

候选晋级必须同时满足：

1. Calibration 改善。
2. 按时间顺序切分的 Validation 改善。
3. Walk-forward 不明显退化，且优势跨时期存在。
4. Shadow 不明显退化并积累足够前向样本。
5. 不明显增加重大回撤漏报。
6. 不明显增加尾部错误损失。
7. 改善具有实际意义，而非仅有统计或小数精度优势。

具体“明显”和“足够”门槛在对应研究开始前预注册；v0.1 不硬编码未经验证的数值。

达到 `PROMOTION_READY` 后必须生成 `PARAMETER_PROMOTION_REPORT`，至少包含旧参数、新参数、
Calibration、Validation、Walk-forward、Shadow、重大回撤风险、尾部损失及潜在副作用。
最终 Production 变更必须由人工明确批准。

## 9. 防过拟合与防漂移

- 时序研究只允许按时间顺序 Calibration → Validation；禁止随机 Train/Test Split。
- 正式候选必须进行 Walk-forward；不能只依赖单次切分。
- 每次研究记录参数组合总数、网格、标签定义和研究版本。
- 扫描组合越多，晋级证据门槛必须越高。
- 优先使用粗网格和稳定区间：`STABLE REGION > SINGLE OPTIMUM`。
- 禁止为了回测结果持续增加参数或搜索小数点后二位的偶然最优值。
- 禁止每次复盘自动调阈值、在线梯度更新 Production 或只看近期滚动优化。
- 股票参数允许独立学习，不假设所有股票共享最优值；2.0 可以保留为通用 baseline。

现有证据：002463.SZ 的 natural_move_k 稳定区间为 1.75～2.50；600487.SH 为
1.75～2.25。该差异不授权修改任何正式交易规则。

## 10. 当前研究候选登记

### STOCK_PT_CONFIRMATION_CANDIDATE

- 状态：`STUDY_CANDIDATE`（候选登记状态；尚未进入参数状态机的 STUDY 执行阶段）
- 来源：2026-09-03 Cross Validation
- 假设：当 Stock Risk = GREEN、但 PT 止跌结构尚未确认时，未来 1～2 个 60m 周期风险
  重新升高的概率是否高于普通 GREEN 样本。
- 本政策动作：仅登记，不执行研究。

### RISK_CONTINUITY_CANDIDATE

- 状态：`CLOSED_INCONCLUSIVE`
- 结论：既有历史研究没有形成足够跨期证据。
- 重开条件：新增样本使有效事件数和跨时期证据达到预注册研究要求。
- 禁止动作：不得因单日 GREEN 与外部 YELLOW 的差异直接加入 hysteresis 或 residual score。

## 11. 版本、自动化与审批

- 政策版本：`Learning Review Policy v0.1`。
- Review cadence：20 个 A 股交易日。
- Parameter auto-promotion：`false`。
- Production change requires human approval：`true`。
- Runtime integration：`DESIGNED_NOT_SCHEDULED`。

目标函数、复盘频率、晋级门槛或参数状态定义的任何变化必须发布新版本，不能隐式覆盖
v0.1。只有 TASK_013A LIVE_SCHEDULED 验收完成后，才讨论将 20 交易日复盘接入无人值守
Runtime；接入本身需要独立任务、测试和人工批准。
