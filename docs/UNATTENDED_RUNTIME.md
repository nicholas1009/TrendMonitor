# TASK_013｜TrendMonitor Local 无人值守自动调度 v0.1

实施日期：2026-08-30（周日）。业务时区固定为`Asia/Shanghai`。

## TASK_013

`PARTIAL`：实现状态为`IMPLEMENTED_PENDING_LIVE_SCHEDULE_VALIDATION`。

生产Runner、Calendar Gate、Process Lock、Retry、Catch-up、Idempotency、Runtime Manifest、
Combined Report、Health Check和用户级LaunchAgent均已实现并完成离线/历史/非交易日验证。
由于实施日是周日，无法伪造“真实交易日上午和下午由launchd自动触发”的证据，也没有重启
Mac进行登录后恢复验收。

## Production Runner

统一入口：

```bash
uv run python scripts/run_intraday_monitor.py
```

参数：

```text
--as-of ISO_TIMESTAMP
--dry-run
--no-network
--force
```

Runner不调用Codex CLI。联网模式顺序复用现有确定性Python入口：Market Data/Risk Input、
Market 60m、Market 15m、两股60m与15m。Provider、Fallback、Preflight与Risk规则仍由原模块
决定，Runtime层只编排和记录。

`--dry-run`只检查配置、冻结规则Hash、Credential存在性、`.env`权限、Calendar、合法周期和
launchd路径，不建立日志或生产Snapshot。`--no-network`只读取已有Replay/Snapshot。

## Schedule

正式周期和默认触发缓冲：

| 周期 | 目标结束 | 默认执行 |
|---|---:|---:|
| 09:30–10:30 | 10:30 | 10:33 |
| 10:30–11:30 | 11:30 | 11:33 |
| 13:00–14:00 | 14:00 | 14:03 |
| 14:00–15:00 | 15:00 | 15:03 |

全部来自`config/runtime_schedule.json`，缓冲为3分钟。午休不会产生额外60m周期，第一版没有
09:45、10:00等15分钟自动任务。

launchd使用`StartInterval=60`唤醒轻量Runner，再由Runner用`Asia/Shanghai`解析边界。这样
不会把Mac的Asia/Tokyo时钟硬编码为A股时钟，也能在Wake/Resume后立即执行Missed Period扫描。

这是User LaunchAgent：只有用户GUI Session存在时才会运行。Mac处于唤醒状态并不等同于用户
已登录；注销用户后，即使系统没有Sleep，`gui/<uid>`中的Agent也不会覆盖后续周期。东京时区
主机上的四个默认触发分别是11:33、12:33、15:03、16:03 JST（对应10:33、11:33、
14:03、15:03 Asia/Shanghai）。

## Trading Day Gate

Runner使用Hithink正式`/api/a-share/calendar/trading-days`，本地保存脱敏Calendar Snapshot：

```text
data/runtime/a_share_calendar.json
```

当前Snapshot包含241个交易日，最后开放日为2026-08-28，`authoritative_through=2026-08-30`。
周末可直接确定跳过；工作日Calendar未覆盖时，联网Runner先刷新Calendar，失败则Fail Closed。

2026-08-30真实运行结果：

```text
SKIPPED_NON_TRADING_DAY
network_attempts = 0
```

同一天重复launchd tick不会重复写Skip Manifest，也不会请求行情。

## As-Of / Lookahead

只有`period_end + buffer <= as_of`的完成周期才进入正式Runtime。Combined Result要求：

- Market、Market 15m和两股结果属于相同period_end；
- 四套rules_version完全匹配冻结版本；
- Market/Stock Replay的lookahead与score immutability均为PASS；
- 过去period只从Replay中精确查找，不使用当前未完成Bar。

任一条件失败会写Runtime Failure Record，不会生成成功结果。

## Idempotency

幂等键：

```text
trading_date + period_end + all frozen rules_versions
```

业务结果使用内容Hash；`generated_at`、执行模式和Source文件UUID不改变业务Hash。已有成功结果时
返回`SKIPPED_ALREADY_COMPLETED`，不刷新Provider。若相同幂等键出现不同业务内容，则拒绝为
`IDEMPOTENCY_CONFLICT`。

2026-08-28 15:00重复历史运行已真实返回`SKIPPED_ALREADY_COMPLETED / network_attempts=0`。

## Lock / Recovery

`data/runtime/runner.lock`使用macOS `fcntl.flock`非阻塞排他锁，并保存PID、创建时间、
process-start marker和run_id：

- 活跃锁：`SKIPPED_ALREADY_RUNNING`；
- 进程崩溃：内核自动释放flock；
- 下次启动根据PID/创建时间/stale timeout记录`stale_lock_recovered`；
- 正常退出写`status=RELEASED`，Health Check不会误报stale。

stale timeout当前为7200秒，配置化。

## Retry / Fallback

Retry默认最多3次，backoff为2秒、5秒。只重试：

```text
NETWORK_ERROR
TIMEOUT
TEMPORARY_PROVIDER_ERROR
RATE_LIMIT
```

Mapping、Unsupported、Permission/Auth、Schema/Contract错误不重试。Provider Fallback继续由现有
`MarketDataService`处理，Scheduler没有复制Provider选择逻辑。

对于相同的`trading_date + period_end + rules_versions`，一旦Manifest已有
`FAILED / recoverable=false`，该周期进入`TERMINAL_FAILED`。普通自动Tick返回
`SKIPPED_TERMINAL_FAILURE`、稳定的`TERMINAL_FAILED|<idempotency_key>` skip key和
`NON_RECOVERABLE_FAILURE_ALREADY_RECORDED`，不再调用Pipeline，且正常以exit code 0结束。
历史上尚未写入idempotency key的Failure会从其period与rules_versions重建身份。只有显式
Operator `--force`允许重新执行Terminal Failure；`recoverable=true`不被这个门禁误阻断。

