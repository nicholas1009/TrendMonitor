# TASK_013B｜LaunchAgent restart recovery diagnosis and minimal fix

- Status: `SUCCESS`
- Implementation: `VERIFIED`
- Restart recovery: `VERIFIED_2026-09-03`
- Root cause: `USER_GUI_SESSION_ABSENT_AT_ALL_SCHEDULED_TRIGGERS`
- Sleep hypothesis: `REJECTED`

## Evidence

- Boot: 2026-08-31 06:32:36 JST.
- Installed plist Birth/Modify/Change: 2026-08-30 19:17:49 JST, before the boot.
- Console sessions: 06:32–07:33 JST and 07:36–08:43 JST; no console GUI session existed from 08:43 until 20:08 JST.
- A-share scheduled triggers: 10:33, 11:33, 14:03, 15:03 Asia/Shanghai, equal to 11:33, 12:33, 15:03, 16:03 JST.
- All four triggers occurred while the user GUI session was absent. System wakefulness does not create a User LaunchAgent GUI domain.
- pmset true Sleep/Wake events on 2026-08-31: 0.
- Plist syntax and permissions: PASS / 0644; label, WorkingDirectory, Program, RunAtLoad, and StartInterval are valid.
- Disabled state: enabled.
- Current console login: 20:08 JST; first known Launchd Runtime invocation: 20:09:58 JST.
- Current LaunchAgent: loaded, run interval 60 seconds, last exit code 0.
- Manual CATCH_UP recovery began at 20:43:13 JST and is not LIVE evidence.
- Unified Log retained no label-specific lifecycle entry for the queried interval; exact early-session bootstrap time is therefore not claimed.

## Minimal fix

- Added a secret-free heartbeat written at the start of every launchd Runner invocation.
- Health Check now reports plist existence, loaded and disabled states, last exit code, run count, interval, WorkingDirectory, Program existence, Runtime log writability, heartbeat, and latest Launchd Runtime observation.
- An installed but unloaded agent now reports `LAUNCH_AGENT_NOT_LOADED`.
- No bootstrap, bootout, enable, disable, or kickstart was performed as part of the fix.
- No Risk rule, Risk algorithm, Notification Policy, historical result, Runtime Manifest, or Notification Store was modified or deleted.

## Real restart acceptance

1. Record current Boot time and installed plist hash/mtime.
2. Restart the Mac and log in to the user GUI session.
3. Do not run install, bootstrap, enable, or kickstart.
4. Keep the user logged in and wait at least two 60-second intervals.
5. Run `uv run python scripts/check_runtime_health.py`.
6. Require loaded=true, disabled=false, matching Program/WorkingDirectory/interval, and a heartbeat newer than both Boot and console login.
7. Only then record `RESTART_RECOVERY = VERIFIED`.

TASK_013A remains pending a successful end-to-end `LIVE_SCHEDULED` trading-day run.

## 2026-09-03 restart verification

- Boot: 2026-09-03 06:37 JST; console login: 06:38 JST.
- Installed plist hash and mtime match the pre-restart baseline.
- LaunchAgent remained loaded and automatically triggered the 10:33, 11:33,
  14:03, and 15:03 Runtime invocations without bootstrap or kickstart.
- The Risk Pipeline failed after LaunchAgent invocation; this verifies only
  restart recovery of the scheduling host. TASK_013A remains pending.
