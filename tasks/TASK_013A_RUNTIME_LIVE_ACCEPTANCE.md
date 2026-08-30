# TASK_013A｜真实交易日 launchd / Restart / Sleep-Wake 生产验收 v0.1

本Task只验证TASK_013既有无人值守Runtime，不重新实现Scheduler，也不修改任何Market/Stock
Risk规则。验收证据必须来自无`--as-of`、无`--no-network`、无`--force`的LaunchAgent运行。

正式通过需要：10:30上午Live、14:00下午Live、重启登录后自动恢复、跨正式边界Sleep/Wake后
Catch-up四项均为PASS。人工Runner、历史Replay和缺少trigger provenance的旧记录不能计入。

当前状态：`PARTIAL / PENDING_OPERATOR_LIVE_ACCEPTANCE`。

