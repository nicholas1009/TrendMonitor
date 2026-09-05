# Data Source Contract v0.1

Status: **PARTIAL**
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
| Daily | open | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| Daily | high | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| Daily | low | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| Daily | close | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| Daily | volume | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | DATA_CONFLICT |
| Daily | turnover | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| Daily | timestamp | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | PROVISIONAL |
| Daily | trade_date | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | PROVISIONAL |
| 15m | open | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 15m | high | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 15m | low | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 15m | close | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 15m | volume | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| 15m | turnover | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| 15m | bar_end | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | PROVISIONAL |
| 60m | open | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 60m | high | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 60m | low | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 60m | close | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED |
| 60m | volume | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| 60m | turnover | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| 60m | bar_end | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | PROVISIONAL |
| Latest Quote | last | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | open | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | high | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | low | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | prev_close | longbridge | CNY/share | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | CONFIRMED_CAPABILITY_RESEARCH_NOT_FORMAL_RISK_INPUT |
| Latest Quote | volume | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| Latest Quote | turnover | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | UNKNOWN |
| Latest Quote | timestamp | longbridge | UNKNOWN | QUESTION_FOR_DAILY; NOT_IMPLEMENTED_FOR_MINUTE | PROVISIONAL |
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
- Longbridge SDK returns an epoch-backed timestamp that maps correctly in the audited 2026-09-03/04 data. Its Python SDK reference does not explicitly document the timezone of the returned naive `datetime`, so the general timezone contract remains **PROVISIONAL**.

## Units and Adjustment

- Hithink documents stock Daily/Quote volume in shares and Auction volume in hands.
- Longbridge documents the `volume` field type but not its unit. The six dual-source samples show a stable approximately 100x raw-value ratio. No adapter or normalizer conversion exists. The volume contract is therefore **DATA_CONFLICT**, not inferred as shares-versus-hands.
- Turnover shows no scale conflict in the six samples, but the Longbridge official field unit is not stated; status is **UNKNOWN**.
- Longbridge production history requests use `NoAdjust`/actual. No audited Daily/15m/60m/Auction price-adjustment conflict was found.

## Provenance Contract

- Enabled and degraded formal features require lineage to raw provider evidence.
- A legal disabled feature does not consume a value and does not require lineage.
- Separate persisted normalized/validated snapshot object IDs do not exist; their transformation and field-quality/preflight state is embedded in the risk-input snapshot. This is why `PRODUCTION_SOURCE_TRACE` is PARTIAL even though counted formal feature traceability is 100%.

## Production Boundaries

- Hithink: authoritative A-share calendar and closed/final Auction snapshots.
- Longbridge: production Direct Daily, 15m, 60m, and quote inputs; derived features retain Longbridge lineage.
- 600150.SH remains research/shadow only and is not added to the formal risk pipeline.
- No production runtime, provider selection, risk rule, fallback behavior, or notification policy was changed by this audit.
