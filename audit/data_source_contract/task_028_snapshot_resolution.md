# TASK_028 Snapshot Resolution

- Current contract: **PASS**
- Cycle ID: `manual:2026-09-04T15:00:00+08:00`
- Cycle Raw snapshot: `cycle_raw_snapshot_v1:c424e7436da69dc0283cc20e78e602bc298c9b073af46969977d05b986eadc3f`
- Analysis as-of: `2026-09-04T15:00:00+08:00`
- Frozen members: 30 Raw members across 10 instruments
- Historical 2026-09-03 15:00 classification: `LEGACY_SNAPSHOT_IDENTITY_MISMATCH`
- 2026-09-03 15:00 saved-input replay through the frozen history members: `PASS`

Root cause: Market Coverage/Risk used a short-window Raw fetch while Stock replay/context performed a second historical-window fetch for the same period. The completed data ended at the same market boundary, but the Raw identities differed.

Resolution: the existing Risk Input Snapshot Store now freezes the exact Daily/15m/60m member paths and hashes once per cycle. Coverage, Risk Input, Market 60m, Market 15m, Stock, Current/Replay and Runtime provenance must carry the same `cycle_raw_snapshot_id`; any identity mismatch blocks the gate. The old artifact is preserved.
