# The installer — `install.ps1`, its steps, and `uninstall.ps1`

`install/install.ps1` installs GameCore for Windows or brings an install up to
date; `install/uninstall.ps1` puts the machine back. One file per step in
`install/steps/`, shared helpers in `install/lib/common.ps1`, the step list in
`install/lib/steps.ps1`. Windows PowerShell 5.1, which every Windows 10/11 has.

The Linux `arch.sh` was one long script by decision upstream: no test covered
it and splitting it untested risked a half-installed box. The Windows one is
split from the start and every step is tested (below).

## Running it

| Command | What |
|---|---|
| `.\install\install.ps1` | interactive: asks where the data goes, the web password, the Windows password for automatic sign-in |
| `.\install\install.ps1 -ConfigFile install.conf -Unattended` | scripted, keys in `install/install.conf.example`; automatic sign-in is left for the owner |
| `.\install\install.ps1 -DryRun` | prints every change it would make, makes none, needs no elevation |
| `.\install\install.ps1 -Only configure-autologon` | one step (or several, comma-separated) |
| `.\install\uninstall.ps1 [-DryRun]` | undo, from the record |

`-CodeRoot`, `-DataRoot` and `-ConsoleUser` override `install.conf`. Defaults:
`C:\Program Files\GameCore` for the code, `<Documents of the console user>\GameCore`
for the data (dev log D6). A data root inside the code root, or relative, is
refused (an update replaces the code root whole); one under OneDrive gets a
warning: ROMs and saves would upload.

The script **elevates itself**: the settings are resolved first, in the user's
own context, and handed to the elevated copy as arguments, so "Documents" is the
console user's even when UAC asks for another account's password.

## The steps, in order

| Step (`install/steps/`) | Does | Uninstall |
|---|---|---|
| `copy-code-root.ps1` | builds the frontend and the shell when the source has neither (needs npm), then `robocopy` of the source to the code root (no `.git`, `node_modules`, `.venv`, `docs`, data folders) and of the packed shell to `<code>\shell` (`electron\dist\win-unpacked` in a checkout, `shell\` in a release archive) | removes the code root, only if this install created it |
| `install-python.ps1` | CPython 3.12.10 from its NuGet package (SHA-512 pinned) into `<code>\python`, a venv in `<code>\.venv`, `backend\requirements.txt` (re-run when its hash changes) | goes with the code root |
| `create-data-root.ps1` | `config`, `config\themes`, `emu`, `emu\covers`, `logs`; seeds each bundled theme whose folder is absent or empty; gives the console user Modify | **never** touches the data root |
| `write-session-env.ps1` | `%ProgramData%\GameCore\session.env`: both roots, the port, the scraper keys; Administrators and SYSTEM only; a key empty in `install.conf` keeps the value already written | removes it |
| `set-web-password.ps1` | when `<data>\config\auth.json` is absent: `WEB_PASSWORD` or a prompt, handed to `backend.services.auth.set_password()` through the environment | kept with the data |
| `install-caddy.ps1` | Caddy 2.11.7 (SHA-512 pinned) in `<code>\caddy`, the `GameCoreCaddy` service, its CA under `%ProgramData%\GameCore\caddy` | removes the service; the CA stays, so phones that trust it keep working after a reinstall |
| `configure-firewall.ps1` | `GameCore - Web access` (TCP 8443) and `GameCore - SSH` (TCP 22), inbound, Private only | removes the rules it made |
| `install-openssh.ps1` | the OpenSSH Server feature, `sshd` automatic and started, Windows' all-profile sshd rule disabled | removes the feature only if GameCore installed it; otherwise restores the start type |
| `configure-power.ps1` | in every power plan: power button = sleep, wake timers off, no password on wake, USB selective suspend off; Fast Startup off | puts every value back |
| `set-wifi-location.ps1` | build 26100+ (24H2) only: location access for desktop apps, or the Wi-Fi scan is empty | puts the values back |
| `configure-autologon.ps1` | automatic sign-in: Winlogon `AutoAdminLogon`, `DefaultUserName`; the password, typed at the console, as the LSA secret `DefaultPassword` (never in the registry in clear, never in `install.conf`); Windows Hello-only sign-in off | deletes the secret, restores the values |
| `register-session-task.ps1` | the `\GameCore\Session` logon task ([1](01-runtime-topology.md#the-logon-task-the-services-the-files-they-read)), and a `GameCore` shortcut on the public desktop and in the Start menu (the way back from Desktop mode) | removes the shortcuts it made, the task, and stops every process running from the code root |
| `build-media-index.ps1` | the offline metadata index (LaunchBox dump, 234 MB) into `<data>\emu\gamescrape`, through `gamescrape.py --refresh`; skipped when present, with `MEDIA_INDEX=0`, or under 700 MB free; a failed build is a warning | kept with the data |

A failed step is reported and the next one runs: a degraded install that says
what failed beats an install aborted halfway. The summary lists the failures;
the exit code is 1 when there was one.

## The record

Every change goes through `Invoke-GcChange` (a dry run lists it and does
nothing) and every value it replaces through `Set-GcRecord` into
`%ProgramData%\GameCore\install-manifest.json`. A value already recorded is
kept on a re-run: the record holds the PC's original setting, not GameCore's
own from the previous run. `uninstall.ps1` runs the steps backwards from it and
deletes a step's entry once undone; a step that fails keeps its entry, so the
next run can retry.

Re-running `install.ps1` changes only what differs: each step reads the current
state first (power values through WMI `Win32_PowerSettingDataIndex`, since
`powercfg`'s text is translated; tasks, services, rules and the registry by
their own cmdlets).

## Tests

`backend/tests/test_install_*.py` run the steps under pytest through
`backend/tests/powershell_harness.py`: one `powershell.exe` per case, the step
files dot-sourced, every cmdlet and native program a step calls replaced by a
fake function that notes its arguments (a function wins over a cmdlet of the
same name). Files go to the test's tmp folder; `GAMECORE_STATE_DIR` moves the
record and `session.env` there. `test_install_entry.py` runs `install.ps1
-DryRun` and `uninstall.ps1 -DryRun` for real and checks they create nothing.
`Invoke-GcChange` runs its block in its own scope, so its parameters carry a
`GcChange` prefix: one called `$Action` hid a step's own `$action` inside the
block. The `.ps1` files stay ASCII: Windows PowerShell reads a file without a BOM in
the ANSI code page.

## What has not been run

The installer has never run for real: it changes the machine it runs on, and
the only honest test is a PC that has never had GameCore. The dry run has, on
the owner's PC. Owner actions and open questions are in the dev log.

Not ported yet: the graphical installer (`install/installer-gui`, Linux), the
catalogue packs and their install pipeline, the addons (step 9), the OTA
updater (step 8).