## Catch-up / Wake Recovery

每次Runner启动会计算当天所有已完成周期，并与Runtime Manifest比较。缺失周期标记：

```text
execution_mode = CATCH_UP
notification_eligibility = CATCH_UP_STALE_FUTURE_POLICY
missed_completed_period = true
```

历史真实验证：

```bash
uv run python scripts/run_intraday_monitor.py \
  --as-of 2026-08-28T15:03:00+08:00 \
  --no-network
```

结果：10:30、11:30、14:00、15:00四周期全部补算成功，均关联TASK_008～010 Replay，
`network_attempts=0`，没有冒充`LIVE_SCHEDULED`。

Mac睡眠期间launchd不能保证准点触发；唤醒后的StartInterval只能保证重新启动Runner，再由上述
Catch-up机制恢复。真实Sleep/Wake边界仍需交易日验收。

## Runtime Manifest / Report

独立于Risk Output Manifest：

```text
data/runtime/
  manifest.jsonl
  runs/<run_id>.json
  reports/<period>__<content_hash>.json
  reports/<period>__<content_hash>.md
```

Run Manifest包含run_id、scheduled period、开始/结束/耗时、交易日期、period_end、status、
network attempts、Market/15m/Stock Result ID、error summary、rules versions、execution mode与
notification eligibility。Failure同样生成Run Record。

Combined Report只输出Market、Market 15m、两股60m/15m、Coverage与Confidence。Industry固定：

```text
DEFERRED
```

不包含买卖或仓位建议。

## Logs

Runtime日志：`logs/runtime/intraday_monitor.log`。

使用Python `RotatingFileHandler`，单文件2MB、保留10份。Risk Snapshot不参与日志轮转。日志会
用本地Credential值和常见Key模式双重脱敏。launchd环境设置`TREND_MONITOR_LAUNCHD=1`，避免
每分钟把Runner JSON写入stdout。

## launchd

模板：`config/launchd/com.trendmonitor.local.intraday.plist`。

管理命令：

```bash
uv run python scripts/manage_launchd.py --install
uv run python scripts/manage_launchd.py --status
uv run python scripts/manage_launchd.py --uninstall
```

已真实安装：

```text
~/Library/LaunchAgents/com.trendmonitor.local.intraday.plist
INSTALLED true
LOADED true
runs 7
last exit code 0
```

plist不包含Secret，只包含绝对项目路径、`/usr/local/bin/uv`、60秒StartInterval及日志路径；
Runner自行从0600的`.env`读取Credential。文件位于用户LaunchAgents目录，具备下次登录自动
加载条件，但本Task没有真的重启Mac，故只能标记“配置持久化PASS / 重启实证PENDING”。

## Health Check

入口：

```bash
uv run python scripts/check_runtime_health.py
```

当前结果`PASS`：项目路径、uv/Python、`.env`、0600权限、Longbridge/Hithink凭证存在性、Raw
Cache、Snapshot、Runtime、Logs可写、Calendar、launchd模板/安装、最近成功Run、Lock正常释放
及磁盘空间均通过。Health只输出`PRESENT/MISSING`，不打印Credential值。

Health Check同时核对已安装plist、当前GUI domain loaded状态、disabled状态、last exit code、
run interval、WorkingDirectory、Program路径、Runtime日志可写性，以及最近一次launchd heartbeat
和Runtime Manifest中的Launchd观察。plist存在但未加载会明确返回
`LAUNCH_AGENT_NOT_LOADED`。

Restart Recovery只能通过真实重启验证：先记录当前Boot与plist hash/mtime，重启并登录用户，
不要运行install/bootstrap/kickstart，等待两个StartInterval，再运行Health Check。只有新Boot后的
GUI domain自动loaded、plist未变化且heartbeat晚于本次登录，才可记录`RESTART_RECOVERY = VERIFIED`。
此前只能记录`IMPLEMENTED_PENDING_RESTART_TEST`。

## Tests / Regression

TASK_013定向测试覆盖：四周期、午休、时区、非交易日、幂等、Lock、stale恢复、Retry、确定性
错误不重试、Catch-up、lookahead、append-only、Secret日志、0600 Fail Closed、Industry Deferred、
规则Hash不变、当前Runner、历史Runner和Failure Record。

完整回归结果见最终Task报告。TASK_011/012继续保持Industry `BLOCKED / DEFERRED`。

本次实际结果：TASK_013定向22/22、全量199/199；Registry 20/20；Risk Input、8指数
FULL_READY、Market 60m 80周期、Market 15m 80周期、Stock 160 Observations全部通过。
Runtime日志、Manifest、模板及已安装plist共23个文件的Credential值扫描为`0 hits`。

## Unattended Runtime Readiness

`READY_WITH_LIMITS`。

实现、历史补算、非交易日和实际launchd RunAtLoad均已通过；限制是尚未在真实A股交易日观察
至少一个上午和一个下午自动边界，也未通过真实Mac重启/Sleep-Wake验收。

## 下一阶段建议

只建议：

> TASK_013A｜真实交易日launchd上午/下午、重启与Sleep-Wake验收 v0.1

完成该验收并达到`READY`后，再考虑TASK_014 Notification。

## 是否影响Master

`NO`。Runtime只消费冻结规则，没有写回Master、Risk Config或趋势系统v0.3.1。
