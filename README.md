# Amphetamine macOS Sleep Guard

A macOS [herdr](https://github.com/gw31415/herdr) plugin that keeps the machine awake with [Amphetamine](https://apps.apple.com/us/app/amphetamine/id937984704) while agents are working.

It does one thing deliberately: when at least one herdr agent is `working`, it starts or tops up a short Amphetamine session if the remaining time is below a threshold. When agents go idle, it stops topping up. It never ends the user's current Amphetamine session.

## Features

- Resident per-user LaunchAgent (`RunAtLoad` + `KeepAlive`).
- Interactive TUI for status, settings, install/uninstall, and enable/pause.
- Flicker-resistant state machine with start/stop grace periods.
- Short finite sessions that are extended only while agents work.
- Safe around manual Amphetamine use: no forced `end session` calls.
- Optional closed-display keep-awake support via Amphetamine's closed-display mode.
- Stdlib-only Python; no package install step.

## Requirements

- macOS.
- Amphetamine installed at `/Applications/Amphetamine.app`.
- herdr `>= 0.7.0` on `PATH`, or `HERDR_BIN_PATH` set.
- `/usr/bin/python3` with `curses`.

## Install

From this repository root:

```sh
herdr plugin link "$PWD"
herdr plugin action invoke install-launchagent --plugin local.amphetamine-macos
```

The installer:

- resolves the `herdr` binary path for the LaunchAgent environment,
- seeds `~/Library/Application Support/herdr-amphetamine/config.json`,
- writes `~/Library/LaunchAgents/com.herdr.amphetamine.monitor.plist`,
- starts the resident monitor,
- writes logs under `~/Library/Logs/herdr-amphetamine/`.

The first real Amphetamine command may trigger a macOS Automation permission prompt. Allow the launching process to control Amphetamine.

## Usage

Open the TUI:

```sh
herdr plugin action invoke tui --plugin local.amphetamine-macos
# or
herdr plugin pane open --plugin local.amphetamine-macos --entrypoint tui
# or
python3 scripts/tui.py
```

Useful keys:

| Key | Action |
| --- | --- |
| `Space` | enable / pause the guard (`armed`) |
| `c` | toggle closed-display keep-awake |
| `d` | toggle display sleep allowed |
| `p` | edit poll interval |
| `g` | edit start grace |
| `s` | edit stop grace |
| `m` | edit session length |
| `e` | edit extend threshold |
| `b` | edit herdr binary path |
| `a` | edit Amphetamine app path |
| `i` | install/reinstall LaunchAgent |
| `u` | uninstall LaunchAgent |
| `r` | refresh |
| `?` | help |
| `q` | quit |

Non-interactive status:

```sh
herdr plugin action invoke status --plugin local.amphetamine-macos
# or
python3 scripts/monitor.py --status
```

## Behavior

The monitor observes `herdr agent list` and treats only exact `working` statuses as active work.

```text
off --working--> pending_on --(start grace)--> on
on  --idle----> pending_off --(stop grace)---> off
```

Defaults:

- poll every 5 seconds,
- require 5 seconds of sustained work before starting,
- require 30 seconds of sustained idle before leaving `on`,
- start/top up a 10-minute Amphetamine session,
- top up when remaining time is at or below 5 minutes.

Important safety rule: the monitor never calls Amphetamine's `end session`. `armed=false`, idle agents, daemon shutdown, and uninstall all stop plugin activity without cancelling the user's manual Amphetamine session.

## Configuration

Persistent config lives at:

```text
~/Library/Application Support/herdr-amphetamine/config.json
```

Edit it via the TUI when possible. The daemon reloads config every poll and on best-effort `SIGHUP` from the TUI.

| Key | Default | Meaning |
| --- | --- | --- |
| `armed` | `true` | enable/pause the guard |
| `poll_seconds` | `5` | seconds between observations |
| `start_grace_seconds` | `5` | sustained work required before starting |
| `stop_grace_seconds` | `30` | sustained idle required before leaving `on` |
| `session_minutes` | `10` | Amphetamine session length; `0` means infinite |
| `extend_threshold_minutes` | `5` | top up when remaining time is at/below this |
| `prevent_closed_display_sleep` | `true` | enable Amphetamine closed-display mode |
| `display_sleep_allowed` | `false` | allow display sleep during sessions |
| `herdr_bin_path` | `null` | `null` means env/PATH resolution |
| `amphetamine_app_path` | `/Applications/Amphetamine.app` | Amphetamine app bundle path |

Environment overrides:

- `HERDR_AMPHETAMINE_POLL_SECONDS`
- `HERDR_AMPHETAMINE_START_GRACE_SECONDS`
- `HERDR_AMPHETAMINE_STOP_GRACE_SECONDS`
- `HERDR_AMPHETAMINE_SESSION_MINUTES`
- `HERDR_AMPHETAMINE_EXTEND_THRESHOLD_MINUTES`
- `HERDR_BIN_PATH`
- `AMPHETAMINE_APP_PATH`
- `HERDR_AMPHETAMINE_STATE_DIR`

## Uninstall

```sh
herdr plugin action invoke uninstall-launchagent --plugin local.amphetamine-macos
herdr plugin unlink local.amphetamine-macos
```

The uninstall action unloads/removes the LaunchAgent. It does not end Amphetamine sessions.

## Troubleshooting

- Automation denied: grant permission in System Settings -> Privacy & Security -> Automation.
- Lid-closed sleep still happens: complete Amphetamine's one-time closed-display prompt/setup; see `docs/manual-test.md`.
- Daemon not running: check `launchctl print gui/$UID/com.herdr.amphetamine.monitor` and `~/Library/Logs/herdr-amphetamine/monitor.err.log`.
- TUI says daemon not detected: run the install action (`i` in the TUI or `install-launchagent`).

## Development

Run tests:

```sh
python3 -m unittest discover -s tests -v
```

Manual validation steps are in `docs/manual-test.md`. Security notes are in `docs/security.md`.
