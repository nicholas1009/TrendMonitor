# TASK_016｜亨通光电＋沪电股份 09:25 集合竞价采集与手机通知 v0.1

当前状态：`IMPLEMENTED_PENDING_LIVE_AUCTION`。

## 已实现范围

- 固定标的：`600487.SH` 亨通光电、`002463.SZ` 沪电股份。
- 官方接口：`GET /api/a-share/auction/snapshot`，请求 `stage=final`。
- 自动窗口：A 股交易日 `09:25:00` 至 `09:32:59`（`Asia/Shanghai`）。该上限是
  TASK_024A 基于 2026-09-03、2026-09-04 live evidence 设置的 provisional provider
  grace，状态为 `PENDING_MORE_LIVE_SAMPLES`，不改变 09:25 Auction market time。
- 终态条件：`auction_phase=closed` 且 `data_status=final`，并且两个标的都存在。
- 复用 `com.trendmonitor.local.intraday` 的 60 秒 Tick；没有新增 LaunchAgent、线程或秒级调度器。
- 复用 Hithink Provider、Provider Registry、Raw Cache、Runtime Store、NotificationService、Bark Adapter 和中文 Presentation Layer。
- 成功幂等键：`trading_date + AUCTION_FINAL_SNAPSHOT`；成功后当天不再请求、保存或通知。
- 窗口内已有 Not Ready 请求证据且超过自动窗口仍未成功时，记录一次 `FAILED / DATA_NOT_READY` 并发送一次运行异常类通知；当天不再自动重试。代码部署或进程启动时已经错过窗口且没有请求证据，只返回 `MISSED_AUTOMATIC_WINDOW`，不猜测为数据源失败。
- 盘后补采只能由 Operator 显式使用 `--auction-catch-up`，并记录为 `CATCH_UP`，不冒充 `LIVE_SCHEDULED`。

## 数据边界

Raw Snapshot 保存官方原始信封及直接返回字段；`null` 原样保留，不补零。没有使用普通 Quote、Longbridge、1 分钟 K 线或其他推导值替代集合竞价数据。

当前没有增加交易建议字段，也没有定义任何主观阈值、固定百分比、目标价或止损规则。

## 未来研究目标

Auction 功能最终不是单纯展示竞价数据。未来在积累足够真实样本后，将按 `trading_date + symbol` 联结：

前一交易日状态 + 09:25 集合竞价 + 09:30 以后真实盘中走势。

研究是否能够高置信度区分：

1. 开盘买入
2. 开盘卖出
3. 正 T
4. 倒 T
5. 不操作

进一步研究：

- 先卖时的建议卖出价格、目标买回价格、最大允许偏差、判断错误后的强制买回价格。
- 先买时的建议买入价格、目标卖出价格、最大允许偏差、判断错误后的强制卖出价格。
- 判断错误时的止损 / 撤退价格及必须执行的反向买回 / 卖出价格。

这些价格只能来自实际历史样本、波动率、ATR、前日价格结构、集合竞价缺口和盘中统计。当前正式交易规则：`NO`。

## 验收边界

只有真实 A 股交易日 09:25 后完整通过 Hithink Final → 两标的数据有效 → Raw Snapshot → Runtime Manifest → Notification Policy → Bark → 手机收到中文通知，才可把状态改为 `LIVE_AUCTION = VERIFIED`。

TASK_013A 保持 `PENDING`，其四个 60 分钟 `LIVE_SCHEDULED` 验收与本任务独立。

Master Impact：`NO`。
