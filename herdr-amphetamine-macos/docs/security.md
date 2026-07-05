# Security review notes

## What this plugin touches

- **Amphetamine**, via `osascript` AppleScript. The complete set of verbs is in
  [`scripts/amphetamine_ctl.py`](../scripts/amphetamine_ctl.py):
  `session is active`, `start new session`, `end session`, and
  `enable closed display mode`. One verb per function; no string interpolation
  from herdr data reaches AppleScript.
- **herdr**, via `herdr agent list` (read-only JSON). The monitor parses only the
  `agent_status` field.
- **The user LaunchAgent** `com.herdr.amphetamine.monitor`, installed under
  `~/Library/LaunchAgents/` (per-user, no root, no admin prompt).

## What it does NOT do

- No network access. No telemetry. No outbound connections anywhere in the code.
- No Accessibility/UI automation, no synthetic clicks, no screen reading.
- Does not write outside the user's home directory.
- Does not touch any application other than Amphetamine.
- Does not end an Amphetamine session it did not start (ownership is recorded in
  `state.json` and reconciled at startup).

## Privileges

- Runs entirely as the logged-in user. The LaunchAgent is per-user
  (`~/Library/LaunchAgents/`), so installation needs no administrator password.
- The one privilege it requires is **macOS Automation** consent for the
  launching process to control Amphetamine. macOS gates this with a one-time
  prompt; the plugin never attempts to bypass it.

## Auditing

- Every Amphetamine call is a literal AppleScript string in `amphetamine_ctl.py`.
  `start_session` is the only function that interpolates a value, and that value
  is a hard-coded `true`/`false` literal derived from a boolean argument — never
  from herdr output.
- `osascript` is invoked with an argv list (`subprocess.run(["osascript","-e",script])`),
  not a shell string, so there is no shell-injection surface.
- State file (`state.json`) is written atomically (temp file + rename) and
  contains only monitor fields — no secrets.

## Logs

`~/Library/Logs/herdr-amphetamine/monitor.{out,err}.log` contain timestamped
state transitions and observations. They may include agent status counts but no
command content or secrets.
