# Longbridge分钟数据口径与TrendMonitor System Bar

验证日期：2026-08-29  
TASK_004状态：`PARTIAL`  
正式业务时区：`Asia/Shanghai`

## 结论

Longbridge 15m/60m Source数据可以稳定形成固定数量的Derived System Bar：正常完整交易日为16根15m和4根60m。09:30 opening-only OHLC异常已得到窄范围、可测试的分类和Derived处理，所有Source/Raw字段保持原样。

但当前仍不应把分钟High/Low/Volume直接交给风险引擎：股票分钟聚合High/Low与同源Daily高频不一致；中证500和科创50各有3个交易日出现严重分钟/日线量额差异。因此日线对账失败日明确为`DATA_INCOMPLETE`，TASK保持`PARTIAL`。

真实证据保存在`data/reports/minute_convention_latest.json`；每个窗口的Raw路径和每根System Bar样例Lineage均写在该报告中。

## 三层数据

### Source Bar

Longbridge官方SDK原始响应。通过现有Append-only Raw Cache按Provider/周期/日期分层保存，不修改、不覆盖。

### Normalized Source Bar

只做JSON安全序列化、数字类型、统一字段和UTC epoch到`Asia/Shanghai`转换。Source OHLC不做clamp或覆盖。

### System Bar

TrendMonitor使用的Derived Bar。字段包括：

```text
instrument_id, period, system_start, system_end,
open, high, low, close, volume, turnover,
source_provider, source_bar_ids, source_raw_paths,
transformation, quality_status
```

`source_bar_ids`由Provider、Provider Symbol、周期和源时间戳构成；`source_raw_paths`指向实际Raw窗口文件。

## 交易时段依据

Longbridge官方`QuoteContext.trading_session()`真实返回CN：

```text
09:30–11:30 Intraday
13:00–14:57 Intraday
```

