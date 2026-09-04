# 中国船舶 Opening Playbook v0.1

状态：`INCONCLUSIVE`；运行模式：`READ_ONLY / SHADOW / PRECISION_FIRST`。

目标日期尚未确认：Hithink 日历在本次审计时只权威覆盖至 2026-09-04，2026-09-07 尚未出现。研究 CLI 会在执行当天重新检查；日期缺失时直接 `NO_SIGNAL`。

## 输入门槛

只有同时满足以下条件才匹配场景：

1. 当前上海日期存在于 Hithink authoritative trading calendar；
2. `auction_phase=closed`；
3. `data_status=final`；
4. 600150.SH 的 `auction_price` 非空。

否则输出 `NO_DECISION / DATA_NOT_READY` 或 `NO_SIGNAL`，不写生产状态、不发 Bark。

## 场景

| 场景 | Auction gap vs 37.47 | Calibration / Validation | v0.1 Action |
| --- | --- | ---: | --- |
| LOW_OPEN | ≤ -0.3174% | 3 / 1 | NO_SIGNAL（SAMPLE_THIN） |
| NEUTRAL_OPEN | (-0.3174%, +0.1537%) | 1 / 2 | NO_SIGNAL（SAMPLE_THIN） |
| HIGH_OPEN | ≥ +0.1537% | 3 / 1 | NO_SIGNAL（SAMPLE_THIN） |

当前没有历史证据充分的 ADD、HOLD 或 DEFENSIVE 开盘桶。`NO_SIGNAL` 是本版正确结果，并不等于突破已失败。

即使未来某一预注册场景晋级为 ADD：

- `ADD_QUALIFICATION = YES`
- `EXECUTE_AT_AUCTION = NO`
- `TARGET_POSITION_SIZE = UNKNOWN`
- 只能展示 `PROVISIONAL_EXPERIMENT_SIZE = 100 shares`，不得冒充正式仓位管理规则

原因：Auction/Open bridge 只有 5 个样本，且尚未验证可执行的 post-open window。
