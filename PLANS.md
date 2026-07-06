# herdr Amphetamine macOS sleep-prevention plugin ExecPlan

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This file is written in the style described by OpenAI's PLANS.md guidance for Codex execution plans: it is self-contained, outcome-focused, and intended to let a future agent or human implement the plugin without relying on this conversation.

**Revision 2026-07-05 (redesign):** the architecture is reorganized around a
**resident LaunchAgent daemon** supervised by an **interactive TUI**. The
previous "command-started background monitor" model is superseded. Verified
foundations (the Amphetamine AppleScript wrappers, the hysteresis state machine,
the closed-display-mode correction, short-session extension) are **retained**;
only the control surface and process topology change. See *Revision Notes*.

## Purpose / Big Picture

After this work is implemented, a macOS user who runs AI coding agents inside herdr will be able to close the MacBook display or leave the machine unattended while agents are working, without manually starting Amphetamine each time. The plugin observes herdr agent activity and asks Amphetamine to keep the machine awake only while at least one agent is working. When all relevant agents become idle, done, blocked, or disappear for a grace period, the plugin releases its Amphetamine session so normal sleep behavior resumes.

The user-visible proof is simple: start a herdr agent, observe that Amphetamine has an active session and `pmset -g assertions` shows sleep-prevention assertions; let the agent stop working, observe that the Amphetamine session ends and the assertions disappear.

The architecture has two cooperating parts:

1. **A resident LaunchAgent daemon** (`com.herdr.amphetamine.monitor`). This is
   Apple's per-user background-service mechanism; it runs as the logged-in user
   (no admin password on every start), starts at login, and is kept alive by
   `KeepAlive`. It is the **always-on supervisor** — the only process that owns
   the Amphetamine session lifecycle.
2. **An interactive TUI** (`scripts/tui.py`) that the operator uses to **arm /
   disarm (start / pause)** the guard, **change settings**, and **view live
   status**. The TUI never owns the Amphetamine session; it commands the daemon
   through shared files and renders the daemon's state.

This split means the guard keeps working after the terminal closes and across
reboots (the LaunchAgent), while all human control happens in one keyboard-driven
screen (the TUI).

## Architecture

```
                 ┌─────────────────────────────────────────────────────┐
   launchd       │  LaunchAgent  com.herdr.amphetamine.monitor          │
   (login,       │  RunAtLoad + KeepAlive  ──►  monitor.py --daemon     │
   KeepAlive)    └───────────────────────┬─────────────────────────────┘
                                         │ polls every cycle reads/writes
                                         ▼
            ┌──────────────────────────────────────────────────────┐
            │  State dir: ~/Library/Application Support/herdr-amphetamine/ │
            │                                                      │
            │   config.json  ◄── intent/settings (TUI-editable)    │
            │      armed, poll, grace, session, paths, closed-display     │
            │   state.json   ◄── runtime (monitor_state, owned_session,   │
            │      last_error, last_transition)  [written by daemon]      │
            └──────────────────────────────────────────────────────┘
                  ▲                                  ▲
                  │ atomic write                     │ read
            ┌─────┴──────────────────┐      ┌────────┴───────────────┐
            │  TUI  scripts/tui.py    │      │  daemon reconcile loop │
            │  (curses, keyboard)     │      │  herdr agent list ──►  │
            │                         │      │  Amphetamine ctl       │
            │  • arm / disarm (pause) │      │  (start/end/extend)    │
            │  • edit settings        │      │  (ownership stays here)│
            │  • live status          │      └────────────────────────┘
            │  • install/uninstall LA │
            │  • tail events          │
            └─────────────────────────┘
```

**Control flow.**

- The daemon reads `config.json` **every poll cycle** (cheap, atomic read). It
  treats `armed` as the master switch and the numeric fields as the live
  configuration. Env vars still override file values when set (for the LaunchAgent
  bootstrap and power users); the precedence is **env var > config.json > built-in
  default**.
- **Arm/disarm (start/pause) is logical, not a process kill.** When `armed=false`,
  the daemon releases any owned Amphetamine session, sets `monitor_state="off"`,
  and idles without acting — but the process stays resident (LaunchAgent
  `KeepAlive`). When re-armed, normal hysteresis behavior resumes. This honors
  "the monitor process stays resident via LaunchAgent" while "start / pause" is a
  TUI action.
- The TUI **only** reads `state.json` + queries Amphetamine read-only, and
  **writes** `config.json`. It never calls `start new session` / `end session`
  directly, so session ownership semantics remain entirely in the daemon.
- IPC is **shared files only** — no sockets, no Mach ports. The daemon polls
  `config.json` each cycle; the TUI polls `state.json` + Amphetamine each render
  (≈1 Hz). A `SIGHUP` to the daemon is an optional "reload config now" nicety
  (otherwise the next poll picks up the change within `poll_seconds`).

## Progress

### Verified foundations (retained from prior work — 2026-07-05)

- [x] Captured the Amphetamine AppleScript verbs from the installed dictionary:
  `session is active`, `start new session`, `end session`, `enable closed display
  mode`, `disable closed display mode`, `session time remaining`.
- [x] `scripts/amphetamine_ctl.py` wraps each verb in one auditable function and
  is unit-tested (start/end/active/closed-display, focus-preserving start,
  activate fallback).
