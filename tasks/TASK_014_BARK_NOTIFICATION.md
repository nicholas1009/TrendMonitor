# TASK_014｜Bark iPhone Notification v0.1

- Status: `SUCCESS`
- Notification readiness: `IMPLEMENTED_AND_CHANNEL_VERIFIED`
- Rules version: `notification_policy_v0.1`
- Bark channel: one explicit `event_type=TEST` request accepted in one attempt; operator-provided Mac → Bark → iPhone channel evidence is verified.
- Policy: event-driven Market, Stock, DATA_INCOMPLETE, Runtime failure, and final Provider failure events.
- Deduplication key: event type, instrument, trading date, period end, and frozen source rules version.
- Catch-up: stale ordinary Risk events are suppressed; ERROR events remain eligible.
- Isolation: Bark failure is recorded independently and cannot change Risk Runtime status.
- Store: append-only `data/notifications/manifest.jsonl`; no Device Key or full Bark request URL is persisted.
- Security: `.env` remains `0600`; Device Key hits outside `.env` = 0.
- Regression: 237 unit/regression tests pass; frozen Market/Stock Risk results and rule hashes remain unchanged.
- Production limit: TASK_013A live trading-day acceptance is still `PENDING`; unattended launchd → Runtime → Bark → iPhone acceptance is not yet claimed.
