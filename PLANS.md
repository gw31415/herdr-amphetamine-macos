# herdr Amphetamine macOS sleep-prevention plugin ExecPlan

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This file is written in the style described by OpenAI's PLANS.md guidance for Codex execution plans: it is self-contained, outcome-focused, and intended to let a future agent or human implement the plugin without relying on this conversation.

## Purpose / Big Picture

After this work is implemented, a macOS user who runs AI coding agents inside herdr will be able to close the MacBook display or leave the machine unattended while agents are working, without manually starting Amphetamine each time. The plugin will observe herdr agent activity and ask Amphetamine to keep the machine awake only while at least one agent is working. When all relevant agents become idle, done, blocked, or disappear for a grace period, the plugin will release its Amphetamine session so normal sleep behavior resumes.

The user-visible proof is simple: start a herdr agent, observe that Amphetamine has an active session and `pmset -g assertions` shows sleep-prevention assertions; let the agent stop working, observe that the Amphetamine session ends and the assertions disappear. The design requires a macOS LaunchAgent as the standard always-on supervisor. A LaunchAgent is Apple's per-user background-service mechanism; it runs as the logged-in user and therefore should not require an administrator password on every start.

## Progress

- [x] (2026-07-04 12:15Z) Rewrote the previous high-level design into a PLANS.md-style self-contained ExecPlan.
- [x] (2026-07-04 12:15Z) Captured known Amphetamine AppleScript commands from the installed application dictionary: `session is active`, `start new session`, `end session`, `enable closed display mode`, and `disable closed display mode`.
- [x] (2026-07-04 12:15Z) Captured the architectural decision that the long-running supervisor must be a macOS user LaunchAgent, not a transient herdr event command.
- [ ] Choose the final repository directory for the plugin. This plan assumes `./herdr-amphetamine-macos/` relative to the working directory where implementation begins.
- [ ] Create the plugin skeleton and scripts.
- [ ] Validate Amphetamine AppleScript control on the target Mac.
- [ ] Validate herdr agent-state observation on the target herdr version.
- [ ] Install and verify the LaunchAgent.
- [ ] Run an end-to-end test with a real herdr agent.

## Surprises & Discoveries

- Observation: macOS `caffeinate` is not enough for this use case because the desired behavior includes staying awake when the display/lid is closed. Amphetamine has a separate closed-display-mode feature exposed through AppleScript.
  Evidence: `man caffeinate` documents idle/system/display sleep assertions but does not provide a lid-closed override. Amphetamine's scripting dictionary contains `enable closed display mode` and `disable closed display mode` commands.

- Observation: Amphetamine has a real AppleScript interface, so the plugin should not drive the UI with clicks or Accessibility automation.
  Evidence: running `sdef /Applications/Amphetamine.app` on the target machine showed commands named `session is active`, `start new session`, `end session`, `enable closed display mode`, and `disable closed display mode`.

- Observation: Herdr plugins are executable packages rather than sandboxed SDK extensions. They can run scripts and call the full herdr CLI.
  Evidence: Herdr plugin documentation states that a plugin is a directory with `herdr-plugin.toml`, and that the Herdr CLI is the plugin API. Runtime commands receive environment variables such as `HERDR_BIN_PATH`, `HERDR_PLUGIN_ROOT`, `HERDR_PLUGIN_CONFIG_DIR`, and `HERDR_PLUGIN_STATE_DIR`.

## Decision Log

- Decision: Use Amphetamine AppleScript commands instead of `caffeinate`.
  Rationale: The user specifically needs the machine to stay awake when the display is closed. Amphetamine already provides this behavior on the user's Mac, while `caffeinate` is only reliable for idle sleep and related assertions.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Run the monitor as a macOS user LaunchAgent.
  Rationale: A LaunchAgent is the standard macOS mechanism for a per-user background process. It starts at login, runs without repeated administrator prompts, and can supervise a long-running monitor independently of a herdr pane.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Prefer polling `herdr agent list` for the first implementation, even if herdr event hooks are later available.
  Rationale: Polling is easier to validate end-to-end and does not depend on the exact event names exposed by the installed herdr version. Event hooks can be added later as an optimization. The poll interval should be conservative, such as 5 seconds, because this is not latency-critical.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Treat `working` as the only state that actively requires Amphetamine.
  Rationale: `idle`, `done`, `blocked`, and `unknown` should not hold the machine awake indefinitely by default. `blocked` often means user input is needed, not that useful background work is continuing. This default avoids accidentally preventing sleep forever.
  Date/Author: 2026-07-04 / Hermes Agent

