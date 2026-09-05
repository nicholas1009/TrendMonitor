# Fallback Audit

| Data Type | Primary | Fallback | Status | Provenance |
| --- | --- | --- | --- | --- |
| Daily | longbridge | hithink | BLOCKED_PENDING_CONTRACT_VALIDATION | EXPLICIT_BLOCK_REASON |
| 15m/60m | longbridge | hithink | NOT_IMPLEMENTED | NOT_APPLICABLE |
| Auction | hithink | None | BLOCKED | EXPLICIT_HITHINK_RAW |
| Trading Calendar | hithink | None | BLOCKED | CACHE_METADATA_RETAINED |

`SILENT_FALLBACK_FOUND = NO`. MarketDataService uses explicit ordered candidates and records requested provider, actual provider, fallback flag/reason, and raw path.

No cross-provider fallback is formally approved in Data Source Contract v0.1. The production Risk Input boundary filters Hithink from Longbridge Daily fallback candidates and records `HITHINK_DAILY_FALLBACK_BLOCKED_PENDING_CONTRACT_VALIDATION`. Explicit research/cross-validation access to Hithink Daily remains available. Hithink minute bars are unsupported, so 15m/60m have no effective fallback. Auction and calendar have no alternate source.
