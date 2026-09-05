# Hardcoded Source Audit

| File / function | Direct source | Data type | Production impact | Status |
| --- | --- | --- | --- | --- |
| `scripts/run_intraday_monitor.py` Auction/calendar setup | Hithink | Auction, calendar | Production scheduler entrypoint | JUSTIFIED |
| `scripts/verify_market_index_coverage.py` refresh | Longbridge | index Daily/15m/60m | Production stage | JUSTIFIED_CANONICAL_SOURCE; policy is call-site fixed |
| `scripts/verify_risk_input.py` refresh | Longbridge, explicit Hithink Daily candidate | stock Daily/15m/60m | Production stage | QUESTION_FOR_DAILY_FALLBACK |
| `src/trend_monitor/services/market_data.py` | Registry adapters | general | Production service | JUSTIFIED |
| `scripts/verify_*`, research scripts | provider-specific | verification/research | No source-selection conflict in formal results | JUSTIFIED_BY_SCOPE |

`HARDCODED_SOURCE_CONFLICT = NO` for the audited results. Some production entrypoints instantiate a canonical provider directly rather than selecting it from a centralized policy object. Their observed source matches the contract and is recorded in lineage, but the Daily fallback approval remains unresolved.