- [x] The hysteresis state machine (`off / pending_on / on / pending_off / error`)
  and `handle_transition` are unit-tested: sustained working starts, sustained
  idle stops, flicker is absorbed, resume/cancel are no-ops, pre-existing
  sessions are not owned, silent-no-op start drops to `off`. `46 tests pass`.
- [x] `herdr agent list` emits JSON with `result.agents[].agent_status` (`working`
  /`idle`/`done`/`blocked`/`unknown`); `monitor.get_agent_statuses` parses it
  directly. Automation permission for osascript→Amphetamine is granted.
- [x] Real Amphetamine control proven on AC power: `start_session` (activate-less
  first, fallback to activate) → `is_session_active=true` with pmset
  `PreventUserIdleSystemSleep`/`PreventUserIdleDisplaySleep`; `end_session` clears
  them. `enable closed display mode` returns `true` (the inverted-verb correction
  is verified).
- [x] herdr 0.7.x injects `HERDR_BIN_PATH` (and `HERDR_PLUGIN_ID`, context vars)
  but **not** `HERDR_PLUGIN_STATE_DIR`/`HERDR_PLUGIN_ROOT`. State path resolves as
  `HERDR_AMPHETAMINE_STATE_DIR` env → `~/Library/Application Support/herdr-amphetamine/`.
- [x] Battery-power caveat documented: Amphetamine refuses `start new session` on
  battery unless its "Allow sessions while on battery power" preference is on. The
  daemon reconciles the on-state every poll so a silently-ignored start does not
  leave a stale `owned_session`.

### Redesign work (2026-07-05)

- [x] Promote the LaunchAgent from "optional" to the **primary, always-on
  supervisor**. `KeepAlive=true`, `RunAtLoad=true`. The `--start`/`--stop`
  detached-background code path in `monitor.py` is removed; `status_pane.py` is a
  deprecated shim that forwards to `tui.py`.
- [x] Add **`config.json`** as the persistent, TUI-editable settings store; move
  tunables out of the plist's `EnvironmentVariables` (the plist keeps only
  bootstrap: `HERDR_BIN_PATH` absolute, `HERDR_AMPHETAMINE_STATE_DIR`, python
  interpreter, log/state paths).
- [x] Add the **`armed` flag** to `config.json` and teach `iterate()` to honor it:
  `armed=false` → force `off`, end owned session, idle; `armed=true` → normal.
  `armed` persists so a pause survives daemon restarts.
- [x] Implement **`scripts/tui.py`** — an interactive curses UI (arm/disarm, edit
  each setting, live status, install/uninstall LaunchAgent, tail recent events).
  Stdlib-only; degrades to a non-interactive status print if the terminal lacks
  curses support or stdin/stdout are not a TTY.
- [x] Expose a single herdr plugin action/pane that **opens the TUI**. Keep
  `install-launchagent` / `uninstall-launchagent` and `status`. Dropped the
  now-redundant `start-monitor` / `stop-monitor` / `open-status-pane` actions.
- [x] Update `herdr-plugin.toml` (v0.2.0), README, `docs/manual-test.md`, and the
  plist template to the new model. `herdr-plugin.toml` validates; the rendered
  plist passes `plutil -lint`.
- [x] Add tests for armed/disarmed behavior, `config.json` load/save + validation,
  and env-over-file precedence. Suite is green: **71 passed, 0 failed**.
      (`python3 -m unittest discover -s tests -v`; also `py_compile` clean on all
      scripts, and smoke-checked `--status` / `--once` / config round-trip / TUI
      non-TTY fallback / plist render on the target Mac.)
- [ ] Manual on-Mac validation per *Validation and Acceptance*: resident daemon
  survives logout/reboot, TUI arm/disarm changes live behavior, TUI config edit
  takes effect without reinstalling the LaunchAgent. **Left to the operator.**

## Surprises & Discoveries

- Observation: macOS `caffeinate` is not enough for this use case because the desired behavior includes staying awake when the display/lid is closed. Amphetamine has a separate closed-display-mode feature exposed through AppleScript.
  Evidence: `man caffeinate` documents idle/system/display sleep assertions but does not provide a lid-closed override. Amphetamine's scripting dictionary contains `enable closed display mode` and `disable closed display mode`.

- Observation: Amphetamine has a real AppleScript interface, so the plugin should not drive the UI with clicks or Accessibility automation.
  Evidence: running `sdef /Applications/Amphetamine.app` on the target machine showed commands named `session is active`, `start new session`, `end session`, `enable closed display mode`, and `disable closed display mode`.

- Observation: Herdr plugins are executable packages rather than sandboxed SDK extensions. They can run scripts and call the full herdr CLI.
  Evidence: Herdr plugin documentation states that a plugin is a directory with `herdr-plugin.toml`, and that the Herdr CLI is the plugin API. Runtime commands receive environment variables such as `HERDR_BIN_PATH`, `HERDR_PLUGIN_ID`, `HERDR_PLUGIN_ACTION_ID`, `HERDR_PLUGIN_CONTEXT_JSON`, `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`, `HERDR_PANE_ID`, and `HERDR_SOCKET_PATH`.

