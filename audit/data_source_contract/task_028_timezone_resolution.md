# TASK_028 Timezone Resolution

- Contract: **PASS**
- Longbridge naive datetime semantic: `CONFIRMED`
- Evidence: `CONTROLLED_PROCESS_TIMEZONE_EPOCH_INVARIANT`
- SDK: `longbridge==4.5.0`
- Internal market time: `ASIA_SHANGHAI_AWARE`

Controlled calls under Asia/Tokyo and UTC returned different naive wall-clock representations with the same Unix epoch. The adapter attaches the process-local zone only to recover that instant, then derives timezone-aware Asia/Shanghai market time. Normalization verifies any emitted `market_time` against the epoch, so the host timezone cannot shift A-share period boundaries.
