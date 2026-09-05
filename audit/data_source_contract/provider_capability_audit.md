# Provider Capability Audit

| Provider | Capability | Production use observed | Status | Evidence |
| --- | --- | --- | --- | --- |
| Hithink | A-share trading calendar | Runtime calendar cache | CONFIRMED | `/api/a-share/calendar/trading-days`; cached response retains provider metadata |
| Hithink | Auction final | 2026-09-03/04 closed/final CATCH_UP raw snapshots | CONFIRMED | `/api/a-share/auction/snapshot`, `stage=final` |
| Hithink | Daily/Quote | Validation/research and explicit Daily fallback candidate | CONFIRMED capability / fallback QUESTION | Official endpoint schema and adapter |
| Hithink | 15m/60m | Not supported by current adapter | NOT_IMPLEMENTED | Adapter raises unsupported capability |
| Longbridge | Daily | Production/research Direct Daily, NoAdjust | CONFIRMED | Adapter/provider code and retained raw requests |
| Longbridge | 15m/60m | All eight audited intraday results | CONFIRMED | Result → risk input → raw trace |
| Longbridge | Quote | Current quote/research query path | CONFIRMED capability | Provider code and official quote schema |

600150.SH is limited to research/shadow evidence. It is not a production Risk instrument.
