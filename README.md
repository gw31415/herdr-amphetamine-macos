# herdr plugin: Amphetamine macOS Sleep Guard

A macOS [herdr](https://github.com/gw31415/herdr) plugin that keeps the machine awake with [Amphetamine](https://apps.apple.com/us/app/amphetamine/id937984704) while agents are working.

It does one thing deliberately: when at least one herdr agent is `working`, it starts or tops up a short Amphetamine session if the remaining time is below a threshold. When agents go idle, it stops topping up. It never ends the user's current Amphetamine session.

## Features

- Session-scoped per-user LaunchAgent, installed once and started/stopped with agent count.
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

### From GitHub (recommended)

```sh
herdr plugin install gw31415/herdr-amphetamine-macos
herdr plugin action invoke sync-launchagent --plugin amphetamine-macos
```

### From a local clone (developers)

```sh
herdr plugin link "$PWD"
herdr plugin action invoke sync-launchagent --plugin amphetamine-macos
```

The installer:

- resolves the `herdr` binary path for the LaunchAgent environment,
- seeds `<HERDR_PLUGIN_CONFIG_DIR>/<session>/config.json` (herdr's per-plugin
  config dir, e.g. `~/.config/herdr/plugins/config/amphetamine-macos/<session>/`),
- writes `~/Library/LaunchAgents/com.herdr.amphetamine.monitor.<session>.plist`,
- starts this session's monitor when agents exist,
- writes logs under `~/Library/Logs/herdr-amphetamine/<session>/`.

The first real Amphetamine command may trigger a macOS Automation permission prompt. Allow the launching process to control Amphetamine.

## Usage

Open the TUI:

```sh
herdr plugin pane open --plugin amphetamine-macos --entrypoint tui
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
herdr plugin action invoke status --plugin amphetamine-macos
# or
python3 scripts/monitor.py --status
```

## Behavior

The monitor observes `herdr agent list` and treats only exact `working` statuses as active work.
`sync-launchagent` installs this session's LaunchAgent if needed, starts it when
agent count is nonzero, and stops it when the count is zero. A running monitor
also stops its own LaunchAgent after it observes zero agents.

```text
off --working--> pending_on --(start grace)--> on
on  --idle----> pending_off --(stop grace)---> off
```

Defaults:

- poll every 5 seconds,
- require 5 seconds of sustained work before starting,
- require 30 seconds of sustained idle before leaving `on`,
- start a 1-minute Amphetamine session,
- when remaining time is below 2 minutes, add 1 minute by resetting the
  Amphetamine session duration to the current remaining time plus 1 minute
  (rounded up to whole minutes).

Important safety rule: the monitor never calls Amphetamine's `end session`. `armed=false`, idle agents, daemon shutdown, and uninstall all stop plugin activity without cancelling the user's manual Amphetamine session.

## Configuration

Persistent settings and runtime state live in herdr's per-plugin directories,
under per-session subdirectories so concurrent herdr sessions stay isolated:

```text
$HERDR_PLUGIN_CONFIG_DIR/<session>/config.json   # settings (TUI-editable)
$HERDR_PLUGIN_STATE_DIR/<session>/state.json     # runtime monitor state
```

herdr injects `HERDR_PLUGIN_CONFIG_DIR` / `HERDR_PLUGIN_STATE_DIR` for plugin
actions; discover the config dir with `herdr plugin config-dir amphetamine-macos`
(typically `~/.config/herdr/plugins/config/amphetamine-macos`). Edit config via
the TUI when possible. The daemon reloads config every poll and on best-effort
`SIGHUP` from the TUI.

| Key | Default | Environment override | Meaning |
| --- | --- | --- | --- |
| `armed` | `true` | — | enable/pause the guard |
| `poll_seconds` | `5` | `HERDR_AMPHETAMINE_POLL_SECONDS` | seconds between observations |
| `start_grace_seconds` | `5` | `HERDR_AMPHETAMINE_START_GRACE_SECONDS` | sustained work required before starting |
| `stop_grace_seconds` | `30` | `HERDR_AMPHETAMINE_STOP_GRACE_SECONDS` | sustained idle required before leaving `on` |
| `top_up_minutes` | `1` | `HERDR_AMPHETAMINE_TOP_UP_MINUTES` | minutes to add on each top-up; also the initial session length; `0` means infinite |
| `top_up_threshold_minutes` | `2` | `HERDR_AMPHETAMINE_TOP_UP_THRESHOLD_MINUTES` | top up when remaining time is below this |
| `prevent_closed_display_sleep` | `true` | — | enable Amphetamine closed-display mode |
| `display_sleep_allowed` | `false` | — | allow display sleep during sessions |
| `herdr_bin_path` | `null` | `HERDR_BIN_PATH` | `null` means env/PATH resolution |
| `amphetamine_app_path` | `/Applications/Amphetamine.app` | `AMPHETAMINE_APP_PATH` | Amphetamine app bundle path |

`HERDR_AMPHETAMINE_CONFIG_DIR` / `HERDR_AMPHETAMINE_STATE_DIR` select the
per-session config/state directories. herdr injects the plugin-scoped roots
(`HERDR_PLUGIN_CONFIG_DIR` / `HERDR_PLUGIN_STATE_DIR`); the installer resolves
the per-session subdirectory and the LaunchAgent pins the resulting absolute
paths.

## Uninstall

```sh
herdr plugin action invoke uninstall-launchagent --plugin amphetamine-macos
herdr plugin unlink amphetamine-macos
```

The uninstall action unloads/removes only the current session's LaunchAgent. It does not end Amphetamine sessions.

## Troubleshooting

- Automation denied: grant permission in System Settings -> Privacy & Security -> Automation.
- Lid-closed sleep still happens: complete Amphetamine's one-time closed-display prompt/setup; see `docs/manual-test.md`.
- Daemon not running: run `sync-launchagent`, or use `i` in the TUI for the current session.
- TUI pane says daemon not detected with agents present: run the sync action.

## Development

Run tests:

```sh
python3 -m unittest discover -s tests -v
```

Manual validation steps are in `docs/manual-test.md`. Security notes are in `docs/security.md`.