- **Correction (2026-07-05): the closed-display verb was inverted in an earlier draft.** Amphetamine's "closed-display mode" means *keep the Mac awake while the lid is closed*. The sdef says `enable closed display mode` = "allow closed-display mode" and `disable closed display mode` = "prevent closed-display mode". To keep the system awake when the display is closed, the plugin must call **`enable closed display mode`**, not `disable`. `prevent_sleep_when_display_closed()` in `scripts/amphetamine_ctl.py` therefore calls `enable closed display mode`, guarded by a regression test (`test_uses_enable_not_disable`). Enabling can surface a one-time warning prompt; see `docs/manual-test.md`.

- Observation: `herdr agent list` prints JSON by default on herdr 0.7.1-preview (no `--json` flag; passing it errors). The envelope is `{"id":"cli:agent:list","result":{"agents":[{"agent_status":...}], "type":"agent_list"}}`. The status field is `agent_status` (not `status`).
  Evidence: `herdr agent list` on the target machine returned 7 agents including `working`, `idle`, and `done`.

- Observation: herdr 0.7.x does **not** inject `HERDR_PLUGIN_ROOT`, `HERDR_PLUGIN_CONFIG_DIR`, or `HERDR_PLUGIN_STATE_DIR`. Because the daemon also runs as a LaunchAgent (outside any plugin action), it cannot rely on herdr-injected state paths. State resolves as `HERDR_AMPHETAMINE_STATE_DIR` → `~/Library/Application Support/herdr-amphetamine/`. The LaunchAgent pins the former.
  Evidence: `strings` on `/Users/ama/.local/bin/herdr` and `herdr plugin action invoke --help`.

- Observation: a user LaunchAgent runs with a minimal PATH that does not include `~/.local/bin`, so the installer resolves herdr to an absolute path and bakes it into the plist as `HERDR_BIN_PATH`. This stays true in the redesign; the plist still pins `HERDR_BIN_PATH` even though tunables move to `config.json`.
  Evidence: first install logged repeated "herdr unavailable"; after the fix the daemon logged `Transition: off -> pending_on`.

- Observation: Amphetamine silently no-ops `start new session` until activated. `start_session` tries activate-less first (focus-preserving), then falls back to `activate` + settle and retries. Matters for the headless LaunchAgent.
  Evidence: AC-power test — activate-less then fallback produced `is_session_active=true` and two pmset assertions; without activate, `session time remaining = -3`.

- Observation: on **battery power**, Amphetamine refuses to start a session via AppleScript even with `activate`. `iterate()` reconciles the on-state every poll so a silently-ignored start drops to `off` rather than leaving `owned_session=True`.
  Evidence: battery test — all start variants returned `session time remaining = -3`; `test_drops_to_off_when_start_silently_ignored` covers it.

- **Observation (2026-07-05, redesign): LaunchAgent `KeepAlive` means `launchctl bootout`/uninstall is the only clean way to stop the daemon process.** "Pause" therefore must not be modeled as killing the process — launchd would immediately restart it. Modeling pause as an `armed=false` flag (the daemon stays alive but idle) is both what the user asked for and what `KeepAlive` forces. This is why arm/disarm is a *logical* switch, not a process control.
  Evidence: `KeepAlive=true` in the plist; `launchctl print` shows `properties = keepalive | runatload`.

## Decision Log

- Decision: Use Amphetamine AppleScript commands instead of `caffeinate`.
  Rationale: The user specifically needs the machine to stay awake when the display is closed. Amphetamine already provides this behavior; `caffeinate` is only reliable for idle sleep.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Run the monitor as a macOS user LaunchAgent.
  Rationale: A LaunchAgent is the standard macOS mechanism for a per-user background process. It starts at login, runs without repeated administrator prompts, and supervises a long-running monitor independently of any herdr pane.
  Date/Author: 2026-07-04 / Hermes Agent
  **(2026-07-05 redesign): promoted from "one option" to the primary, always-on supervisor.**

- Decision: Prefer polling `herdr agent list` for the first implementation.
  Rationale: Polling is easy to validate end-to-end and does not depend on exact event names. A conservative 5 s interval is not latency-critical. The same poll cadence naturally reloads `config.json`, so arm/disarm and setting changes take effect within one cycle with no extra IPC.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Treat `working` as the only state that actively requires Amphetamine.
  Rationale: `idle`, `done`, `blocked`, `unknown` should not hold the machine awake indefinitely. This avoids accidentally preventing sleep forever.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Implement hysteresis with configurable grace periods.
  Rationale: Agent status flickers; the plugin must not rapidly toggle Amphetamine. Start/stop grace make behavior stable.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Call `enable closed display mode` (not `disable`) to keep the Mac awake when the display is closed.
  Rationale: "closed-display mode" is the keep-awake-while-closed feature; the sdef defines `enable` as "allow". An earlier draft inverted this.
  Date/Author: 2026-07-05 / implementer

- Decision: Resolve state under `HERDR_AMPHETAMINE_STATE_DIR` → `~/Library/Application Support/herdr-amphetamine/`, not `HERDR_PLUGIN_STATE_DIR`.
  Rationale: herdr 0.7.x does not inject plugin state paths, and the daemon runs both as a plugin action and as a standalone LaunchAgent.
  Date/Author: 2026-07-05 / implementer