- Decision: Implement hysteresis with configurable grace periods.
  Rationale: Agent status can briefly flicker between states. The plugin should not rapidly start and stop Amphetamine sessions. A start grace and stop grace make the behavior stable and explainable.
  Date/Author: 2026-07-04 / Hermes Agent

## Outcomes & Retrospective

No implementation has been performed yet. This rewrite clarifies the intended behavior, defines the LaunchAgent-based architecture, and records the main design choices. The next major outcome should be a minimal plugin skeleton whose Amphetamine control can be tested independently from herdr monitoring.

## Context and Orientation

This plan concerns three systems.

Herdr is a terminal workspace manager for AI coding agents. A herdr workspace contains tabs and panes. A pane can contain an agent. Herdr exposes agent status through its CLI. The relevant status values are `working`, `idle`, `blocked`, `done`, and `unknown`. In this plan, an "agent is running" means at least one observed herdr agent is in the `working` state.

Amphetamine is a macOS application that can keep the computer awake. The user's installed Amphetamine app supports AppleScript. AppleScript is Apple's scripting interface for controlling applications. The planned plugin will call AppleScript through the command-line tool `osascript`. The relevant Amphetamine commands are `session is active`, `start new session`, `end session`, `enable closed display mode`, and `disable closed display mode`. `start new session` can accept options including `duration`, `interval`, and `displaySleepAllowed`. For an infinite-duration session, use duration `0` and interval `0`. `displaySleepAllowed:false` means display sleep is not allowed during the Amphetamine session.

LaunchAgent is macOS's user-level background process manager. A LaunchAgent plist placed under `~/Library/LaunchAgents/` can run a command at login as the current user. Because it is per-user, it does not need root privileges for normal installation under the user's home directory. It is appropriate for the monitor process because the monitor must continue running while herdr is open and should not require a visible terminal pane.

The future repository layout assumed by this plan is:

    herdr-amphetamine-macos/
      herdr-plugin.toml
      README.md
      scripts/
        amphetamine_ctl.py
        monitor.py
        install_launchagent.py
        uninstall_launchagent.py
      launchagents/
        com.herdr.amphetamine.monitor.plist.template
      tests/
        test_state_machine.py
        test_amphetamine_ctl.py
      docs/
        security.md
        manual-test.md

If the implementer chooses a different repository root, keep the same internal paths relative to that root unless there is a strong reason to change them. Update this ExecPlan if paths change.

## Plan of Work

Begin with a minimal Python implementation rather than shell. Python is available on macOS, is easier to unit-test than shell, and can call `subprocess.run` for both `osascript` and `herdr`. Do not add a third-party dependency for the first version. The initial implementation should be a small monitor loop with clear modules: one module controls Amphetamine, one module reads herdr status, and one module applies a state machine.

Create `scripts/amphetamine_ctl.py` first. It should define small functions such as `is_session_active()`, `start_session(display_sleep_allowed: bool = False)`, `end_session()`, and `disable_closed_display_sleep()`. The function name `disable_closed_display_sleep()` should mean "tell Amphetamine to prevent sleeping when the display is closed". It should call the Amphetamine AppleScript command `disable closed display mode`, because the Amphetamine dictionary describes this command as modifying the current session or global preference to prevent closed-display mode. Add comments explaining this inverse naming so future maintainers do not accidentally flip it.

Create `scripts/monitor.py` next. It should read configuration from environment variables first, with sensible defaults. Use `HERDR_BIN_PATH` when present; otherwise fall back to `herdr` on PATH. Use `HERDR_AMPHETAMINE_POLL_SECONDS`, default `5`; `HERDR_AMPHETAMINE_START_GRACE_SECONDS`, default `5`; and `HERDR_AMPHETAMINE_STOP_GRACE_SECONDS`, default `30`. The monitor should query herdr, determine whether any agent is `working`, and then transition between states. The simplest states are `off`, `pending_on`, `on`, `pending_off`, and `error`. The monitor should log each transition to stdout so LaunchAgent can capture it.

Use `herdr agent list` as the first status source. If the command can output JSON in the installed version, use JSON. If not, parse the stable textual output only after documenting the exact observed format in `Surprises & Discoveries`. The implementation should prefer structured output whenever possible. Do not assume pane IDs are stable across restarts.

