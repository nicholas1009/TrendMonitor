# Proposed Cross-Provider Data Conflict Policy

Status: **PROPOSED_CONFLICT_POLICY**. This document does not change production behavior.

| Conflict | Proposed action | Rationale |
| --- | --- | --- |
| PRICE_CONFLICT | BLOCK | Price semantics directly affect risk and research outputs |
| VOLUME_UNIT_CONFLICT | BLOCK | Never infer or silently normalize an undocumented unit |
| TIMESTAMP_CONFLICT | BLOCK | Can create lookahead or wrong-period selection |
| TRADING_DAY_CONFLICT | BLOCK | Calendar controls scheduling and as-of boundaries |
| ADJUSTMENT_CONFLICT | BLOCK | Adjusted/unadjusted mixing changes returns and levels |
| PROVIDER_STALE | ALLOW_WITH_DEGRADATION only when an existing bounded grace/recoverability contract applies; otherwise BLOCK | Preserve provider timing evidence without accepting incomplete bars |
| FIELD_SEMANTIC_UNKNOWN | QUESTION and exclude from formal scoring | Unknown semantics cannot be promoted to a canonical feature |

Human approval and a versioned production contract are required before wiring this proposal into Runtime.