- Decision: Default to short top-ups: add 1 minute when remaining time is below 2 minutes.
  Rationale: A short session that is repeatedly topped up keeps the Mac awake continuously while agents work, but naturally expires quickly if the monitor ever stops extending (crash, uninstall, bug). `HERDR_AMPHETAMINE_TOP_UP_MINUTES=0` restores infinite.
  Date/Author: 2026-07-05 / implementer

- Decision: Make `start_session` focus-preserving (activate only as a fallback).
  Rationale: Unconditional `activate` stole foreground focus on every start. Activate-less first, with `is_session_active()` verification and a one-shot activate fallback, avoids focus theft in the common case while remaining reliable.
  Date/Author: 2026-07-05 / implementer

- **Decision (2026-07-05, redesign): LaunchAgent is the primary, always-on supervisor; the command-started background monitor is removed.**
  Rationale: The user asked for the monitor process to be resident via LaunchAgent and controlled through a TUI. A `KeepAlive` LaunchAgent is the supervisor; the previous `--start`/`--stop` detached-process model duplicated that role and split ownership across two control surfaces. One resident daemon, one control surface (the TUI).
  Date/Author: 2026-07-05 / implementer

- **Decision (2026-07-05, redesign): "Start / pause" means arm / disarm a logical guard flag, not start / stop the OS process.**
  Rationale: `KeepAlive` would immediately restart a killed daemon, so process-level pause is meaningless. An `armed` boolean in `config.json` lets the daemon stay resident while releasing its owned session and idling when disarmed, and resuming when re-armed. This is exactly "resident via LaunchAgent" + "start/pause via TUI". Persisting `armed` makes a pause survive daemon restarts.
  Date/Author: 2026-07-05 / implementer

- **Decision (2026-07-05, redesign): Persistent settings live in `config.json`, not plist `EnvironmentVariables`.**
  Rationale: The TUI must change settings without reinstalling the LaunchAgent. The plist keeps only bootstrap environment (`HERDR_BIN_PATH` absolute, `HERDR_AMPHETAMINE_STATE_DIR`, interpreter, log/state paths). Tunables (poll, grace, top-up amount, top-up threshold, closed-display, app paths, `armed`) live in `config.json` and are re-read every poll. Env vars override the file when set, preserving power-user escape hatches.
  Date/Author: 2026-07-05 / implementer

- **Decision (2026-07-05, redesign): The TUI is interactive curses (stdlib) and never owns the Amphetamine session.**
  Rationale: "TUI" means keyboard-driven control, not the prior read-only pane. curses ships with macOS system Python (no third-party deps). To preserve ownership correctness, only the daemon calls `start new session` / `end session`; the TUI writes `config.json` (intent) and reads `state.json` + Amphetamine read-only. IPC is shared files, polled — no sockets.
  Date/Author: 2026-07-05 / implementer

- **Decision (2026-07-05, redesign): Default `armed=true` on first install; the TUI's primary affordance is an ARM/DISARM toggle.**
  Rationale: The plugin's purpose is to guard autonomously; an operator who installs it expects the machine to stay awake while agents work without an extra step. Defaulting disarmed would surprise users who "installed it and nothing happens." The TUI makes pause one keystroke, and an operator who wants manual-only can set `armed=false` in `config.json` (or via the TUI) — that intent persists. Flagged here because the request emphasized "start via TUI"; if the operator prefers opt-in guarding, flip the default in `config.py`.
  Date/Author: 2026-07-05 / implementer

- **Decision (2026-07-05, redesign): Implement the resident daemon and TUI in Python, not Rust.**
  Rationale: Rust would be ~5–10× lighter in memory (~1–3 MB vs Python's ~12–18 MB RSS), but for a single user-level daemon that sleeps at 0% CPU between 5 s polls, the absolute cost is immaterial on a modern Mac. The dominant cost is spawning `herdr`/`osascript` every poll, which is language-independent; the real efficiency lever (if ever needed) is the `HERDR_SOCKET_PATH` persistent connection or a longer poll, not the daemon language. Rust would add a build toolchain, architecture-specific binaries (aarch64/x86_64) + signing, TUI crate dependencies (breaking the stdlib-only principle), loss of script auditability, and full re-validation of the verified Amphetamine quirks. Switching to native IOKit assertions (the one path where Rust's native access helps) would risk the headline closed-display feature. Python keeps zero-dependency deployment, auditable scripts, and the already-verified logic.
  Date/Author: 2026-07-05 / implementer

## Outcomes & Retrospective

Verified on the target Mac (2026-07-05, AC power) and **retained** through the
redesign:

- `python3 -m unittest discover -s tests -v` → **46 passed, 0 failed** (state
  machine + AppleScript wrappers).
- `herdr plugin link` accepted the manifest; `herdr plugin list --json` shows the
  plugin enabled.
- Real Amphetamine control via the actual code path: `start_session` →
  `is_session_active=true` and pmset `PreventUserIdleSystemSleep` /
  `PreventUserIdleDisplaySleep` for pid 862(Amphetamine); `end_session` clears
  them. `enable closed display mode` → `true`.
- The rendered LaunchAgent plist passes `plutil -lint`.
- End-to-end (LaunchAgent installed): `off -> pending_on -> on`, an owned session
  started, pmset assertions appeared; the stop-grace path is unit-tested.

