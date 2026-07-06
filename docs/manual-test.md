# Manual validation

Automated unit tests cover the state machine, AppleScript string generation,
config.json load/save/validate, and armed/disarmed behavior. The steps below
validate real behavior on a Mac. Run them from the plugin root:

```sh
cd herdr-amphetamine-macos
```

## 0. Unit tests (no Amphetamine/herdr side effects)

```sh
python3 -m unittest discover -s tests -v
```

Expect: all tests pass, including `test_sustained_working_starts_session`,
`test_sustained_idle_stops_session`, the `test_flicker_*` family,
`test_pre_existing_session_not_owned_and_not_started`,
`test_disarmed_does_not_end_owned_session`, `test_env_overrides_file_when_set`,
`test_validate_clamps_negatives_and_bad_types`,
`test_no_activate_when_start_succeeds`, and
`test_restarts_when_owned_session_expired`.

## 0b. TUI (interactive control surface)

```sh
python3 scripts/tui.py
#   or:  herdr plugin action invoke tui --plugin amphetamine-macos
#   or:  herdr plugin pane open --plugin amphetamine-macos --entrypoint tui
```

Expect: a live panel showing ARMED/DISARMED, the monitor state (ON / starting /
stopping / OFF / ERROR), working-agent count, the live Amphetamine session state,
the daemon status, the configured session length, the recent log tail, and the
editable settings. It refreshes ~1 Hz and never starts/ends a session itself.

Key checks:

- Press `Space` to **disarm**; within one poll the badge flips to DISARMED. The
  daemon must not end or alter the current Amphetamine session. Press `Space`
  again to arm.
- Edit a setting (e.g. `s` → stop grace → `15`); on quit it persists in
  `config.json` and the daemon honors it on the next cycle.
- Press `?` for the keymap.

Non-TTY fallback (e.g. piped stdin/stdout) prints the same status block once and
exits, like `monitor.py --status`.

## 1. Amphetamine control (no herdr dependency)

> ⚠️ These change Amphetamine state. If you already have a session you care
> about, skip this section or record/restore it manually.
> ⚠️ Run on **AC power**. On battery, Amphetamine refuses AppleScript-started
> sessions unless *Preferences → Sessions → Allow sessions while on battery
> power* is on; `start_session` returns success but no session is created.

The first call triggers a macOS **Automation** permission prompt. Allow it.
`start_session` is focus-preserving: it tries `start new session` *without*
activating Amphetamine first, and only activates as a fallback if Amphetamine
ignores the command (idle/app-nap). The default session length is 1 minute;
pass `duration_minutes=` to override. The monitor adds 1 minute whenever agents
are working and time remaining drops below the extend threshold (default 2). It
does not end the session when agents go idle.

```sh
# Before: note the current state
osascript -e 'tell application "Amphetamine" to session is active'
pmset -g assertions | grep -i amphetamine   # likely empty

# Start via the plugin wrapper (starts + enables closed-display), then prove the
# session is active. Your foreground app should NOT lose focus.
python3 -c "import sys; sys.path.insert(0,'scripts'); import amphetamine_ctl as a; a.start_session(); a.prevent_sleep_when_display_closed()"
osascript -e 'tell application "Amphetamine" to session is active'   # -> true
pmset -g assertions | grep -i amphetamine   # expect Amphetamine assertions

# End it
python3 -c "import sys; sys.path.insert(0,'scripts'); import amphetamine_ctl as a; a.end_session()"
osascript -e 'tell application "Amphetamine" to session is active'   # -> false
```

Expected `pmset -g assertions` lines on this Mac (AC power), for the record:

```
pid NNN(Amphetamine): ... PreventUserIdleSystemSleep named: "Amphetamine (Single-Use - System)"
pid NNN(Amphetamine): ... PreventUserIdleDisplaySleep named: "Amphetamine (Single-Use - Display)"
```

Record the exact lines you observe (they vary by Amphetamine version) in
`PLANS.md` → *Outcomes & Retrospective*.

### One-time closed-display setup

`prevent_sleep_when_display_closed()` calls `enable closed display mode`. The
first time, Amphetamine may show a warning prompt. To silence it permanently
(recommended so the session LaunchAgent can run unattended):

1. Open Amphetamine → **Preferences → Sessions**.
2. Find **"Allow System to Sleep When Display is Closed"** and toggle it off.
3. In the warning prompt that appears, choose **Do Not Show This Message Again**.

Reference: Amphetamine's sdef states `enable closed display mode` "allow[s]
closed-display mode" (i.e. keep awake while the lid is closed). The older
PLANS.md inverted this; `disable closed display mode` is the *opposite* and must
not be used here.

## 2. herdr observation

With at least one agent `working`:

```sh
python3 scripts/monitor.py --status
# Expect a line like:  herdr agents:    N observed, M working
# and at least one '- working' entry.
```

With no working agents (all `idle`/`done`/`blocked`), expect `0 working`.

## 3. Session LaunchAgent

Sync the current herdr session. If this session has agents, this seeds a default
`config.json`, writes this session's plist, and loads it:

```sh
python3 scripts/sync_launchagent.py
python3 scripts/install_launchagent.py                         # force install for manual testing
sleep 8
python3 scripts/monitor.py --status                             # live state
```

The installer resolves herdr to an absolute path and bakes it into the plist as
`HERDR_BIN_PATH` (a user LaunchAgent has a minimal PATH). Tunables live in
`config.json`, so you do **not** reinstall to change them — use the TUI.

Persistence check (pause survives a restart):

```sh
# Disarm via the TUI (Space), then press i to reinstall/restart this session's daemon.
# The daemon comes back up DISARMED (reads config.json) and starts no session.
```

To remove:

```sh
python3 scripts/uninstall_launchagent.py            # keeps logs/state
#   python3 scripts/uninstall_launchagent.py --cleanup   # also removes them
```

## 4. End-to-end

1. Install/sync the LaunchAgent (step 3) and ensure no working agents are present;
   confirm Amphetamine has no active session.
2. Start a real herdr agent that enters `working`.
3. Within `poll + start_grace` (≈10s by default), confirm:
   `osascript -e 'tell application "Amphetamine" to session is active'` → `true`.
4. Let the agent finish / go idle.
5. Within `stop_grace` (≈30s), confirm the monitor stops extending only. The
   Amphetamine session remains active until its own remaining timer expires.

Paste a short transcript into `PLANS.md` → *Outcomes & Retrospective* when green.
