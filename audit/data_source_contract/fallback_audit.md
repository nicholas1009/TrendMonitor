# Fallback Audit

| Data Type | Primary | Fallback | Status | Provenance |
| --- | --- | --- | --- | --- |
| Daily | longbridge | hithink | QUESTION | EXPLICIT |
| 15m/60m | longbridge | hithink | NOT_IMPLEMENTED | NOT_APPLICABLE |
| Auction | hithink | None | BLOCKED | EXPLICIT_HITHINK_RAW |
| Trading Calendar | hithink | None | BLOCKED | CACHE_METADATA_RETAINED |

`SILENT_FALLBACK_FOUND = NO`. MarketDataService uses explicit ordered candidates and records requested provider, actual provider, fallback flag/reason, and raw path.

No cross-provider fallback is formally approved in Data Source Contract v0.1. The production Daily call sites currently pass Hithink as an explicit candidate, but field/unit semantic compatibility is not ratified; this is **QUESTION**. Hithink minute bars are unsupported, so 15m/60m have no effective fallback. Auction and calendar have no alternate source.