Not yet redone under the new architecture (pending redesign work above):

- Resident-daemon + TUI end-to-end: arm/disarm via TUI changes live behavior;
  config edit via TUI takes effect without reinstalling the LaunchAgent; daemon
  survives logout/reboot and re-arms from persisted `config.json`.
- New tests for armed/disarmed, `config.json` load/save/validation, env-over-file
  precedence, hot-reload.

Retrospective notes:

- The single most important catch historically was the inverted closed-display
  verb; the redesign does not touch that verified logic.
- Modeling pause as a logical `armed` flag (not a process kill) is forced by
  `KeepAlive` and is also the cleanest UX — a happy coincidence worth recording.
- Keeping all Amphetamine session ownership in the daemon (TUI writes intent
  only) means the redesign adds a control surface without re-deriving the
  ownership/state-machine correctness already covered by tests.

## Context and Orientation

This plan concerns four systems.

Herdr is a terminal workspace manager for AI coding agents. A herdr workspace contains tabs and panes. A pane can contain an agent. Herdr exposes agent status through its CLI. The relevant status values are `working`, `idle`, `blocked`, `done`, and `unknown`. In this plan, an "agent is running" means at least one observed herdr agent is in the `working` state. Herdr panes are PTY-backed terminals, so an interactive (curses) program can run inside one.

Amphetamine is a macOS application that can keep the computer awake. The user's installed Amphetamine supports AppleScript, driven through `osascript`. The relevant verbs are `session is active`, `start new session` (options `{duration, interval, displaySleepAllowed}`; `duration:0, interval:0` = infinite), `end session`, `session time remaining`, and `enable` / `disable closed display mode`.

LaunchAgent is macOS's user-level background process manager. A plist under `~/Library/LaunchAgents/` runs a command at login as the current user, no root required. With `RunAtLoad=true` and `KeepAlive=true`, launchd keeps the daemon resident across crashes, logout, and reboot. It is the supervisor for this plugin.

curses is Python's standard terminal-UI library (binding to ncurses, shipped with macOS system Python). It is used for the interactive TUI: keyboard input, a fixed-layout screen, and in-place redraw. It is stdlib-only; no third-party dependency.

The future repository layout assumed by this plan:

    herdr-amphetamine-macos/
      herdr-plugin.toml
      README.md
      scripts/
        amphetamine_ctl.py     # AppleScript wrappers (unchanged, verified)
        monitor.py             # daemon loop + state machine; now honors `armed`,
                               #   reads config.json each cycle
        config.py              # config.json load/save/validate (new)
        tui.py                 # interactive curses UI (new)
        install_launchagent.py # renders + loads the LaunchAgent (updated plist)
        uninstall_launchagent.py
      launchagents/
        com.herdr.amphetamine.monitor.plist.template
      tests/
        test_state_machine.py
        test_amphetamine_ctl.py
        test_config.py         # config.json + armed/disarmed (new)
      docs/
        security.md
        manual-test.md

If the implementer chooses a different repository root, keep the same internal paths relative to that root unless there is a strong reason to change them. Update this ExecPlan if paths change.

## Plan of Work

Keep the verified modules (`amphetamine_ctl.py`, the state-machine core of `monitor.py`) and reorganize the control surface.

1. **Introduce `config.json` and `scripts/config.py`.** Define a schema (see
   *Interfaces and Dependencies*) covering `armed` plus all tunables and paths.
   `config.py` provides `default_config()`, `load_config(path)` (merge file →
   defaults), `apply_env_overrides(cfg)` (env var wins when set), `validate(cfg)`
   (clamp graces ≥ 0, session ≥ 0, etc.), and `save_config(path, cfg)` (atomic).
   The plist no longer hardcodes tunables; it sets only
   `HERDR_BIN_PATH`, `HERDR_AMPHETAMINE_STATE_DIR`, the interpreter, and log/state
   paths. On first run, if `config.json` is absent, write defaults (`armed=true`).

2. **Teach `monitor.py` to honor `armed` and hot-reload config.** `run_daemon`
   reloads `config.json` each cycle; `iterate()` checks `armed`: if false, end any
   owned session, set `monitor_state="off"`, log "guard disarmed", and skip the
   normal transition logic; if true, run the existing state machine. Persist
   nothing new in `state.json` for armed (armed is intent → `config.json`); keep
   `state.json` for runtime only.

3. **Remove the command-started background model.** Delete `start_background` /
   `stop_background` / the `--start` / `--stop` modes and `monitor.pid` handling.
   Keep `--daemon` (LaunchAgent), `--status` (one-shot read-only print), and
   `--once` (manual single iteration). The LaunchAgent is the only long-running
   invocation.

