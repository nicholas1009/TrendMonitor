# TASK_014A｜Bark phone notification Simplified-Chinese presentation v0.1

- Status: `PARTIAL`
- Pending reason: `PENDING_OPERATOR_CONFIRMATION`
- Phone presentation: `SIMPLIFIED_CHINESE`
- Internal protocol: `ENGLISH`
- Scope: phone notification `title` and `body` presentation only.
- Central mapping: Risk Light, Risk Direction, market internal state, stock 15-minute classification, joint flags, and Runtime failures.
- Unknown mapping: logs `UNKNOWN_TRANSLATION`; phone fallback is `状态待解释`.
- Policy immutability: event count, event type, severity, deduplication key, catch-up policy, retry behavior, and Runtime isolation remain unchanged.
- Regression: 247 unit and regression tests pass; frozen Market and Stock Risk rule and replay hashes remain unchanged.
- Bark test: one explicit Chinese `event_type=TEST` request was accepted by Bark in one attempt.
- Operator evidence: receipt of the specific Chinese test notification on the iPhone has not yet been explicitly confirmed, so `CHINESE BARK PRESENTATION = VERIFIED` is not claimed.
- Production limit: TASK_013A remains pending live trading-day evidence; notification delivery does not establish unattended production readiness.
