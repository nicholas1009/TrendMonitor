# TASK_011｜两只正式个股行业Benchmark与60m共振验证 v0.1

本Task只为600487亨通光电与002463沪电股份建立Industry Context辅助层。第一阶段必须以
Provider行业目录和成分证据确认Canonical Benchmark；没有可信DIRECT 15m/60m时，正式
Context必须显式`UNAVAILABLE`，禁止合成行业指数、股票篮子、ETF替代或跨Provider猜码。

规则版本为`stock_industry_context_v0.1`。行业数据可用时只允许可信Close派生行业收益、
相对收益、持续弱势、两层/三层共振与严格as-of历史p10；所有结果只作解释，不得修改冻结的
Stock/Market 60m或15m规则和分数，也不产生交易建议、调度或通知。