4. **Implement `scripts/tui.py`** (curses, stdlib). Screens/sections:
   - **Header:** ARMED/DISARMED badge + monitor state (ON / ON (starting) / ON
     (stopping) / OFF / ERROR), working/total agents, session active + owned,
     session length, last error.
   - **Settings:** one line per tunable with a hotkey to edit it (prompt for a
     new value, validate, atomically rewrite `config.json`). Include the
     `armed` toggle here too.
   - **Recent:** tail of `~/Library/Logs/herdr-amphetamine/monitor.out.log`
     (last N transition lines).
   - **Footer/keybindings:** `[Space]` arm/disarm, `[q]` quit, `[r]` reload/refresh,
     `[i]` install LaunchAgent, `[u]` uninstall, `[L]` full log, `[?]` help.
   - Refresh ≈1 Hz by re-reading `state.json` + querying Amphetamine read-only.
     Writes go only to `config.json`. If `curses` is unavailable or not a TTY,
     fall back to printing the same status block non-interactively (reuse
     `monitor.print_status`).
   - After writing `config.json`, optionally send `SIGHUP` to the daemon (best
     effort) so the change applies immediately instead of waiting one poll.

5. **Update `herdr-plugin.toml`.** Replace the old action set with:
   - `tui` — open the interactive TUI (primary; opens a herdr pane).
   - `install-launchagent` / `uninstall-launchagent` — first-time setup / removal.
   - `status` — non-interactive one-shot print (kept for scripting).
   - Declare the `tui` entrypoint as a pane so `herdr plugin pane open` works.
   Drop `start-monitor` / `stop-monitor` / `open-status-pane`.

6. **Update the plist template** so `EnvironmentVariables` contains only
   bootstrap vars (above). Keep `RunAtLoad=true`, `KeepAlive=true`,
   `ThrottleInterval=10`, and the log paths.

7. **Update README and `docs/manual-test.md`** to the new model: install the
   LaunchAgent once, then drive everything from the TUI. Document arm/disarm,
   config editing, persistence, and the `armed=true` default.

8. **Tests.** Extend `tests/`: `test_config.py` for load/save/validate/env-over-
   file; extend `test_state_machine.py` for `armed=false` forcing `off` (and
   ending owned) and `armed=true` resuming; add a hot-reload test (config changes
   between cycles take effect). Keep all existing tests green.

9. **Manual on-Mac validation** per *Validation and Acceptance*.

## Concrete Steps

All commands run from the plugin directory (`herdr-amphetamine-macos/` under the
repo root).

```sh
cd "/Users/ama/herdr_plugin_Amphetamine_macos(仮)/herdr-amphetamine-macos"
```

Create `scripts/config.py` with the schema and helpers in *Interfaces and
Dependencies*. Example shape:

```python
DEFAULT_CONFIG = {
    "armed": True,
    "poll_seconds": 5.0,
    "start_grace_seconds": 5.0,
    "stop_grace_seconds": 30.0,
    "top_up_minutes": 1.0,              # 0 = infinite
    "top_up_threshold_minutes": 2.0,
    "herdr_bin_path": None,         # None -> HERDR_BIN_PATH env -> `which herdr`
    "amphetamine_app_path": "/Applications/Amphetamine.app",
    "prevent_closed_display_sleep": True,
    "display_sleep_allowed": False,
}
```

Modify `monitor.py`:

- `load_config()` becomes: read `config.json` (via `config.load_config`),
  apply env overrides, return a `Config` dataclass. Resolve `herdr_bin` as
  `config.herdr_bin_path` → `HERDR_BIN_PATH` → `shutil.which("herdr")` → `"herdr"`.
- `iterate(cfg, ctx, now)`: after computing `working`, if `not cfg.armed`:
  if `ctx.owned_session`, call `end_session()` (best-effort), set
  `owned_session=False`, `monitor_state="off"`, `last_transition=now`, log
  `"Guard disarmed; released owned session."` (or `"Guard disarmed; idle."` if
  nothing owned); return early. Otherwise proceed with the existing logic.
- Remove `start_background`, `stop_background`, `_pid_file`, `_is_running`, the
  `--start` / `--stop` / `--open-pane` argparse options, `open_status_pane`, and
  the `PLUGIN_ID`/`PANE_ENTRYPOINT` constants.
- In `run_daemon`, reload `config.json` each loop (so edits take effect); install
  a `SIGHUP` handler that forces an immediate reload on the next cycle.
- Keep `--status` and `--once`; remove `status_pane.py` (its rendering is absorbed
  into `tui.py`; keep a non-interactive fallback).

Implement `scripts/tui.py` (curses). Structure:

- `main()` initializes curses, sets up signal handlers (SIGTERM/SIGINT → clean
  exit), enters a loop that (a) reads `state.json` + `config.json`, (b) queries
  Amphetamine read-only (`is_session_active`, `is_amphetamine_available`), (c)
  tails the last N log lines, (d) renders the layout, (e) handles one keystroke.
- Editing a setting: prompt inline (a simple curses text box or a read-line loop),
  validate via `config.validate`, then `config.save_config(...)` and optionally
  `SIGHUP` the daemon.
- Non-TTY / no-curses fallback: print `monitor.print_status(cfg)` once and exit.

Update `launchagents/com.herdr.amphetamine.monitor.plist.template`:

```xml
<key>EnvironmentVariables</key>
<dict>
  <key>HERDR_BIN_PATH</key>
  <string>__HERDR_BIN__</string>
  <key>HERDR_AMPHETAMINE_STATE_DIR</key>
  <string>__HOME__/Library/Application Support/herdr-amphetamine</string>
</dict>
```

(Tunables are gone — they live in `config.json`. `ProgramArguments` still runs
`__PYTHON__ __PLUGIN_ROOT__/scripts/monitor.py --daemon`.)

