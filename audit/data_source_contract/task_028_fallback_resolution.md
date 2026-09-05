# TASK_028 Daily Fallback Resolution

- Production canonical Daily: `Longbridge`
- Hithink Daily production fallback: `BLOCKED_PENDING_CONTRACT_VALIDATION`
- Silent fallback: `NO`
- Explicit Hithink Daily research/cross-validation: `ALLOWED`

If Longbridge Daily is unavailable, production Risk Input remains blocked through the existing data-unavailable/preflight path and records a fallback block reason. This task does not approve cross-provider production fallback.