Create `herdr-plugin.toml` to make this a herdr plugin. The plugin should declare macOS as the supported platform. It should expose actions for `status`, `install LaunchAgent`, `uninstall LaunchAgent`, and `restart monitor` if herdr plugin actions support these workflows in the installed version. The LaunchAgent remains the real supervisor; plugin actions are convenience entry points.

Create `launchagents/com.herdr.amphetamine.monitor.plist.template`. The template should be rendered by `scripts/install_launchagent.py` because it needs absolute paths to the repository root and Python interpreter. The generated plist should be written to `~/Library/LaunchAgents/com.herdr.amphetamine.monitor.plist`. It should set `RunAtLoad` to true. It may set `KeepAlive` to true, but if the monitor exits intentionally on uninstall or disable, the uninstall script must unload the LaunchAgent first. Standard output and error should go to files under `~/Library/Logs/herdr-amphetamine/`.

Create unit tests for the pure state machine before writing the monitor loop. The tests should prove that one `working` observation does not necessarily start Amphetamine until the start grace expires, that sustained `working` starts it, that sustained non-working stops it, and that status flicker does not cause rapid start/stop calls. Mock `subprocess.run` in Amphetamine tests so tests do not actually start Amphetamine.

After the unit-tested pieces work, perform manual integration tests. First test Amphetamine alone with `osascript`. Then test herdr status detection with one agent. Then install the LaunchAgent and verify that the monitor starts at login or with `launchctl bootstrap`. Finally run the end-to-end scenario and record short evidence snippets in this plan.

## Concrete Steps

Run all implementation commands from the parent directory where the future repository should live. If the chosen directory is `/Users/ama/herdr-amphetamine-macos`, then use:

    cd /Users/ama
    mkdir -p herdr-amphetamine-macos/scripts herdr-amphetamine-macos/launchagents herdr-amphetamine-macos/tests herdr-amphetamine-macos/docs
    cd herdr-amphetamine-macos

Create `herdr-plugin.toml` with plugin metadata and actions. The exact action syntax must be checked against the installed herdr version before finalizing. The intended shape is:

    id = "local.amphetamine-macos"
    name = "Amphetamine macOS Sleep Guard"
    version = "0.1.0"
    min_herdr_version = "0.7.0"
    description = "Keeps macOS awake with Amphetamine while herdr agents are working."
    platforms = ["macos"]

    [[actions]]
    id = "status"
    title = "Show Amphetamine monitor status"
    contexts = ["workspace"]
    command = ["python3", "scripts/monitor.py", "--status"]

    [[actions]]
    id = "install-launchagent"
    title = "Install user LaunchAgent"
    contexts = ["workspace"]
    command = ["python3", "scripts/install_launchagent.py"]

    [[actions]]
    id = "uninstall-launchagent"
    title = "Uninstall user LaunchAgent"
    contexts = ["workspace"]
    command = ["python3", "scripts/uninstall_launchagent.py"]

Implement `scripts/amphetamine_ctl.py` with subprocess wrappers around `osascript`. The AppleScript snippets should be small and auditable. Example commands to validate manually before coding are:

    osascript -e 'tell application "Amphetamine" to session is active'
    osascript -e 'tell application "Amphetamine" to start new session with options {duration:0, interval:0, displaySleepAllowed:false}'
    osascript -e 'tell application "Amphetamine" to disable closed display mode'
    osascript -e 'tell application "Amphetamine" to end session'

The expected first command output is either `true` or `false`. Starting a session should not print a large transcript; if macOS asks for Automation permission, grant it once and record that in `Surprises & Discoveries`. After starting a session, run:

    pmset -g assertions

The expected evidence is a process or application assertion associated with Amphetamine. Exact assertion labels may vary by Amphetamine version, so record the observed line in this plan.

Implement `scripts/monitor.py` so it supports both daemon mode and one-shot status mode. One-shot status mode should not change Amphetamine; it should print the observed agents and the state file contents. Daemon mode should loop until interrupted. It should handle SIGTERM by ending only the Amphetamine session that this monitor believes it started. If Amphetamine was already active before the monitor started, the first implementation should avoid ending that pre-existing session; store ownership in state, for example `owned_session: true` only after this monitor successfully starts a session while no Amphetamine session was previously active.

Use a state file under the plugin state directory when available. The priority should be `HERDR_PLUGIN_STATE_DIR/state.json`, then `~/Library/Application Support/herdr-amphetamine/state.json`. The state should include at least:

    {
      "monitor_state": "off",
      "owned_session": false,
      "last_agent_working": false,
      "last_transition_unix": 0,
      "last_error": null
    }