Update `herdr-plugin.toml`:

```toml
id = "local.amphetamine-macos"
name = "Amphetamine macOS Sleep Guard"
version = "0.2.0"
min_herdr_version = "0.7.0"
description = "Resident LaunchAgent keeps macOS awake with Amphetamine while herdr agents work; controlled via an interactive TUI."
platforms = ["macos"]

[[actions]]
id = "tui"
title = "Open Amphetamine Sleep Guard (TUI)"
contexts = ["workspace"]
command = ["python3", "scripts/tui.py"]

[[actions]]
id = "status"
title = "Print Amphetamine monitor status"
contexts = ["workspace"]
command = ["python3", "scripts/monitor.py", "--status"]

[[actions]]
id = "install-launchagent"
title = "Install the resident monitor LaunchAgent"
contexts = ["workspace"]
command = ["python3", "scripts/install_launchagent.py"]

[[actions]]
id = "uninstall-launchagent"
title = "Uninstall the monitor LaunchAgent"
contexts = ["workspace"]
command = ["python3", "scripts/uninstall_launchagent.py"]

[[panes]]
id = "tui"
title = "Amphetamine Sleep Guard"
placement = "overlay"
command = ["python3", "scripts/tui.py"]
```

Tests:

```sh
python3 -m unittest discover -s tests -v
```

Expected new/updated tests: `test_disarmed_forces_off_and_ends_owned`,
`test_disarmed_with_no_owned_is_noop`, `test_armed_resumes_normal`,
`test_config_load_save_roundtrip`, `test_config_validate_clamps`,
`test_env_overrides_file`, `test_hot_reload_picks_up_new_grace`.

End-to-end manual validation:

```sh
python3 scripts/install_launchagent.py
launchctl print gui/$UID/com.herdr.amphetamine.monitor
python3 scripts/tui.py            # or: herdr plugin action invoke tui
# Inside the TUI: press Space to disarm, observe the session end; Space again to arm.
# Edit a setting (e.g. stop grace), quit, confirm it persisted in config.json.
tail -n 50 ~/Library/Logs/herdr-amphetamine/monitor.out.log
```

## Validation and Acceptance

The implementation is acceptable only if it produces observable behavior.

1. **Resident daemon.** After install, `launchctl print gui/$UID/com.herdr.amphetamine.monitor`
   shows `state = running`, `properties = keepalive | runatload`. The log shows a
   startup line and periodic observations. Survives a `launchctl kill SIGTERM` (
   launchd restarts it within `ThrottleInterval`).

2. **TUI control — arm/disarm.** With agents working and the guard armed, the
   daemon holds an owned session (pmset assertions present). Press Space in the
   TUI to disarm; within one poll the owned session ends (assertions cleared,
   `monitor_state=off`) and the badge shows DISARMED. Press Space to arm; within
   start-grace the session restarts.

3. **TUI control — config edit.** Edit `stop_grace_seconds` in the TUI, quit. The
   value persists in `config.json` and the daemon honors it on the next cycle
   (verifiable by watching the stop-grace timing change). No LaunchAgent reinstall
   is required.

4. **Persistence.** Disarm, then `launchctl kickstart -k gui/$UID/com.herdr.amphetamine.monitor`
   (or reboot/logout+login). The daemon comes back up **disarmed** (reads
   `config.json`) and does not start a session until re-armed.

5. **Amphetamine + state-machine correctness** (unchanged): `osascript ... session is active`
   and `pmset -g assertions` reflect owned-session start/end; `python3 -m unittest
   discover -s tests -v` is green, including the new armed/disarmed and config
   tests.

6. **End-to-end.** Start a real herdr agent that enters `working`; within poll +
   start-grace Amphetamine starts. Let it finish; within stop-grace the monitor
   ends the owned session. Record a transcript in *Outcomes & Retrospective*.

## Idempotence and Recovery

All scripts must be safe to run repeatedly. Installing the LaunchAgent twice replaces the plist and restarts the service. Uninstalling when not loaded does not fail the script. The daemon tolerates herdr being down (treats it as no working agents) and Amphetamine being missing (logs and retries with backoff).

The daemon must not end an Amphetamine session it did not start. On startup, if `session is active` is already true, `owned_session` is false. Disarming ends only an owned session; a pre-existing (non-owned) session is left alone.

`config.json` is the single source of intent. Corrupt or missing `config.json` → fall back to defaults (and rewrite a valid file). Invalid values (negative grace, non-numeric) → clamped/rejected by `config.validate`; the TUI refuses to save an invalid value. Env vars override the file only when explicitly set, so the LaunchAgent bootstrap (`HERDR_BIN_PATH`) always wins.

If AppleScript permission is denied, the daemon logs a clear message naming the launching process (Python / herdr / launchd) and the Privacy & Security → Automation pane. Do not bypass macOS prompts.

If closed-display behavior triggers an Amphetamine warning prompt, document the one-time manual setup (see `docs/manual-test.md`); do not automate clicks through the warning.

## Artifacts and Notes

