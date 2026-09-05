# Data Source Contract v0.1

Status: **COMPLETE**
Audit scope: 2026-09-03 and 2026-09-04 production evidence. All eight successful intraday results are `CATCH_UP`; they are not LIVE proof.

## Contract Matrix

| Data Type | Field | Canonical Source | Unit | Fallback | Status |
| --- | --- | --- | --- | --- | --- |
| Trading Calendar | trading_day | hithink | calendar date | BLOCKED | CONFIRMED |
| Auction | auction_price | hithink | CNY/share | BLOCKED | CONFIRMED |
| Auction | open_price | hithink | CNY/share; exact field semantics remain provider-defined | BLOCKED | QUESTION |
| Auction | auction_volume | hithink | hand | BLOCKED | CONFIRMED |
| Auction | timestamp | hithink | epoch milliseconds; provider response assembly time | BLOCKED | CONFIRMED |
| Auction | auction_phase | hithink | enum | BLOCKED | CONFIRMED |
| Auction | data_status | hithink | enum | BLOCKED | CONFIRMED |
| Daily | open | longbridge | CNY/share | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED |
| Daily | high | longbridge | CNY/share | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED |
| Daily | low | longbridge | CNY/share | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED |
| Daily | close | longbridge | CNY/share | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED |
| Daily | volume | longbridge | shares (Longbridge CN raw x100) | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED_EMPIRICALLY |
| Daily | turnover | longbridge | UNKNOWN | BLOCKED_PENDING_CONTRACT_VALIDATION | UNKNOWN |
| Daily | timestamp | longbridge | Unix epoch; aware Asia/Shanghai market_time | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED_EMPIRICALLY |
| Daily | trade_date | longbridge | Asia/Shanghai calendar date | BLOCKED_PENDING_CONTRACT_VALIDATION | CONFIRMED_EMPIRICALLY |
| 15m | open | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 15m | high | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 15m | low | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 15m | close | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 15m | volume | longbridge | shares (Longbridge CN raw x100) | NOT_IMPLEMENTED | CONFIRMED_EMPIRICALLY |
| 15m | turnover | longbridge | UNKNOWN | NOT_IMPLEMENTED | UNKNOWN |
| 15m | bar_end | longbridge | timezone-aware Asia/Shanghai datetime | NOT_IMPLEMENTED | CONFIRMED_EMPIRICALLY |
| 60m | open | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 60m | high | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 60m | low | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 60m | close | longbridge | CNY/share | NOT_IMPLEMENTED | CONFIRMED |
| 60m | volume | longbridge | shares (Longbridge CN raw x100) | NOT_IMPLEMENTED | CONFIRMED_EMPIRICALLY |
| 60m | turnover | longbridge | UNKNOWN | NOT_IMPLEMENTED | UNKNOWN |
| 60m | bar_end | longbridge | timezone-aware Asia/Shanghai datetime | NOT_IMPLEMENTED | CONFIRMED_EMPIRICALLY |
| Latest Quote | last | longbridge | CNY/share | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | open | longbridge | CNY/share | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | high | longbridge | CNY/share | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | low | longbridge | CNY/share | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | prev_close | longbridge | CNY/share | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | volume | longbridge | shares (Longbridge CN raw x100) | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_EMPIRICALLY |
| Latest Quote | turnover | longbridge | UNKNOWN | NOT_APPLICABLE_RESEARCH_CAPABILITY | UNKNOWN |
| Latest Quote | timestamp | longbridge | Unix epoch; aware Asia/Shanghai market_time | NOT_APPLICABLE_RESEARCH_CAPABILITY | CONFIRMED_EMPIRICALLY |
| Derived | ATR14-SMA | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED_FOR_RESEARCH_NOT_CURRENT_INTRADAY_SCORE |
| Derived | MA | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | returns | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | breadth | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | persistence | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | repair | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | distortion | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | shock | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | stock relative strength | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | 15m supporting features | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |
| Derived | 60m features | local deterministic calculation over Longbridge raw inputs | derived | inherits source field policy | CONFIRMED |

## Time Semantics

- Trading timezone is Asia/Shanghai. `analysis_as_of` and `market_period_end` remain the scheduled market boundary; later `provider_observed_at` does not move that boundary.
- The retained 2026-09-03 15:00 current Market artifact stores its runtime cutoff in the legacy `as_of` field, while `last_completed_bar_end`, report period, replay period, Stock context, and selected bars are all 15:00. It is labeled explicitly in its trace and is not treated as a later market period.
- Auction market time is 09:25. The audited closed/final snapshots were observed later during operator CATCH_UP.
- Controlled `longbridge==4.5.0` calls under Asia/Tokyo and UTC produced different naive wall-clock values but identical Unix epochs. The SDK value is therefore confirmed as a process-local naive representation of an absolute instant. The adapter converts to epoch first and emits timezone-aware Asia/Shanghai `market_time`; `TIMEZONE_CONTRACT = PASS`.

## Units and Adjustment

- Hithink documents stock Daily/Quote volume in shares and Auction volume in hands.
- Longbridge documents the `volume` field type but not its unit. Across all six retained Daily samples, `turnover / volume_raw` lies outside the day's price range while `turnover / (volume_raw * 100)` lies inside it; Hithink share volume independently agrees after normalization and no counterexample exists. `LONGBRIDGE_CN_VOLUME_SCALE = 100_SHARES_PER_RAW_UNIT` is **EMPIRICALLY_CONFIRMED_BY_DIMENSIONAL_INVARIANT**, not officially documented.
- Canonical cross-provider volume is shares. Hithink Daily is identity; Hithink Auction hands multiply by 100. Unknown provider/unit combinations are never converted automatically.
- Turnover shows no scale conflict in the six samples, but the Longbridge official field unit is not stated; status is **UNKNOWN**.
- Longbridge production history requests use `NoAdjust`/actual. No audited Daily/15m/60m/Auction price-adjustment conflict was found.

## Provenance Contract

- Enabled and degraded formal features require lineage to raw provider evidence.
- A legal disabled feature does not consume a value and does not require lineage.
- Separate persisted normalized/validated snapshot object IDs do not exist; their transformation and field-quality/preflight state is embedded in the risk-input snapshot. This is why `PRODUCTION_SOURCE_TRACE` is PARTIAL even though counted formal feature traceability is 100%.

## Production Boundaries

- Hithink: authoritative A-share calendar and closed/final Auction snapshots.
- Longbridge: production Direct Daily, 15m, 60m, and quote inputs; derived features retain Longbridge lineage.
- Production Hithink Daily fallback is `BLOCKED_PENDING_CONTRACT_VALIDATION`; explicit research/cross-validation use remains available.
- Each successful analysis cycle freezes one immutable Raw-member bundle and propagates its `cycle_raw_snapshot_id` through Coverage, Risk Input, Market 60m, Market 15m, Stock and Runtime result provenance.
- 600150.SH remains research/shadow only and is not added to the formal risk pipeline.
- The only production source-selection change is the explicit safety block on an unapproved Hithink Daily fallback. Runtime scheduling, risk rules, scores, lights and notification policy are unchanged.