Create `launchagents/com.herdr.amphetamine.monitor.plist.template` with placeholders for absolute paths. The generated plist should resemble:

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
      <key>Label</key>
      <string>com.herdr.amphetamine.monitor</string>
      <key>ProgramArguments</key>
      <array>
        <string>/usr/bin/python3</string>
        <string>__PLUGIN_ROOT__/scripts/monitor.py</string>
      </array>
      <key>RunAtLoad</key>
      <true/>
      <key>KeepAlive</key>
      <true/>
      <key>StandardOutPath</key>
      <string>__HOME__/Library/Logs/herdr-amphetamine/monitor.out.log</string>
      <key>StandardErrorPath</key>
      <string>__HOME__/Library/Logs/herdr-amphetamine/monitor.err.log</string>
      <key>EnvironmentVariables</key>
      <dict>
        <key>HERDR_AMPHETAMINE_POLL_SECONDS</key>
        <string>5</string>
      </dict>
    </dict>
    </plist>

The install script should create `~/Library/Logs/herdr-amphetamine/`, render the plist, run `launchctl bootout gui/$UID ~/Library/LaunchAgents/com.herdr.amphetamine.monitor.plist` ignoring the error if it was not loaded, then run `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.herdr.amphetamine.monitor.plist`, and finally run `launchctl kickstart -k gui/$UID/com.herdr.amphetamine.monitor`. The uninstall script should run `launchctl bootout gui/$UID ~/Library/LaunchAgents/com.herdr.amphetamine.monitor.plist`, remove the plist, and leave logs/state unless the user passes an explicit cleanup flag.

For unit tests, use Python's standard `unittest` unless the repository already adopts `pytest`. If no test framework exists, `unittest` avoids adding dependencies. The command should be:

    cd /Users/ama/herdr-amphetamine-macos
    python3 -m unittest discover -s tests -v

Expected output after implementation should include tests such as:

    test_flicker_does_not_toggle ... ok
    test_sustained_idle_stops_owned_session ... ok
    test_sustained_working_starts_session ... ok

For end-to-end validation, run:

    cd /Users/ama/herdr-amphetamine-macos
    python3 scripts/monitor.py --status
    python3 scripts/install_launchagent.py
    launchctl print gui/$UID/com.herdr.amphetamine.monitor
    tail -n 50 ~/Library/Logs/herdr-amphetamine/monitor.out.log

The expected result is that `launchctl print` shows the service, and the log shows that the monitor has started and has observed either no agents or the current herdr agents.

## Validation and Acceptance

The implementation is acceptable only if it produces observable behavior, not just files.

First, Amphetamine control must be proven. With no herdr dependency, running the start command must make Amphetamine report an active session, and running the end command must make it report no active session if the monitor owned that session. The proof commands are:

    osascript -e 'tell application "Amphetamine" to session is active'
    pmset -g assertions

Second, herdr observation must be proven. With at least one real herdr agent working, `python3 scripts/monitor.py --status` must print an agent list or summary that includes a `working` state. With no working agents, it must print that no working agents are present. If herdr's output format differs from expectations, update this plan and the parser together.

Third, the state machine must be proven with tests. Run:

    python3 -m unittest discover -s tests -v

Expect all tests to pass. At minimum, there must be tests covering sustained working, sustained idle, flicker, pre-existing Amphetamine sessions, and subprocess failure.

Fourth, the LaunchAgent must be proven. After installation, run:

    launchctl print gui/$UID/com.herdr.amphetamine.monitor

Expect a printed service description rather than "Could not find service". The monitor log under `~/Library/Logs/herdr-amphetamine/monitor.out.log` must contain a startup line and periodic observations.

Fifth, end-to-end behavior must be proven. Start a real herdr agent that enters `working`. Within the configured poll interval plus start grace, Amphetamine should start. Let the agent finish. Within the stop grace, the monitor should end the Amphetamine session it owns. Record a concise transcript in `Outcomes & Retrospective` when this succeeds.

## Idempotence and Recovery

All scripts must be safe to run repeatedly. Installing the LaunchAgent twice should replace the existing plist and restart the service. Uninstalling when the service is not loaded should not fail the whole script. The monitor should tolerate herdr not running; in that case it should log the condition and treat it as no working agents.

The monitor must not end an Amphetamine session it did not start. This matters because the user may manually start Amphetamine for another reason. On startup, if `session is active` is already true, the monitor should set `owned_session` to false. If agents later stop, it should not call `end session` for that pre-existing session. If agents are working and no session is active, the monitor may start one and set `owned_session` to true.

