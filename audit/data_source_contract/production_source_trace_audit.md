# Production Source Trace Audit

## Result

- `PRODUCTION_SOURCE_TRACE = PARTIAL`
- `SOURCE_TRACE_COMPLETENESS = 100.00%`
- `SNAPSHOT_CONTRACT = FAIL`
- `AS_OF_CONTRACT = PASS`
- `LOOKAHEAD = PASS`

The completeness denominator is the set of formal persisted Market 60m, Market 15m, Stock 60m, Stock 15m, and Auction feature/field instances in the eight audited successful reports plus two Auction snapshots. Disabled non-consuming inputs are audited separately and are not counted as formal feature instances.

| Period | Execution Mode | Snapshot | As-Of |
| --- | --- | --- | --- |
| 2026-09-03T10:30:00+08:00 | CATCH_UP | PASS | PASS |
| 2026-09-03T11:30:00+08:00 | CATCH_UP | PASS | PASS |
| 2026-09-03T14:00:00+08:00 | CATCH_UP | PASS | PASS |
| 2026-09-03T15:00:00+08:00 | CATCH_UP | FAIL | PASS |
| 2026-09-04T10:30:00+08:00 | CATCH_UP | PASS | PASS |
| 2026-09-04T11:30:00+08:00 | CATCH_UP | PASS | PASS |
| 2026-09-04T14:00:00+08:00 | CATCH_UP | PASS | PASS |
| 2026-09-04T15:00:00+08:00 | CATCH_UP | PASS | PASS |

## Counts

| Scope | Total | Traceable | Rate |
| --- | --- | --- | --- |
| Market | 1344 | 1344 | 100.00% |
| Stock | 448 | 448 | 100.00% |
| Auction | 18 | 18 | 100.00% |
| All | 1810 | 1810 | 100.00% |

All counted fields reach an existing provider raw file with a SHA-256 and request metadata. The overall chain is still PARTIAL because the normalized and validated stages are embedded in risk-input structures rather than separately persisted objects with independent snapshot IDs.

The two 15:00 production assemblies per trading date retain Direct Daily evidence for both formal stocks (four snapshots total): requested provider and actual provider are Longbridge, `fallback_used=false`, and the risk-input snapshot points to an existing NoAdjust Longbridge raw file.

The retained 2026-09-03 15:00 combined evidence contains Market 60m and Market 15m versus Stock replay-context raw-snapshot identity mismatches. Both snapshot sets end at 15:00 and the Current/Replay semantic values match, but no contract authorizes treating different raw identities as the same snapshot. This historical artifact remains `LEGACY_SNAPSHOT_IDENTITY_MISMATCH` and is not rewritten. TASK_028 adds one immutable cycle bundle for new executions; the latest controlled saved-input replay has `SNAPSHOT_CONTRACT = PASS`. No Current/Replay market-period drift was found.
