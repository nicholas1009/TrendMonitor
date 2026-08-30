# TASK_009｜15分钟内部结构辅助模块 v0.1

本Task在冻结的`market_60m_risk_v0.1`之下建立8指数15分钟内部结构辅助层。唯一正式分析输入为TASK_006 Preflight允许消费的15m Risk Input / System Bar；只使用可信Close和确定性派生Feature。

完整60m周期固定映射4根15m System Bar，分类限定为`HEALTHY_UP / HEALTHY_DOWN / LATE_REPAIR / FAILED_REPAIR / LATE_WEAKENING / MIXED`。未满4根时只允许`EARLY_STRENGTH / EARLY_WEAKNESS / EARLY_MIXED`。市场辅助状态限定为`REPAIR_BROADENING / WEAKNESS_BROADENING / INTERNAL_MIXED / DATA_INCOMPLETE`。

规则版本固定为`market_15m_internal_v0.1`。输出必须关联Risk Input Snapshot及对应60m Risk Result，append-only保存，并对TASK_008最近20个交易日80周期进行Replay、样本审计、前兆后验统计、确定性和60m Score不可变验证。

本Task不建立独立15m风险灯、风险分数、交易信号、调度、通知、板块、个股、ETF或自动交易；不得修改Daily正式系统或`market_60m_risk_v0.1`。
