# Amphetamine macOS Sleep Guard (herdr plugin)

Keeps your Mac awake with [Amphetamine](https://apps.apple.com/us/app/amphetamine/id937984704)
while at least one herdr agent is **working**, and lets it sleep again once they
go idle. Supports staying awake with the lid closed (closed-display mode), which
plain `caffeinate` cannot do reliably.

The plugin has two parts:

- A **resident LaunchAgent daemon** that observes herdr agent status and drives
  Amphetamine. It starts at login, survives terminal closes and reboots
  (`RunAtLoad` + `KeepAlive`), and is the only thing that owns the Amphetamine
  session lifecycle.
- An **interactive TUI** (`scripts/tui.py`) for everything human: **start/pause**
  the guard (arm/disarm), **change settings**, and **view live status**.

All Amphetamine interaction is in [`scripts/amphetamine_ctl.py`](scripts/amphetamine_ctl.py)
— a tiny, auditable surface of one AppleScript verb per function.

## Requirements

- macOS (tested on Darwin 25 / macOS 26)
- Amphetamine installed at `/Applications/Amphetamine.app`
- herdr ≥ 0.7.0 on `PATH` (or set `HERDR_BIN_PATH`)
- `/usr/bin/python3` (system Python 3.9+, includes `curses`). No third-party Python packages.

## How it works

A small state machine with hysteresis prevents flicker from rapidly toggling
Amphetamine:

```
off --working--> pending_on --(start grace)--> on
on  --idle----> pending_off --(stop grace)---> off
```

- A single short blip of `working` that does not survive the **start grace**
  (default 5s) never starts Amphetamine.
- A short blip of idle that does not survive the **stop grace** (default 30s)
  never stops it.
- **Ownership:** the daemon only ever ends a session *it* started. If Amphetamine
  already has an active session when work begins, the daemon rides along and will
  not end that pre-existing session.
- Only the `working` state holds the machine awake. `idle`, `done`, `blocked`,
  and `unknown` release it (after the stop grace).

**Arm/disarm (start/pause).** The LaunchAgent keeps the daemon resident, so
"pause" is a logical switch, not a process kill. Disarm (`armed=false`) releases
any owned session and idles; the daemon stays alive. Re-arm resumes normal
behavior. The flag persists in `config.json`, so a pause survives daemon restarts.

## Install

```sh
# 1. Register the plugin with herdr (local link)
herdr plugin link /absolute/path/to/herdr-amphetamine-macos

# 2. Install the resident LaunchAgent (starts the daemon now and at login)
herdr plugin action invoke install-launchagent --plugin local.amphetamine-macos
#   or:  python3 scripts/install_launchagent.py
```

The installer resolves `herdr` to an absolute path (a user LaunchAgent has a
minimal `PATH`), seeds a default `config.json`, writes the plist to
`~/Library/LaunchAgents/`, and loads it. Logs go to
`~/Library/Logs/herdr-amphetamine/`.

The first time the daemon talks to Amphetamine, macOS shows an **Automation**
permission prompt: allow the launching process (Terminal/iTerm/herdr/Python/
launchd) to control Amphetamine. Grant it once. See
[docs/manual-test.md](docs/manual-test.md) for the one-time closed-display setup.

## Usage (the TUI)

Open the TUI — it is the single control surface:

```sh
herdr plugin action invoke tui --plugin local.amphetamine-macos
#   or:  herdr plugin pane open --plugin local.amphetamine-macos --entrypoint tui
#   or:  python3 scripts/tui.py
```

The TUI shows live status (monitor state, working agents, Amphetamine session,
recent log) and lets you:

| Key | Action |
|-----|--------|
| `Space` | **arm / disarm** the guard (start / pause) |
| `c` / `d` | toggle closed-display keep-awake / display-sleep-allowed |
| `p` `g` `s` `m` `e` | edit poll / start grace / stop grace / session min / extend threshold |
| `b` / `a` | edit herdr bin path / Amphetamine app path |
| `i` / `u` | install / uninstall the LaunchAgent |
| `r` | refresh now |
| `?` | help |
| `q` | quit |

Edits are written to `config.json` and the daemon is notified (best-effort
`SIGHUP`), so a change takes effect immediately or within one poll. The TUI never
starts/ends an Amphetamine session itself — only the daemon does.

For a non-interactive one-shot (scripting), use:

```sh
herdr plugin action invoke status --plugin local.amphetamine-macos
#   or:  python3 scripts/monitor.py --status
```

## Behavior notes

- **Focus-preserving start.** `start_session` first tries `start new session`
  *without* activating Amphetamine, so your foreground app keeps focus. If
  Amphetamine ignores the command (idle/app-nap), it activates once as a fallback
  and retries.
- **Short sessions, extended on a schedule.** Sessions default to 10 minutes
  (`session_minutes`; `0` = infinite) and are extended back to the full length
  whenever time remaining drops to the threshold (`extend_threshold_minutes`,
  default 5). So a session stays at 5–10 minutes while agents work — checked every
  poll (5s) — and naturally expires if the daemon ever stops extending. If an
  owned session disappears anyway, it is restarted immediately (no sleep gap).
- **Hot-reload.** The daemon re-reads `config.json` every poll and on `SIGHUP`,
  so TUI edits apply without reinstalling the LaunchAgent.

## Configuration

Persistent settings live in `config.json` under the state directory
(`HERDR_AMPHETAMINE_STATE_DIR`, default
`~/Library/Application Support/herdr-amphetamine/`). Edit them via the TUI, or by
hand. Defaults:

| Key | Default | Meaning |
|---|---|---|
| `armed` | `true` | master switch — `false` pauses the guard (daemon stays resident) |
| `poll_seconds` | `5` | seconds between observations |
| `start_grace_seconds` | `5` | sustained `working` required before starting |
| `stop_grace_seconds` | `30` | sustained idle required before stopping |
| `session_minutes` | `10` | Amphetamine session length in minutes (`0` = infinite) |
| `extend_threshold_minutes` | `5` | extend the session when time remaining is at/below this |
| `prevent_closed_display_sleep` | `true` | call `enable closed display mode` (keep awake lid-closed) |
| `display_sleep_allowed` | `false` | allow display sleep during the session |
| `herdr_bin_path` | `null` | `null` → `HERDR_BIN_PATH` env → `which herdr` |
| `amphetamine_app_path` | `/Applications/Amphetamine.app` | Amphetamine bundle path |

Environment variables (`HERDR_AMPHETAMINE_*`, `HERDR_BIN_PATH`,
`AMPHETAMINE_APP_PATH`) override the file **when set** — useful for the LaunchAgent
bootstrap and power users. The plist pins only `HERDR_BIN_PATH` and
`HERDR_AMPHETAMINE_STATE_DIR`; everything else is tunable from the TUI.

## Uninstall

```sh
# 1. Unload and remove the LaunchAgent (the daemon ends its owned session on SIGTERM)
herdr plugin action invoke uninstall-launchagent --plugin local.amphetamine-macos
#   or:  python3 scripts/uninstall_launchagent.py            # keeps logs/state
#        python3 scripts/uninstall_launchagent.py --cleanup  # also removes them

# 2. Unlink the plugin
herdr plugin unlink local.amphetamine-macos
```

## Troubleshooting

- **"macOS denied Automation permission for Amphetamine"** — grant it in
  *System Settings → Privacy & Security → Automation*. The prompt attributes the
  request to whatever launched Python (Terminal, iTerm, herdr, or launchd).
- **Amphetamine keeps sleeping with the lid closed** — perform the one-time
  closed-display setup in [docs/manual-test.md](docs/manual-test.md). The daemon
  calls `enable closed display mode`, which can surface a warning prompt the first
  time.
- **Daemon not running** — check `launchctl print gui/$UID/com.herdr.amphetamine.monitor`
  (`state = running`) and `~/Library/Logs/herdr-amphetamine/monitor.err.log`.
- **TUI shows "daemon: not detected"** — install the LaunchAgent (`i` in the TUI,
  or the `install-launchagent` action).

## Tests

```sh
python3 -m unittest discover -s tests -v
```
