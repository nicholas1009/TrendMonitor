# Longbridge分钟字段可信度Profile

验证日期：2026-08-29  
数据口径：Longbridge `NoAdjust`，统一为 `Asia/Shanghai`  
结论：`YES_WITH_LIMITS`

## 样本与方法

真实验证覆盖四个正式对象，每个对象60个共同完整交易日：

| Instrument | Asset Type | 日期范围 | 1m行 | 15m行 | 60m行 | Daily行 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 600487 亨通光电 | Stock | 2026-06-02–2026-08-28 | 14,447 | 1,020 | 300 | 60 |
| 002463 沪电股份 | Stock | 2026-06-01–2026-08-28 | 14,447 | 1,020 | 300 | 60 |
| 000905 中证500 | Index | 2026-06-04–2026-08-28 | 14,459 | 1,020 | 300 | 60 |
| 000688 科创50 | Index | 2026-06-03–2026-08-28 | 14,460 | 1,020 | 300 | 60 |

执行了五条逐字段比较链：

- A：1m DIRECT → 诊断聚合15m，对比15m DIRECT；
- B：1m DIRECT → 诊断聚合60m，对比60m DIRECT；
- C：1m DIRECT → 诊断聚合全天，对比Daily DIRECT；
- D：System 15m → 聚合全天，对比Daily DIRECT；
- E：System 60m → 聚合全天，对比Daily DIRECT。

1m聚合仅用于本Task诊断，不能成为Provider fallback或`LOCAL_AGGREGATION`。部分有效交易日只有239/240根1m，原因是源端没有返回无成交分钟；诊断允许在合法交易时刻集合内聚合这些稀疏分钟，但生产System Bar仍要求固定16根15m或4根60m，缺周期时为`DATA_INCOMPLETE`。

完整的count、mean、median、p90、p95、p99、max、mismatch frequency和逐条Raw lineage保存在 `data/reports/risk_input_quality_latest.json`，没有用人为阈值换取PASS。

## Cross-Period结论

| 比较 | 结果 | 关键证据 |
| --- | --- | --- |
| 1m vs 15m | `REVIEW` | 4,080个Close全部一致；Open、High/Low及少量量额有源端差异 |
| 1m vs 60m | `REVIEW` | 1,200个Close全部一致；Open、Low及少量量额有源端差异 |
| 1m vs Daily | `SOURCE_CROSS_PERIOD_SEMANTIC_DIFFERENCE` | 240个Open和Close全部一致；High/Low及量额存在差异 |
| System 15m vs Daily | `SEMANTIC_DIFFERENCE` | 240个Open/Close全部一致；与1m全天的High/Low、量额结论一致 |
| System 60m vs Daily | `SEMANTIC_DIFFERENCE` | 240个Open/Close全部一致；与System 15m结论一致 |

五条链合计6,000个Close比较全部精确匹配。1m、15m和60m的异常日全天聚合高度一致，而与Daily不同，说明主要问题是`SOURCE_CROSS_PERIOD_SEMANTIC_DIFFERENCE`，不是Closing Bucket合并算法造成。

## 误差分布摘要

以下频率均为真实样本，不是质量阈值：

| Asset | 比较 | 字段 | 不一致频率 | 最大相对差 |
| --- | --- | --- | ---: | ---: |
| Stock | 1m vs 15m | Open | 25/2,040 = 1.23% | 0.398% |
| Stock | 1m vs 60m | Open | 17/600 = 2.83% | 0.308% |
| Stock | 1m vs Daily | High | 96/120 = 80.00% | 0.376% |
| Stock | 1m vs Daily | Low | 87/120 = 72.50% | 0.656% |
| Stock | 1m vs Daily | Volume | 108/120 = 90.00% | 0.788% |
| Stock | 1m vs Daily | Turnover | 120/120 = 100% | 0.783% |
| Index | 1m vs 15m | Open | 1/2,040 = 0.05% | 0.042% |
| Index | 1m vs 60m | Open | 1/600 = 0.17% | 0.042% |
| Index | 1m vs Daily | High | 8/120 = 6.67% | 0.0017% |
| Index | 1m vs Daily | Low | 7/120 = 5.83% | 0.0006% |
| Index | 1m vs Daily | Volume | 120/120 = 100% | 98.29% |
| Index | 1m vs Daily | Turnover | 120/120 = 100% | 6.91% |