If AppleScript permission is denied, the monitor should log a clear message telling the user to allow Automation access for the launching process. Depending on how macOS attributes the automation request, the process may appear as Python, Terminal, herdr, or launchd. Do not attempt to bypass macOS permission prompts.

If Amphetamine is not installed at `/Applications/Amphetamine.app`, the monitor should log a clear error and continue sleeping between retries. It may support an environment variable such as `AMPHETAMINE_APP_NAME` or `AMPHETAMINE_APP_PATH`, but do not add that until the default path is proven insufficient.

If closed-display behavior triggers an Amphetamine warning prompt, document the required one-time manual setup: open Amphetamine preferences, visit the Sessions setting for allowing system sleep when the display is closed, toggle it, and choose not to show the warning again if Amphetamine offers that option. Do not automate clicks through that warning.

## Artifacts and Notes

Known Amphetamine AppleScript dictionary excerpts from the target Mac, paraphrased for this self-contained plan:

    session is active
      Returns true or false indicating whether there is an active session.

    start new session with options {duration:integer, interval:hours or minutes, displaySleepAllowed:true or false}
      Starts a new session. For an infinite duration session, use duration 0 and interval 0.

    end session
      Ends the current session. Trigger sessions may restart if Amphetamine triggers are enabled.

    enable closed display mode
      Allows closed-display mode for the current session or future sessions. Amphetamine warns that a prompt may appear the first time this feature is enabled.

    disable closed display mode
      Prevents closed-display mode for the current session or future sessions. This is the command relevant to keeping the system awake when the display is closed.

Known herdr plugin facts from the documentation:

    A plugin is a directory with herdr-plugin.toml.
    Commands are argv arrays, not shell strings.
    Runtime commands receive HERDR_BIN_PATH, HERDR_PLUGIN_ROOT, HERDR_PLUGIN_CONFIG_DIR, HERDR_PLUGIN_STATE_DIR, and context variables when available.
    Herdr does not sandbox plugin commands; they run as the user.

Relevant manual commands for implementation and validation:

    herdr agent --help
    herdr agent list
    herdr plugin --help
    herdr plugin link /absolute/path/to/herdr-amphetamine-macos
    pmset -g assertions
    osascript -e 'tell application "Amphetamine" to session is active'

## Interfaces and Dependencies

The first version should use only macOS built-ins and the installed applications: `/usr/bin/python3`, `/usr/bin/osascript`, `/bin/launchctl`, `/usr/bin/pmset`, the `herdr` binary, and `/Applications/Amphetamine.app`. Do not add Homebrew dependencies, Python packages, or root-level daemons for the first version.

`scripts/amphetamine_ctl.py` should expose these functions:

    def is_amphetamine_available() -> bool:
        """Return true if Amphetamine can be addressed by AppleScript."""

    def is_session_active() -> bool:
        """Return Amphetamine's current session state."""

    def start_session(display_sleep_allowed: bool = False) -> None:
        """Start an infinite Amphetamine session."""

    def end_session() -> None:
        """End the current Amphetamine session."""

    def prevent_sleep_when_display_closed() -> None:
        """Call Amphetamine's disable closed display mode command."""

`scripts/monitor.py` should expose testable pure logic in addition to the daemon entry point:

    def any_agent_working(agent_statuses: list[str]) -> bool:
        """Return true if any status is exactly working."""

    def next_monitor_state(current_state: str, observed_working: bool, elapsed_seconds: float, start_grace: float, stop_grace: float) -> str:
        """Return off, pending_on, on, pending_off, or error based on the observation and elapsed time."""

    def load_state(path: pathlib.Path) -> dict:
        """Load monitor state, returning defaults if the file does not exist."""

    def save_state(path: pathlib.Path, state: dict) -> None:
        """Atomically write monitor state."""

The LaunchAgent installer should expose a command-line interface:

    python3 scripts/install_launchagent.py
    python3 scripts/uninstall_launchagent.py

Both scripts should print what they changed and where logs are stored. They should exit non-zero only for real failures, not for harmless cases like unloading an already-unloaded service.

## Revision Notes

- 2026-07-04 12:15Z / Hermes Agent: Rewrote the prior design into a PLANS.md-style ExecPlan after reading the OpenAI Cookbook PLANS.md guidance. The rewrite adds living-document sections, self-contained context, concrete validation, idempotence guidance, and explicit interface definitions.
