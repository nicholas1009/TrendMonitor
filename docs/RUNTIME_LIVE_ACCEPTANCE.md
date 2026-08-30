# TASK_013A｜真实交易日 Runtime 生产验收

基线建立日期：2026-08-30（周日）  
业务时区：`Asia/Shanghai`  
主机时区：`Asia/Tokyo / JST`

## TASK_013A

`PARTIAL`：`PENDING_OPERATOR_LIVE_ACCEPTANCE`。

TASK_013 Scheduler没有重新设计。新增内容仅包括：Runtime Manifest触发来源元数据、只读取现有
运行/系统证据的验收器、append-only Acceptance Evidence及其最小测试。实施日不是A股交易日，
没有把历史`--as-of`、手工Python、既有CATCH_UP或周末RunAtLoad冒充Live证据。

机器结果：

```text
data/runtime/acceptance/runtime_live_acceptance_latest.json
```

append-only证据：

```text
data/runtime/acceptance/evidence/
data/runtime/acceptance/manifest.jsonl
data/runtime/acceptance/baseline/
```

验收入口：

```bash
uv run python scripts/verify_runtime_live_acceptance.py
```

该命令不会调用Risk Runner、Provider或生成Risk Result；只读取Runtime Manifest、源结果、
LaunchAgent状态、boot/login信息及`pmset` Sleep/Wake日志，并保存脱敏验收观察。

## 当前验收状态

2026-08-30 23:51 JST收尾复查：当前本地工作区仍只有6条Runtime Manifest记录；其中5条为
2026-08-28历史`CATCH_UP`（包含一条重复跳过事件），没有`LAUNCHD + LIVE_SCHEDULED`记录。
系统boot仍为2026-08-30 12:30 JST，未观察到基线后的restart；`pmset`也没有可与正式交易边界
及Launchd Catch-up配对的新Sleep/Wake证据。Operator操作描述因此尚不能由当前本机证据确认。

| 项目 | 状态 | 当前证据 |
|---|---|---|
| Morning Live 10:30 | PENDING | 没有`LAUNCHD + LIVE_SCHEDULED`真实交易日记录 |
| Afternoon Live 14:00 | PENDING | 没有`LAUNCHD + LIVE_SCHEDULED`真实交易日记录 |
| Restart Recovery | PENDING | 已建立重启前boot/plist基线，尚未发生Operator Restart |
| Sleep/Wake Recovery | PENDING | 没有跨正式边界且由系统日志佐证的Launchd Catch-up |

当前不是验收失败；是四项强制实证尚未完成。在四项齐全前，TASK_013A不会判PASS，TASK_013
既有生产就绪度保持`READY_WITH_LIMITS`；只有观察到核心验收失败时才降为`NOT_READY`。

## Acceptance Provenance

未来Runtime Manifest的`extra`会记录：

```text
trigger_source
launchd_label
process_pid
parent_pid
as_of_override
no_network
force
```

Live PASS要求：

```text
trigger_source = LAUNCHD
execution_mode = LIVE_SCHEDULED
as_of_override = false
no_network = false
force = false
```

同时必须验证Combined Report、Market 60m、Market 15m、两只股票结果、源Snapshot、Provider
Lineage、相同period_end、Lookahead和冻结rules_version。旧Manifest没有该provenance，故不会追认。

## 当前系统基线

```text
boot_time          2026-08-30 12:30 +09:00
console_login      2026-08-30 12:30 +09:00
LaunchAgent        LOADED
last_exit_code     0
installed_plist    unchanged baseline captured
```

LaunchAgent仍保持已安装/已加载状态；本Task没有执行`manage_launchd.py --install`、
`launchctl bootstrap`、reboot或Sleep。

## Operator真实验收顺序

在同一个正式A股交易日进行：

1. 保持Mac开机、用户登录且Awake，让10:33（上海时间）自动完成10:30周期；不要手工运行Runner。
2. 运行验收器和Health Check保存上午证据。
3. 由Operator重启Mac并重新登录；不要运行`manage_launchd.py --install`或手工bootstrap。
4. 保持Awake，让14:03（上海时间）自动完成14:00周期，再运行验收器和Health Check。
5. 在15:03（上海时间）前由Operator让Mac Sleep，并跨过边界；Wake后等待LaunchAgent自然运行。
6. 运行验收器和Health Check，确认15:00缺失周期为`CATCH_UP`，且`pmset`日志能配对Sleep/Boundary/Wake。

主机比上海快一小时：10:33/14:03/15:03上海时间分别是11:33/15:03/16:03日本时间。

## Trigger Delay / Provider / Closing Bucket

当前无Live样本，因此Trigger Delay、Live Provider/Fallback、最新Source Timestamp和+3分钟数据
到齐状态均为`PENDING`。未来验收器会从Manifest与关联Risk Input Snapshot解析：

```text
scheduled_at
started_at
trigger_delay_seconds
requested_provider
actual_provider
fallback_used
latest_source_timestamp
last_completed_bar_end
```

若Sleep/Wake样本是15:00周期，还会要求8个Market输入最后60m Bar均保留现有
`MERGE_CLOSING_BUCKET`，不会修改Closing Bucket规则。

## Restart判据

Restart PASS要求boot time晚于本次基线、用户重新登录、LaunchAgent处于loaded、已安装plist的
Hash和mtime未改变，并存在重启后的真实Launchd成功Run。若必须重新install/bootstrap，判为FAIL，
不能通过手工重载掩盖问题。

## Sleep/Wake判据

Sleep/Wake PASS要求系统日志证明Mac在scheduled boundary处于Sleep；同周期没有Live成功结果；
Wake后LaunchAgent触发Runner并产生`execution_mode=CATCH_UP`；Combined Report为相同period、
strict as-of / lookahead PASS。仅有CATCH_UP而没有Sleep日志不会通过。

## Runtime Health / Secret / Rules

当前：

```text
Runtime Health     PASS
Secret Audit       PASS
Lookahead Gate     PASS
Frozen Rule Hash   PASS
```

Acceptance Evidence不保存Credential；`.env`保持0600。Market/Stock 60m及15m规则、Safe Feature
Contract、TASK_013历史Manifest和Risk Snapshot均未修改。

## Known Production Limits

- Power off：关机期间不能运行。
- User login：当前是User LaunchAgent，重启后必须登录用户Session。
- Sleep：不能保证boundary准点执行，只能在Wake后Catch-up。
- Network：只使用既有有限Retry/Fallback；断网可能失败并留Failure Record。
- Provider：依赖上游数据可用性与+3分钟数据到齐情况。

## 下一阶段建议

当前只建议：

> 继续完成TASK_013A的真实交易日Operator验收。

四项达到PASS并进入`READY / READY_WITH_LIMITS`后，才建议TASK_014 Bark Notification。

## 是否影响Master

`NO`。本Task只增加Runtime验收证据，不写回Master或任何业务规则。