多数分布的p90/p95为0，是因为差异集中在少量Source Bar；这不能把非零尾部当作不存在。尤其指数Volume出现接近翻倍的极端尾部，必须与股票规则分离。

## Field Quality Profile

| Asset Type | Period | Field | Quality | Evidence / 使用边界 |
| --- | --- | --- | --- | --- |
| Stock | 15m | Open | `APPROXIMATE` | 1m与DIRECT 15m存在25个股票合计样本中的一部分边界差；不做精确触发 |
| Stock | 15m | High | `APPROXIMATE` | 分钟全天无法稳定重构Daily High；仅辅助描述 |
| Stock | 15m | Low | `APPROXIMATE` | 分钟全天无法稳定重构Daily Low；仅辅助描述 |
| Stock | 15m | Close | `TRUSTED` | A、C、D链全部精确匹配；末周期为转换后可信 |
| Stock | 15m | Volume | `APPROXIMATE` | 大多数周期一致，但存在约12%的单周期尾部差及异常日 |
| Stock | 15m | Turnover | `APPROXIMATE` | 与Volume类似；只能作背景说明 |
| Stock | 60m | Open | `APPROXIMATE` | 1m与DIRECT 60m存在2.83%不一致；不做精确触发 |
| Stock | 60m | High | `APPROXIMATE` | 同Daily High存在高频口径差 |
| Stock | 60m | Low | `APPROXIMATE` | 同Daily Low存在高频口径差 |
| Stock | 60m | Close | `TRUSTED` | B、C、E链全部精确匹配；末周期为转换后可信 |
| Stock | 60m | Volume | `APPROXIMATE` | 可描述量能背景，不可成为硬条件 |
| Stock | 60m | Turnover | `APPROXIMATE` | 可描述金额背景，不可成为硬条件 |
| Index | 15m | Open | `APPROXIMATE` | 少量opening boundary差异；保守禁止精确触发 |
| Index | 15m | High | `APPROXIMATE` | 差异很小但非零，且存在`SOURCE_BOUNDARY_QUIRK` |
| Index | 15m | Low | `APPROXIMATE` | 差异很小但非零，且存在`SOURCE_BOUNDARY_QUIRK` |
| Index | 15m | Close | `TRUSTED` | A、C、D链全部精确匹配；末周期为转换后可信 |
| Index | 15m | Volume | `BLOCKED` | 每日均口径不同，并出现约98%极端差异 |
| Index | 15m | Turnover | `ADVISORY_ONLY` | 日常差异小但存在6.91%异常尾部；不得成为条件 |
| Index | 60m | Open | `APPROXIMATE` | 少量opening boundary差异；保守禁止精确触发 |
| Index | 60m | High | `APPROXIMATE` | 非零跨周期差；仅辅助描述 |
| Index | 60m | Low | `APPROXIMATE` | 非零跨周期差；仅辅助描述 |
| Index | 60m | Close | `TRUSTED` | B、C、E链全部精确匹配；末周期为转换后可信 |
| Index | 60m | Volume | `BLOCKED` | 不允许指数风险Feature使用 |
| Index | 60m | Turnover | `ADVISORY_ONLY` | 只可报告质量受限的背景信息 |

最后一个15m/60m System Bar合并15:00 Closing Bucket，其Close由确定性转换得到，运行时标记`TRUSTED_WITH_TRANSFORMATION`；Lineage必须同时包含常规Source Bar与15:00 Bucket。09:30 `SOURCE_BOUNDARY_ENVELOPE`不提升High/Low可信度，仍为`APPROXIMATE`。

## 结论

Close足以支持有限的Close类风险预警。Open、High/Low不得作为精确阈值；股票量额只能辅助，指数Volume禁止使用，指数Turnover只能说明性使用。字段异常时按Feature降级，不应把仍具可信Close和完整周期的整根Bar全部阻断。