Known Amphetamine AppleScript dictionary excerpts (paraphrased):

    session is active            -> true/false
    start new session with options {duration:int, interval:minutes|0, displaySleepAllowed:bool}
                                 -> starts a session (duration 0 + interval 0 = infinite)
    end session                  -> ends current session
    session time remaining       -> seconds left (0 = infinite; -1 trigger; -2 app/date; -3 none)
    enable closed display mode   -> allow keep-awake-while-closed (THIS is what we want)
    disable closed display mode  -> prevent it (OPPOSITE)

Known herdr plugin facts:

    A plugin is a directory with herdr-plugin.toml.
    Commands are argv arrays, not shell strings.
    Runtime env includes HERDR_BIN_PATH, HERDR_PLUGIN_ID, HERDR_PLUGIN_ACTION_ID,
      HERDR_PLUGIN_CONTEXT_JSON, HERDR_WORKSPACE_ID, HERDR_TAB_ID, HERDR_PANE_ID,
      HERDR_SOCKET_PATH. NOT injected: HERDR_PLUGIN_ROOT / _CONFIG_DIR / _STATE_DIR.
    Herdr does not sandbox plugin commands; they run as the user.
    Panes are PTY-backed, so an interactive curses program runs inside one.

Relevant manual commands:

    herdr agent list
    herdr plugin link <abs-path-to-plugin>
    herdr plugin action invoke tui --plugin local.amphetamine-macos
    herdr plugin pane open --plugin local.amphetamine-macos --entrypoint tui
    launchctl print gui/$UID/com.herdr.amphetamine.monitor
    pmset -g assertions
    osascript -e 'tell application "Amphetamine" to session is active'

## Interfaces and Dependencies

First version uses only macOS built-ins and installed apps: `/usr/bin/python3`
(includes `curses`), `/usr/bin/osascript`, `/bin/launchctl`, `/usr/bin/pmset`, the
`herdr` binary, `/Applications/Amphetamine.app`. No Homebrew or pip packages.

`scripts/amphetamine_ctl.py` — **unchanged** — exposes:

    is_amphetamine_available() -> bool
    is_session_active() -> bool
    session_time_remaining() -> int
    start_session(display_sleep_allowed: bool = False, duration_minutes=None) -> None
    end_session() -> None
    prevent_sleep_when_display_closed() -> None

`scripts/config.py` — **new** — exposes:

    DEFAULT_CONFIG: dict
    config_path() -> pathlib.Path        # state_dir / "config.json"
    default_config() -> dict
    load_config() -> dict                # file -> defaults; never raises (falls back)
    apply_env_overrides(cfg: dict) -> dict   # HERDR_* env wins when set
    validate(cfg: dict) -> dict          # clamp/normalize; returns a valid dict
    save_config(cfg: dict) -> None       # atomic write

`config.json` schema:

    {
      "armed": true,
      "poll_seconds": 5.0,
      "start_grace_seconds": 5.0,
      "stop_grace_seconds": 30.0,
      "top_up_minutes": 1.0,             # 0 = infinite
      "top_up_threshold_minutes": 2.0,
      "herdr_bin_path": null,             # null -> env HERDR_BIN_PATH -> `which herdr`
      "amphetamine_app_path": "/Applications/Amphetamine.app",
      "prevent_closed_display_sleep": true,
      "display_sleep_allowed": false
    }

`scripts/monitor.py` — **updated** — keeps the verified pure core and adds:

    def iterate(cfg, ctx, now) -> MonitorCtx   # now honors cfg.armed (early-return when disarmed)
    def load_config() -> Config                # reads config.json + env overrides + validate
    def run_daemon(cfg) -> None                # reloads config.json each cycle; SIGHUP -> reload
    def print_status(cfg) -> None              # non-interactive one-shot (TUI fallback)

    Removed: start_background, stop_background, _pid_file, _is_running,
             open_status_pane, --start/--stop/--open-pane modes.

`scripts/tui.py` — **new** — exposes:

    def main() -> int           # curses entry point; falls back to print_status if no TTY/curses
    # Internal: render(cfg, state, log_tail), handle_key(key, cfg) -> cfg|None, edit_setting(...)

The LaunchAgent installer CLI is unchanged in shape:

    python3 scripts/install_launchagent.py
    python3 scripts/uninstall_launchagent.py        # --cleanup also removes logs/state

Both print what they changed, where logs/state live, and exit non-zero only for real failures (not for unloading an already-unloaded service).

## Revision Notes

- 2026-07-04 12:15Z / Hermes Agent: Rewrote the prior design into a PLANS.md-style ExecPlan (living-document sections, self-contained context, concrete validation, idempotence guidance, explicit interfaces).
- 2026-07-05 / implementer: Implemented and verified the first version (amphetamine_ctl, monitor state machine, LaunchAgent, status pane); recorded the closed-display verb correction, the activate-before-start requirement, and the battery-power caveat.
- 2026-07-05 / implementer (redesign): Reorganized the architecture per the new requirements — **resident LaunchAgent daemon as the primary supervisor** + **interactive curses TUI for start/pause (arm/disarm), config editing, and status**. Introduced `config.json` as the persistent TUI-editable settings store; moved tunables out of the plist. Modeled pause as a logical `armed` flag (forced by `KeepAlive`). Removed the command-started background monitor. Retained all verified Amphetamine/state-machine foundations. Pending work tracked in *Progress*.
