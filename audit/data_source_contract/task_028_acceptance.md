# TASK_028 Acceptance Criteria

These criteria are frozen before implementation. TASK_028 is complete only when every mandatory check below is supported by retained evidence; an unresolved item remains explicit rather than being forced to pass.

## Snapshot contract

- One analysis cycle has one immutable `cycle_raw_snapshot_id` and one canonical bundle hash.
- The bundle records `cycle_id`, timezone-aware `analysis_as_of`, timezone-aware `provider_observed_at`, and the frozen Daily/15m/60m Raw members used by the cycle.
- Coverage, normalization, validation, Risk Input, Market 60m, Market 15m, Stock Risk, Stock Market Context, Current, and Replay reference that same bundle without a mid-cycle Provider refresh.
- The retained 2026-09-03 15:00 artifact remains unchanged and is classified as `LEGACY_SNAPSHOT_IDENTITY_MISMATCH`.
- Replays for 2026-09-03 15:00 and all four 2026-09-04 periods prove snapshot identity, Current/Replay equality, lookahead safety, determinism, and unchanged risk results.

## Volume contract

- The six retained Daily cross-provider samples are evaluated with both `turnover / volume_raw` and `turnover / (volume_raw * 100)` against each day's `[low, high]` range.
- A Longbridge CN conversion is accepted only if factor 1 fails every sample, factor 100 passes every sample, the Hithink-to-Longbridge raw volume ratio is consistently near 100, and no retained sample is a counterexample.
- Any accepted Longbridge conversion is labeled `EMPIRICALLY_CONFIRMED_BY_DIMENSIONAL_INVARIANT`, never `OFFICIALLY_DOCUMENTED`.
- Canonical volume is `shares`; Hithink Daily is unchanged, Hithink Auction hands are multiplied by 100, and unknown units cannot be converted.
- The normalization is contract/validation metadata only unless an existing production feature requires cross-provider absolute volume; frozen risk results must not change.
- 600150 Daily cross-validation is rerun with normalized volume and recorded in a new resolution artifact without rewriting TASK_026B or TASK_027 history.
- Turnover remains `UNKNOWN` unless independently proven.

## Daily fallback contract

- Production Longbridge Daily failure cannot silently or explicitly produce a formal Risk Result from Hithink Daily while approval is absent.
- The result uses the existing unavailable/data-incomplete error path and records a fallback block reason.
- Explicit Research/Cross Validation use of Hithink Daily remains available.
- No production cross-provider fallback is approved by this task.

## Timezone contract

- Provider-adapter output timestamps are derived without dependence on the Mac host timezone and map to timezone-aware `Asia/Shanghai` market time at downstream boundaries.
- Daily trade dates and representative 15m/60m boundaries remain stable, including 09:30, 10:30, 11:30, 13:00, 14:00, and 15:00.
- Tests prove an `Asia/Tokyo` host setting cannot shift an A-share `market_period_end`.
- Any Longbridge naive-SDK-datetime conclusion states its evidence level; an unproven semantic remains provisional and is handled safely rather than guessed.

## Non-regression and safety

- `AS_OF_CONTRACT`, Current/Replay match, lookahead, determinism, TASK_025 disabled provenance, and the specified 2026-09-04 risk results pass unchanged.
- Frozen risk hashes are unchanged.
- Production risk rules, scores, lights, thresholds, scheduler, Auction grace, Notification Policy, Livermore rules, and 600150 research rules are not modified.
- Full tests, runtime health, replay checks, `git diff --check`, secret scan, and public-safety scan pass.
- No Raw bulk data, runtime/log data, workbook, credentials, or private research evidence is committed.
- The Production-Parity Harness is not implemented; readiness is reported only after these prerequisites are evaluated.