Longbridge Raw路径记录在最新报告的`official_trading_session.raw_path`。官方接口说明见[Longbridge Trading Sessions](https://open.longbridge.com/docs/quote/pull/trade-session)。

上交所2026年交易规则明确：09:30–11:30、13:00–14:57为连续竞价，14:57–15:00为收盘集合竞价，见[上海证券交易所交易规则（2026年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)。这解释了Provider正式Session在14:57结束、但分钟接口另有15:00 Bucket的时间边界。

Longbridge `candlesticks`最多1000根；本次使用官方历史K线接口并分成两个不重叠日期窗口，避免把截断结果误当完整历史。接口限制见[Longbridge Historical Candlesticks](https://open.longbridge.com/docs/quote/pull/history-candlestick)。

## Source结构

正常完整交易日：

```text
15m Source：09:30, 09:45, 10:00, 10:15, 10:30, 10:45, 11:00, 11:15,
            13:00, 13:15, 13:30, 13:45, 14:00, 14:15, 14:30, 14:45, 15:00

60m Source：09:30, 10:30, 13:00, 14:00, 15:00
```

15:00 Source均标记`trade_session=Intraday`，SDK没有单独Auction标签。

## 09:30 Source Boundary Quirk

扫描区间约为2026-04-11至2026-08-29，覆盖四个标的91–96个完整交易日：

| Instrument | 15m Bars | 15m完整日 | 15m 09:30异常 | 15m非09:30异常 | 60m Bars | 60m完整日 | 60m 09:30异常 | 60m非09:30异常 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 600487 | 1,611 | 91 | 0 | 0 | 476 | 92 | 0 | 0 |
| 002463 | 1,611 | 91 | 0 | 0 | 476 | 92 | 0 | 0 |
| 中证500 | 1,615 | 95 | 7 | 0 | 480 | 96 | 6 | 0 |
| 科创50 | 1,615 | 94 | 7 | 0 | 480 | 96 | 5 | 0 |

合计扫描8,364根Source Bar、764根09:30 Bar；25根严格OHLC异常全部位于09:30，非09:30异常为0。异常类型为：

- `LOW_ABOVE_OPEN`：21；
- `HIGH_BELOW_OPEN`：4；
- close越界、high < low或其他类型：0。

25/25个异常Source Open均等于同源Daily Open，且都位于Daily High/Low范围内。因此结论为：

- 这是当前样本中稳定、仅见于指数09:30 opening boundary的Source Quirk；
- 全局严格Validator不放宽；
- 只有“时间=09:30且异常仅涉及open在source high/low外”可返回`SOURCE_BOUNDARY_QUIRK`；
- 相同关系出现在其他时点，或close/high/low本身越界，仍为`INVALID_DATA`。

Source Bar不修改。对应Derived System Bar使用Provider自身O/H/L/C的确定性envelope：`High=max(O,H,L,C)`、`Low=min(O,H,L,C)`，记录：

```text
transformation = SOURCE_BOUNDARY_ENVELOPE
quality_status = SOURCE_BOUNDARY_QUIRK
```

这不是对Source OHLC的更正；Raw与Normalized Source仍保留原值。

## 15:00 Closing Bucket

验证为4个标的×2个周期×20个完整交易日，共160个“标的-周期-日”样本：

- 固定时刻表：160/160；
- 15:00 Volume > 0：160/160；
- 15:00 Close = Daily Close：160/160；
- 纳入15:00后Volume更接近Daily：154/160；
- 纳入15:00后Turnover更接近Daily：158/160；
- 股票15:00为flat OHLC：80/80；
- 指数15:00为flat OHLC：4/80，通常有真实O/H/L/C区间。

结合上交所14:57–15:00收盘集合竞价规则，15:00 Bucket属于正式收盘行情、不可删除。对股票，它高度符合独立收盘集合竞价成交桶；对指数，它表现为收盘阶段的指数路径而非单一flat成交价。这里是基于规则和返回形态的推断；Longbridge仍只标为`Intraday`。

例外必须保留：002463在2026-08-06、中证500在2026-08-07、科创50在2026-08-21出现Closing Bucket量额与Daily异常。尤其两个指数例外日，纳入Closing Bucket后Volume接近重复计算。这些日期不得自动修量，交由Daily reconciliation标记。

## 15m System Bar规则

前15根Source Bar保持1:1 Derived映射；最后一根合并14:45 Source与15:00 Closing Bucket：

```text
Open     = 14:45 Source Open
High     = max(两根Source的O/H/L/C)
Low      = min(两根Source的O/H/L/C)
Close    = 15:00 Source Close
Volume   = sum
Turnover = sum
system_start = 14:45
system_end   = 15:00
transformation = MERGE_CLOSING_BUCKET
quality_status = MERGED_CLOSING_BUCKET
```

四标的各60个完整交易日均稳定形成16根/日。缺15:00、重复、午休Bar或时刻表不完整时返回`DATA_INCOMPLETE`/`INVALID_DATA`，不补价。

## 60m System Bar规则

形成固定4个观察周期：

```text
09:30–10:30
10:30–11:30
13:00–14:00
14:00–15:00（14:00 Source + 15:00 Closing Bucket）
```

最后一根采用与15m相同的OHLCVT合并规则。四标的各60个完整交易日均稳定形成4根/日，不把Longbridge的5根Source Bar解释为5个系统周期。

## Daily Reconciliation

配置化质量门槛：Volume和Turnover相对差≤0.1%才可`PASS`；≤1%且Open/Close匹配时为`REVIEW_REQUIRED`；其余为`DATA_INCOMPLETE`。门槛只用于分类，不修改数据。

60日×4标的×2周期共480组对账：

- System Open与Daily Open：480/480匹配；
- System Close与Daily Close：480/480匹配；
- 股票4组报告均无FAIL，但High/Low高频不一致，最大差分别为1.37和0.90；
- 中证500在2026-07-01、2026-07-03、2026-08-07为FAIL，最大Volume相对差98.29%；
- 科创50在2026-06-15、2026-07-14、2026-08-21为FAIL，最大Volume相对差98.15%，最大Turnover相对差6.91%；
- 15m与60m在这些严重异常日给出相同量额结论，说明问题来自Source分钟/Closing Bucket口径，而不是System合并算法。

09:30 envelope把指数开盘边界造成的Daily极值差异压缩到≤0.03指数点，但不能解释股票分钟High/Low与Daily的大量差异。TASK_003已确认Hithink与Longbridge股票Daily OHLC匹配，因此不能把Daily差异简单视为单源日线错误。

## Data Quality状态

| 状态 | 含义 |
| --- | --- |
| `DIRECT_NORMALIZED` | 1:1 Derived，Source严格有效 |
| `MERGED_CLOSING_BUCKET` | 合并最后常规Source与15:00 Bucket |
| `SOURCE_BOUNDARY_QUIRK` | 09:30 opening-only Source异常；Raw保留，Derived envelope显式记录 |
| `INVALID` / `INVALID_DATA` | 非白名单OHLC、重复、越界等错误 |
| `DATA_INCOMPLETE` | 缺Bar/Closing Bucket或Daily reconciliation失败 |

## 已知限制与进入风险引擎条件

当前不可以进入正式风险引擎（`NO`）：

1. 股票分钟High/Low无法稳定重建同源、且经Hithink交叉支持的Daily High/Low；这会直接影响未来ATR和区间风险计算。
2. 指数少数日期Closing Bucket Volume/Turnover存在严重异常，必须在上层使用前由Daily质量门控阻断。
3. Longbridge把15:00 Bucket标为`Intraday`，没有Auction语义字段；当前语义来自交易所规则与实测证据。

下一Task应只调查Longbridge分钟High/Low及异常量额口径（可使用官方更细周期/成交数据和第二来源日线），不得开始风险模型，也不需要开发Quote采样`LOCAL_AGGREGATION`。
