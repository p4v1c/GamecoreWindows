# Windows port — dev log

GameCore for Windows: the Linux console front-end (`p4v1c/GamecoreRenew`)
ported to Windows 10/11 x64. **A port, not a rewrite**: every file that is not
OS-specific stays as it is; OS calls move behind `backend/services/platform/`.

Resume point for anyone picking the work up: read **Status**, then **Next**.

## Status

| Step (brief §3) | State | PR |
|---|---|---|
| 1. Repo, skills, docs read, dev log, CI skeleton | merged | #1 |
| 2. Platform layer + paths, backend passes the ported suite | merged | #3 |
| 3. Process / session management (launch, suspend, resume, kill) | merged | #4 |
| 4. Electron shell + frontend fullscreen, 5 themes, boot animation and sounds; the bezel overlay leaves | merged | #5 |
| 5. Pads (SDL), standby, display, audio, Wi-Fi, Bluetooth, storage, logs | merged | #6 |
| 6. Rest mode (sleep, wake by pad, splash after wake) | merged | #7 |
| 7. Services, Caddy, SSH, firewall, installer + uninstaller, autologon | merged | #8 |
| 8. OTA update + rollback, release workflow | merged | #9 |
| 9. Addons repo (`p4v1c/gamecore-addons-windows`), the `gamecore-addon` CLI in Python | in review | addons #1, this repo: see below |
| 10. Data import script, README and website | — | — |

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The repository starts from `GamecoreRenew` `main` at `d30cd33`, history kept, **upstream tags not carried** | history is the documentation of every rule; the tags are Linux releases, and `next_version.py` would continue the Linux numbering |
| D2 | Windows releases start at `v0.1.0` and reach `v1.0.0` at feature parity | a box only follows this repository's tags, so any start works; `0.x` says "port in progress" |
| D3 | Embedded **Python 3.12** in the install, 3.12 in CI | the CPython embeddable zip exists per version; 3.12 has wheels for every dependency the port adds (`pywin32`, `pysdl2-dll`, `pycaw`, `winsdk`) |
| D4 | Every OS call lives in `backend/services/platform/`, one module per concern, with the function names the services call today | the brief's rule; a reviewer greps for `os.name`, `ctypes.windll`, `win32*` outside it (`gamecore-review`) |
| D5 | Installer: `install/install.ps1` + one `install/steps/<verb>-<noun>.ps1` per step, self-elevating, unattended mode reading `install.conf` | replaces `install/arch.sh`, which was monolithic by decision; the Windows one is split from the start |
| D6 | Default roots: code `C:\Program Files\GameCore`, data `Documents\GameCore`, both changeable at install (an NTFS partition such as `D:\GameCoreData`) | owner's preference: Windows paths, not `/opt` + `/userdata` |
| D7 | Linux code is removed **with** its Windows replacement, PR by PR, never in one sweep | every PR keeps a tree whose tests mean something; a Linux-only test goes with the code it tested |
| D8 | Session model — **confirmed at step 3**: the backend runs in the console user's session from a logon task (highest privileges) with a watchdog; Caddy and OpenSSH stay services. The task itself is step 7 | a service lives in session 0 and cannot show a window on the user's desktop (session 0 isolation); a per-session launcher agent would add a second process and an IPC channel for the one call the logon task makes directly. Evidence: the real `process_manager` run from a process in this PC's console session (as a logon task would be) opened a game window on the screen, froze, thawed and killed it, raised it and the interface over it, made it borderless fullscreen ("Tested on the real Windows PC"). `session.launch_blocker()` refuses a launch when the backend is not in the console session, so a misinstalled service says so instead of starting invisible games. Not tested: an actual logon task, and a service (nothing installed on this PC, step 7) |
| D9 | `GAMECORE_DATA` unset still means the code root; the installer always sets both roots (step 7). `paths.py` holds no Windows default folder | on an installed PC the code root is not writable by the console user, so "unset" only happens in a checkout (dev, tests, dev server), where the checkout is the right answer. A `Documents\GameCore` default in code would point a developer's checkout at the real data of a GameCore installed on the same PC |
| D10 | File names match case-insensitively, as NTFS does: `Back.PNG` is the `back` picture of a pack (`test_pack_art.py`) | the brief's rule; on Linux the same file was ignored only because `glob("*.png")` is case-sensitive there |
| D11 | Tests of Linux code a later step replaces carry `linux_until_port(step)` and are skipped on Windows with the step named; the full list is [`windows-port-linux-tests.md`](windows-port-linux-tests.md), checked against the source by `test_linux_until_port.py`. A test whose code is already gone is deleted, not marked | D7 says the test goes with its code; the marker keeps the count visible until then, and a mark missing from the list fails the suite |
| D12 | Folder links are NTFS **junctions** (`platform/filesystem.py`), never symlinks | a symlink needs admin or Developer Mode (`WinError 1314`); a junction needs neither and every program follows it. Limit: the target is a local absolute directory that exists |
| D13 | Boot identity = the registry `BootId` counter; process start time = `psutil` creation time (`platform/processes.py`) | `BootId` is exact and readable without admin; `psutil.boot_time()` is derived from the uptime and drifts by a second between calls, which would refuse to adopt a game the backend did start |
| D14 | The backend and every Python tool run in **UTF-8 mode** (`PYTHONUTF8=1`): CI sets it, the installer and the logon task set it for the backend (step 7) | without it Python decodes a file read with no `encoding=` as the ANSI code page, cp1252 on the runner and on a French Windows: the themes, the README, the scripts' own output (`→`) fail. Hundreds of calls rely on the Linux default; UTF-8 mode is that default back, and Python's own future one (PEP 686) |
| D15 | The content types the backend serves are pinned by `platform/media_types.py`, not read from the registry | CI served `.webp` as `application/octet-stream`; some PCs give `.js` as `text/plain`, which blanks the interface |
| D16 | Tests of the Linux process code whose subject is bookkeeping (what is suspended, refused, broadcast) run on Windows through the `posix_boundary` test fixture (`os.killpg`, `os.getpgid`, `os.getuid`, the three signals, played by psutil) instead of carrying `linux_until_port` | the brief's "make the pure logic run": 109 more tests run today, and step 3 has a passing suite to move onto the Job Object code. Only tests that need a real POSIX tool or file system stay marked |
| D17 | A game is a **Job Object**: started suspended, assigned, resumed (`platform/jobs.py`); suspend/resume = `NtSuspendProcess`/`NtResumeProcess` on every process of the job, all or nothing; kill = `TerminateJobObject`. **No `KILL_ON_JOB_CLOSE`** (the brief asked for it): the game's first process holds a duplicate of the job handle, so the job's name survives a backend restart and is how `session.json` finds the game again | kill-on-close kills the game whenever the backend restarts or crashes, which breaks the 09-gotchas invariant "never kill at shutdown, adopt after restart". A job's name dies with its last handle (measured: `OpenJobObject` fails once the creator closes it); with the handle in the game, a restarted backend reopens it (measured, two separate processes). Limit: a game whose first process exited (a launcher that quit) is not adopted after a restart, as on Linux |
| D18 | A session ends when its **job is empty**, not when its first process exits; the watcher polls the job every 0.5 s | a launcher that starts the game and exits is common on Windows; polling keeps the watcher independent of the event loop type and needs no thread that would block shutdown |
| D19 | Suspending raises the interface **before** freezing the game; `bring_to_front()` never attaches to a thread that does not answer `WM_NULL` in 200 ms; suspends and resumes are serialised | measured on this PC: with a frozen game window in front, `AttachThreadInput` + `BringWindowToTop` blocked for as long as the game stayed frozen, and without attaching Windows refused the foreground. In front of a running game, the attached call works |
| D20 | Launcher args are split with shell quotes but **backslashes kept** (`command_line.split_args`); the catalogue's `args` format is unchanged | POSIX `shlex.split` turned `C:\Games\x.iso` into `C:Gamesx.iso`; `Popen` then quotes each argument for Windows |
| D21 | `gamepadTrigger` is ignored (no udev step); the key stays in the schema for the Linux packs | it re-fired udev for Flatpak sandboxes; Windows has neither. The packs stay unchanged until they are rewritten for Windows (owner's decision) |
| D22 | The bezel overlay leaves **whole** (owner, 20/10): Electron window, `overlay_monitor`, backend router and services, `config/overlays.json`, the shipped PNGs, the per-game bezel choice. The pack schema keeps its `overlay` field and `roms.consoles`, read by no Windows code | the Linux packs stay untouched until they are rewritten for Windows (owner); a schema field removed now would fail their validation |
| D23 | electron-builder `--win`, target `dir` (an unpacked `GameCore.exe` folder) | the installer (step 7) copies the folder into the code root; NSIS or portable would only be unpacked again. `productName` stays `GameCore`, the image `window_focus` looks for |
| D24 | Only `GAMECORE_MANAGED=1` stops the shell from starting its own backend; the logon task (step 7) must set it | systemd's `INVOCATION_ID` was the Linux signal and does not exist here; unset now means "a checkout run by hand" |
| D25 | Chromium's device scale factor is pinned to 1 | Settings → Display → Scale (a page zoom) is the interface's only zoom, as under X11; Windows scaling a 4K TV to 150 % would apply on top |
| D26 | Desktop mode starts `explorer.exe` when the session has none, then quits; the HUD toast is shown with `showInactive()` | the console session will run without Explorer (step 7). A shown window activates on Windows: the toast would pull a game out of fullscreen |
| D27 | Wi-Fi, radios and Bluetooth go through **WinRT** (pywinrt), audio through **Core Audio** (pycaw), addresses and USB devices through **WMI**, pads through **SDL2 in the backend** (pysdl2 + its bundled `SDL2.dll`); never `netsh` text | `netsh` prints in the display language (French on the owner's PC): a parser written against English reads nothing. The typed APIs answer the same on every Windows |
| D28 | Bluetooth Connect / Disconnect report the state instead of changing it | Windows connects a paired device when the device asks and has no public call to force or drop one device's link. `/connect` is ok for a connected device and says how to connect the others |
| D29 | Standby switched off **hands Windows its own timers back**; nothing re-takes them (the Linux "X arm" held even with standby off has no counterpart) | on Windows the plan's display and sleep timeouts are the only idle timers; games and Chromium video hold the display themselves. Standby on: GameCore owns them (set to never, previous values noted) |
| D30 | The sleep stage switches to the **Power saver** plan when the PC has one and puts the previous plan back, noted on disk (`power-plan-handoff.json`) | Windows' equivalent of the `cpupower` governor; the plan is system state and outlives the backend, so the way back cannot live in memory |
| D31 | A display mode is applied for the session only and written to the registry on confirm | an unconfirmed mode (a black screen) must not survive a restart, exactly what the revert timer exists for |
| D32 | Battery = SDL2's power **level** mapped to 5 / 20 / 60 / 100 %; a wired pad lists no battery | SDL2 has no percent (SDL3 does); the bands land on the 25 % and 5 % alerts. Exact percent (DualShock HID report or SDL3) left as an open question |
| D33 | A pad's device id is `sdl<instance>`, its registry key the Bluetooth address when SDL reports one | SDL gives a new instance on every connection, like a new `/dev/input/eventN` on Linux; the address keeps the slot, as the MAC did |
| D34 | Rest mode sleeps by the PC's model (`platform/sleep.py`): **S3** = `SetSuspendState`; **Modern Standby** = every game closed and the screen off, the PC left running, and the next pad press (any, at once) is the wake | measured on this Modern Standby PC: `SetSuspendState` hibernated it (S4, about 30 s to wake, and a pad cannot wake S4), and no call enters Modern Standby itself |
| D35 | Every sleep's sweep runs inside Windows' suspend event: its power thread is held up to 1.8 s while the jobs are terminated (0.8 s, then 0.3 s) | Windows sleeps about 2 s after the event, answered or not; there is no delay lock like logind's. Terminating a job is immediate, so the sweep fits |
| D36 | Wake permission: the Bluetooth radios, the Bluetooth pads on the roster and the wired pads are allowed to wake the PC (WMI `MSPower_DeviceWakeEnable`, elevated); nothing is disarmed | on Windows a Bluetooth mouse wakes through the same radio as the pads, so it cannot be told apart at that level; disarming USB keyboards and mice would take away wake sources the owner may rely on |
| D38 | Python comes from CPython's **NuGet package** (3.12.10, SHA-512 pinned) into `<code>\python`, with a venv in `<code>\.venv` (refines D3) | the embeddable zip has no `venv` and no `pip`; the shell and the session expect a venv. 3.12.10 is the last 3.12 with a binary package |
| D39 | One supervisor, `install/bin/gamecore-session` (stdlib Python, `pythonw.exe`), run by the `\GameCore\Session` logon task: backend, then the shell at once (it waits for `/api/ready` itself); restart 5 s after a crash, 60 s after five in two minutes; a clean shell exit is Desktop mode and is not undone; the `GameCore` shortcut runs it again unelevated and it only signals the running copy | the Linux units' `Restart=on-failure`, without a failed state nobody can reset on a console. The signal crosses integrity levels: the mutex and the event carry a descriptor for the interactive user (medium label), or the shortcut would start a second backend |
| D40 | The backend gets a console that is never shown (`CREATE_NO_WINDOW` from the session) instead of a flag on every subprocess call | its console children (powercfg, wevtutil, tasklist, the SDL probes) inherit the hidden console; measured: a child of a hidden process has a console and no window |
| D41 | Desktop mode tells the backend (`POST /api/standby/desktop`): Windows' idle timers go back to Windows and the standby watcher stands down until the shell starts again | the watcher re-claimed the timers every minute, so a desktop left alone never turned its screen off |
| D42 | Explorer stays the console user's shell; GameCore's kiosk covers it | replacing Winlogon `Shell` boots without Explorer, but an `explorer.exe` started later may then open a folder window, not the desktop Desktop mode needs. Untested without a spare PC, so the conservative choice |
| D43 | The install record is `%ProgramData%\GameCore\install-manifest.json` (first values only); `session.env` beside it, Administrators and SYSTEM only (scraper keys); Caddy's CA in `%ProgramData%\GameCore\caddy`, kept by uninstall; OpenSSH removed only if GameCore installed it | uninstall puts back the PC's own settings, not GameCore's previous run; phones that trust the CA keep working after a reinstall |
| D44 | Automatic sign-in: the password is typed at the console when the step runs and stored as the LSA secret `DefaultPassword`; an unattended install skips it and says which command to run | never in the registry in clear, never in `install.conf`, never typed by an agent |
| D45 | Power, in every plan (GameCore switches to Power saver, D30): power button = sleep, wake timers off (AC and DC), no password on wake, USB selective suspend off; Fast Startup off. Read through WMI, written with `powercfg` | the step-6 hand-offs; WMI because `powercfg`'s text is translated |
| D46 | The data root gets `config`, `emu\covers`, `logs` and the bundled themes (a theme folder that exists and is not empty is the player's); no `systems.json`/`apps.json` | the generated tiles are the Linux packs' (Flatpak launchers); the backend reads a missing file as "no system installed yet" |
| D47 | Tests of the Linux packs carry `linux_until_port(11)`: not a port step, "rewritten for Windows after the port" | they were filed under step 7 (installer), but the packs are not rewritten by it (owner, 19/10) |
| D48 | The updater runs in two stages: `update/windows.py` (stdlib, from the backend) downloads, checks and unpacks beside the code root; `update/apply-update.ps1` (Windows PowerShell, detached, from `%ProgramData%\GameCore\update`) stops GameCore and Caddy, swaps the folders by rename and carries `python\`, `.venv\`, `caddy\` over | Windows refuses to rename a folder a running program lives in, and all four run from the code root; a rename is atomic and costs no space, where a copy over files in use fails half-way |
| D49 | One release asset, `gamecore-ota.zip` (+ `.sha256`), in the code root's layout, serves the update and a fresh install; no separate "full" archive, no installer exe | the Windows code root holds the assets too, so a full archive would be the same bytes; the graphical installer is still an open question (step 7) |
| D50 | Integrity = the published SHA-256 and GitHub's own asset digest, over HTTPS; no signature | signing needs a key held by the owner in a repository secret (open question); the catalogue's signed channel is unchanged |
| D51 | Rollback is automatic when anything fails after the swap or the new backend does not answer `/api/ready` within 180 s (any status, a 503 "starting" counts); by hand, `apply-update.ps1 -Rollback`. One snapshot, `<code>.prev` | an import error or a missing dependency leaves a console with no interface and nobody to type a command |
| D52 | `release.yml` runs on pushes to `main` only (no `workflow_dispatch`, no tag trigger), queued by `concurrency: release`, after `ci.yml` (`workflow_call`), and publishes with `gh release create`; first tag `v0.1.0`, a checkout says `v0.0.0` | the Linux workflow's two hazards (a dispatch from a branch published a release; two merges in a row collided on the tag); D2 |
| D53 | The updater refuses a code root that is a git checkout, and downloads nothing when the box is up to date (`--force` reinstalls) | swapping a developer's checkout for an archive loses their work; a restart for nothing is a minute without the TV |
| D37 | Pads stay on into a sleep (Linux disconnected them first) | no public Windows call drops one device's link (D28); an Xbox pad paging back after the sleep, which woke the Linux box, is untested here |

## Baseline on Windows (19/10/2026, before any port change)

Measured on the Windows 11 PC with the copied tree, Python 3.13:

| Check | Result |
|---|---|
| `ruff check .` | green |
| `scripts/check-catalog.py` | green, 42 packs |
| `scripts/gen-catalog.py --check` | green |
| `scripts/check-docs.py --all` | 39 false failures (backslash parents) → fixed in this PR |
| `shellcheck -S warning` | green (shellcheck-py) |
| `pytest backend/tests catalog` | **cannot collect**: `conftest.py` symlinks `catalog/` into the test root (`WinError 1314`, symlinks need admin or Developer Mode) — step 2 |
| file-size check | 3 FAIL already upstream: `catalog/rpcs3/files/rpcs3_smart.py` (1233), `electron/main.js` (1147), `backend/services/process_manager.py` (933). The port touches the last two: each must shrink, not grow |
| frontend `npm run test:run` + `npm run build` (Node 22.23.3) | green: 89 files, 777 tests; bundle built |
| electron `npm test` | green: 38 tests |
| `git ls-files` | no symlink, no case collision, no reserved character in a tracked name |

## After step 2 (19/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1` (D14),
`pytest backend/tests catalog -m "not network"`; CI on `windows-latest`,
Python 3.12, gives the same. The process-group tests run through the
`posix_boundary` fixture (`docs/TESTING.md`), not marked:

| | Test items |
|---|---|
| The tree as ported from Linux (all of them run on the Linux box) | 2 901, 4 of them network |
| Windows before this step | 0: the suite could not be collected |
| Windows after this step: passed | 2 533 |
| skipped: Linux code a later step replaces (`linux_until_port`, D11) | 355 in 54 files ([list](windows-port-linux-tests.md)): 222 marked tests + 133 in the 6 Linux pack files Windows cannot import |
| skipped on missing data or tools, as on Linux (ImageMagick, a frontend build, a release tag, seeds with no controller block) | 54: `test_bezels.py` 27 (ImageMagick; on Linux they ran where it was installed, and bezels leave the port), `test_generator_contract.py` 18 and `test_catalog_consumers.py` 4 (data conditions, identical on Linux), `test_theme_versions.py` 5 (no release tag: D1 did not carry the tags; they run again once step 8 tags) |
| deselected (network) | 4 |
| deleted: their code left in step 1 (`release.yml`) or here (`/proc` parsing) | 4 |
| added: platform layer, home isolation on Windows, Windows roots, the marker, media types | 49 |

Deleted tests, and what the step that brings their code back checks again:

| Test | Why it went | Re-checked by |
|---|---|---|
| `test_catalog_consumers.py::test_every_helper_the_installers_call_is_shipped` | read `.github/workflows/release.yml`, removed in step 1 | step 8: every directory the installer calls is in both archives |
| `test_catalog_consumers.py::test_an_update_never_touches_the_players_bezels` | same | step 8: the OTA archive carries no `assets/`, the updater writes nothing under `assets/overlays/` |
| `test_version_scheme.py::test_the_workflow_computes_the_tag_with_this_script` | same | step 8: the tag comes from `scripts/next_version.py`, no unbounded tag action |
| `test_session_adoption_identity.py::test_start_time_reads_field_22_past_a_comm_with_spaces` | parsed `/proc/<pid>/stat`, replaced by `platform/processes.py` | `test_platform_processes.py`, now |

Other checks, same day:

| Check | Result |
|---|---|
| `ruff check .` | green |
| `scripts/check-catalog.py`, `scripts/gen-catalog.py --check` | green |
| `scripts/check-docs.py --all` | green |
| file-size `--diff` | no file over budget grew; the 3 upstream FAILs untouched |
| `python -m compileall -q backend` | green |
| frontend `npm run test:run` + `npm run build`, electron `npm test` | green, unchanged: 777 vitest tests, bundle built, 38 Electron tests |

## After step 3 (19/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1`,
`pytest backend/tests catalog -m "not network"`:

| | Before (main at `e9094a7`) | After |
|---|---|---|
| passed | 2 532 | 2 567 |
| skipped | 283 | 280 |
| deselected (network) | 4 | 4 |
| `linux_until_port` items ([list](windows-port-linux-tests.md)) | 355 in 54 files | 352 in 52 files (−5 step 3, +2: the lutris test below) |

Deleted with their code: the X11 display probe tests (`test_session_robustness.py`,
6, and `test_gamecore_session.py`, 2: the backend read `session.env`), the
python-xlib window tests (`test_window_focus_multi.py`, 2), the udev re-fire
tests (`test_usb_launch_gate.py`, 2), the thaw-before-SIGKILL tests (replaced
by "terminate the frozen job as it is"), the pgid-signal permission test.
Ported onto Job Objects: every test that ran through the process half of
`posix_boundary` (the fixture keeps `getuid`/`geteuid` only, for steps 5
and 7). New: `test_platform_jobs.py`, `test_platform_window_control.py`,
`test_session_desktop.py`, `test_command_line.py`, `test_window_focus.py`,
`test_event_loop.py`. One pack test is marked step 7
(`catalog/lutris/tests/test_fullscreen.py`: X11 `WM_CLASS` matching).

## After step 4 (20/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1`, Node 22.23.3:

| | Before (main at `ebd3ffb`) | After |
|---|---|---|
| pytest passed | 2 568 | 2 332 |
| pytest skipped | 279 | 271 |
| vitest | 777 | 755 |
| Electron bench | 38 | 42 |
| `linux_until_port` items ([list](windows-port-linux-tests.md)) | 352 in 52 files | 371 in 51 files (−2 step 4, −1 bezel seed test, +22 the sudoers guard, step 7) |

The pytest drop is the overlay code's own tests, removed with it (D7):
`test_bezels.py`, `test_bezel_capture.py`, `test_bezels_consoles.py`,
`test_overlay_monitor.py`, `test_overlay_naming.py`,
`test_overlays_router.py`, plus the bezel cases of five other files; the
vitest drop is `OverlayScreen` and the bezel rows of the options panel.
`main.js` 1 147 → 499 lines: no longer over the file-size budget.

## After step 5 (24/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1`, Node 22.23.3:

| | Before (main at `4f7b0ab`) | After |
|---|---|---|
| pytest passed | 2 332 | 2 375 |
| pytest skipped | 271 | 245 |
| vitest | 755 | 755 |
| Electron bench | 42 | 42 |
| `linux_until_port` items ([list](windows-port-linux-tests.md)) | 371 in 51 files | 345 in 44 files (all 22 step-5 items gone with their code; the Flatpak branch lookup moves to step 7 with the Linux packs; one Rest-mode test lost its subject) |

Deleted with their code: the `wpctl`, `nmcli`, `bluetoothctl`, `xrandr` /
`kscreen-doctor` parsers' tests, the X11 idle-timer arm
(`test_standby_x11_timers.py`), the evdev discovery and sysfs battery /
USB / disk fixtures, the evdev layout walk (`sdl_layout` over key codes, its
`/dev/uinput` verifier `scripts/verify-sdl-layout.py`) and the node grouping
(`nodes_by_key`). New: `test_platform_power.py`, `fake_display.py`,
`fake_volumes.py`, and the platform cases in each settings suite. Known flaky
here, not "fixed" (owner rule): `test_launch_reconcile` timing under load, and
`test_platform_window_control` (3 tests) whenever this PC's desktop refuses the
foreground; both fail the same way on the step-4 tree.

## After step 6 (24/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1`, Node 22.23.3:

| | Before (main at `3a66636`) | After |
|---|---|---|
| pytest passed | 2 374 | 2 389 |
| pytest skipped | 246 | 236 |
| vitest | 755 | 755 |
| Electron bench | 42 | 42 |
| `linux_until_port` items ([list](windows-port-linux-tests.md)) | 345 in 44 files | 338 in 42 files (all 7 step-6 items gone with their code) |

Deleted with their code: `test_rest_mode_install.py` (sudoers rule, wake
helper, boot unit, udev rule, logind drop-in) and `test_wake_setup_helper.py`
(the bash sysfs helper). Rewritten on Windows' mechanisms: `test_resume.py`
(power events fired from a thread through a fake `watch()`),
`test_sleep_guard.py`, `test_bt_wake.py`, `test_wake_source.py`. New:
`test_platform_sleep.py`, `test_standby_rest_wake.py`. Known flaky, not
"fixed": `test_platform_window_control` (3) failed in two full runs and
passed in the last one; it failed on `origin/main` too at the same moment
(checked in a worktree): this desktop refused the foreground;
`test_launch_reconcile` (2) failed once at 0.501 s against
0.5 s while the frontend suites ran alongside, and passed alone and in the
next full run.

## After step 7 (24/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1`, Node 22.23.3:

| | Before (main at `77c14fc`) | After |
|---|---|---|
| pytest passed | 2 388 | 2 455 |
| pytest skipped | 237 | 112 |
| vitest | 755 | 755 |
| Electron bench | 42 | 44 |
| `linux_until_port` items ([list](windows-port-linux-tests.md)) | 338 in 42 files | 178 in 31 files (step 7's installer and session items gone with their code; the packs' 142 under 11) |

New: `test_install_common.py`, `test_install_system_steps.py`,
`test_install_session_steps.py`, `test_install_entry.py` (the PowerShell
installer through `powershell_harness.py`), `test_install_media_index.py` and
`test_gamecore_session.py` (rewritten), `test_standby_desktop.py`. Deleted with
their code: the session, desktop-trip, session-install, session-migrate,
GPU, manifest, secrets, read-only-root and sudoers tests of `arch.sh` and the
X/SDDM scripts. `test_platform_window_control` (known, environmental) failed in
one full run and passed in the next.

## After step 8 (24/10/2026)

Measured on the Windows 11 PC, Python 3.13, `PYTHONUTF8=1`, Node 22.23.3:

| | Before (main at `dd4b82e`) | After |
|---|---|---|
| pytest passed | 2 456 | 2 494 (CI, Python 3.12: 2 492 passed, 86 skipped) |
| pytest skipped | 112 | 84 |
| vitest | 755 | 755 |
| Electron bench | 44 | 44 |
| `linux_until_port` items ([list](windows-port-linux-tests.md)) | 178 in 31 files | 154 in 23 files (step 8's 24 removed with the Linux updater or ported) |

New: `test_ota_stage_one.py`, `test_ota_stage_two.py`, `test_release_package.py`;
ported: `test_update.py`, `test_ota_merge_roots.py`, `test_version_scheme.py`.
Deleted with their code: the `update/linux.sh`, sudoers-step and
`_shared`-retirement tests.

## Done

- Both originals cloned read-only; the clone's `upstream` remote has its push
  URL disabled.
- Skills copied from `GamecoreRenew` and adapted (first commit): PowerShell
  commands, `install/install.ps1`, the platform-layer rule, PRs have CI,
  never push to the originals; `check-file-size` ported to Python (same output
  as the bash one on this tree); the legibility audit finds Chrome or Edge.
- `.gitattributes`: LF everywhere (Git for Windows defaults to
  `autocrlf=true`), CRLF for `.cmd`/`.bat`.
- `scripts/check-docs.py` fixed for Windows paths.
- `ci.yml` on `windows-latest`, frontend and Electron suites included
  (green on Windows unchanged); the Linux `release.yml` and
  `verify-catalog.yml` removed.
- Docs read: `README.md` (outline), `CONTRIBUTING.md`, `docs/TESTING.md`,
  `docs/architecture/01`–`13`. `docs/themes/README.md` and the README's user
  manual are read in full at step 4, before the frontend is touched.

- Step 2: `backend/services/platform/` (`filesystem.py`: junctions;
  `processes.py`: boot id, start time), called by `process_identity.py` and
  `profile_save_paths.py`. `conftest.py` links `catalog/` with a junction and
  points HOME, USERPROFILE, APPDATA and LOCALAPPDATA into the throwaway root
  (on Windows `Path.home()` reads USERPROFILE: the HOME guard alone protected
  nothing). The installer modules load on Windows (`pwd`/`grp` guarded), so 56
  of their 73 tests run. Tests of the Linux process code (`process_manager`,
  `session`, `rest_mode`, the guide gesture, Bluetooth) run through the
  `posix_boundary` fixture instead of being marked. `paths.py` documents the Windows roots;
  `requirements.txt` makes evdev and python-xlib Linux-only and adds `psutil`;
  `ci.yml` gains the `backend` job.

- Step 3: games in Job Objects (`platform/jobs.py`), the console session
  (`platform/console_session.py`), windows (`platform/window_control.py`);
  `process_manager.py` 933 → 597 lines (`game_session.py`,
  `command_line.py` extracted); `session.py`, `window_focus.py`,
  `fullscreen_enforcer.py` rewritten for Win32; `launch.py` without the udev
  step; `rest_mode.close_everything()` on jobs. The X11/pgid code went with it.

- Step 4: the bezel overlay leaves (D22) — Electron window and IPC, the
  `/overlay` page and `useEmulatorOverlay` (its session-screen claim kept as
  `useSessionScreen`), the overlays router, `bezels`, `bezel_capture`,
  `consoles`, `overlay_monitor`, `config/overlays.json`, `assets/overlays/`.
  Electron on Windows: HUD in `electron/hud.js`, never activated (D26);
  `shutdown /r|/s /t 0`; device scale 1 (D25); `.venv\Scripts\python.exe`
  and `GAMECORE_MANAGED` (D24); Desktop mode in `electron/desktop.js` (D26);
  pointer hidden over the interface; a sign-out (`session-end`) ends the shell;
  `electron-builder --win` (D23). Doc 06 renamed `06-electron-shell.md`.

- Step 5: `backend/services/platform/` gains `audio.py`, `com.py`,
  `network.py`, `wifi.py`, `radios.py`, `bluetooth.py`, `display.py`,
  `power.py`, `volumes.py`, `gamepads.py`, `usb.py`. Settings routers (audio,
  Wi-Fi, Bluetooth, display) keep their endpoints and shapes. Standby and
  `desktop_power` own Windows' screen, idle timers and plan; storage links
  drives with junctions and ejects through PnP; pads, the guide gesture, the
  chord, standby's activity, the wizard and batteries read SDL2 in the
  backend; the top bar's IP skips VPN and virtual adapters; a log purge
  empties what a suspended game still holds. `conftest.py` now keeps every
  machine-changing call out of reach for the whole session. `evdev` and
  `python-xlib` left the requirements.

- Step 6: `backend/services/platform/` gains `sleep.py` (sleep model, sleep,
  suspend / resume / console display events as callbacks, the wake log) and
  `device_wake.py` (WMI wake settings). Rest mode sleeps through it (D34);
  `sleep_guard` sweeps inside the suspend event (D35); `resume` reads the
  events, Modern Standby's display included; `bt_wake` arms the radio and
  the pads (D36), turns no pad off (D37); `wake_source` reads the System
  log. Standby lights the screen at the first press during Rest mode. The
  Linux root pieces (sudoers rule, `gamecore-wake-setup`, its unit and udev
  rule, the logind drop-in) left with their tests.

- Step 7: `install/install.ps1` (self-elevating, `-DryRun`, `-Only`,
  `-Unattended` with `install.conf`), `install/uninstall.ps1`, 13 steps in
  `install/steps/*.ps1` ([11](../architecture/11-install-script-seams.md)):
  code root, Python, data root, `session.env`, web password, Caddy service,
  firewall, OpenSSH, power, Wi-Fi location (24H2), automatic sign-in, the
  logon task and the `GameCore` shortcut, the offline metadata index.
  `install/bin/gamecore-session` supervises the backend and the shell. Desktop
  mode hands Windows its idle timers back. `arch.sh`, `uninstall.sh`, the
  X/SDDM session scripts and `electron/start-ui.sh` left with their tests.
  CI lints `install/` with PSScriptAnalyzer.

- Step 8: `update/windows.py` (stage 1), `update/apply-update.ps1` (stage 2
  and `-Rollback`), `update/finish.py` (themes, catalogue merge), D48-D53;
  the backend follows `p4v1c/GamecoreWindows` and `gamecore-ota.zip`;
  `.github/workflows/release.yml` and `scripts/package-release.py`; the
  installer accepts the release archive. Found on the way and fixed: `npm run
  pack` could not run (electron listed as a dependency), and the installer's
  `Invoke-GcNative` threw on any stderr line (a pip or npm warning). The Linux
  updater, its sudoers step and `gamecore-restart` left with their tests. CI
  lints `update/` too.

- Step 9: `install/bin/gamecore-addon` rewritten in Python (stdlib) with a
  `gamecore-addon.cmd` shim: the repo arrives as GitHub's archive of
  `p4v1c/gamecore-addons-windows` (no git on the box), hooks are
  `install.ps1` / `uninstall.ps1` run by Windows PowerShell 5.1, the data root
  comes from the environment or `session.env`, addon venvs are built on a copy
  of the core's Python in `<DATA>\addons\_python` (the updater kills
  processes running from the code root), and `update` stops every addon
  before swapping the repo. `routers/addons.py` runs it on its own
  interpreter. The addons themselves, their scheduled tasks, Windows emulator
  folders and their dev notes (tested, not tested, open questions) are in the
  addons repository's README. The bash CLI's 9 Linux-only test items left
  with it; `test_addon_contract.py` now runs real PowerShell hooks.

## Next

Step 10: `scripts/import-linux-data.py` (an existing `/userdata` tree made the
Windows data root without loss, with a report of what could not be mapped),
the README's user manual and the website for the Windows edition.

## Open questions for the owner

- Step 9, conservative choices made (details in the addons repo README): the
  addon tasks run elevated like the backend; `install` of a new addon does
  not refresh the repo while others run from it (`update` first);
  `uninstall.ps1` does not remove the addons' tasks yet (run
  `gamecore-addon remove` for each before uninstalling GameCore).
- Step 9: merging this PR publishes a release (it touches `install/bin` and
  `backend/`); the addons repo has no release flow, its `main` is what
  `gamecore-addon` downloads.

- Step 8: **merging #9 publishes the first release, `v0.1.0`** (it touches
  shipped paths), and every later merge to `main` publishes another. Merge one
  PR at a time and wait for its "Build and Release" run.
- Step 8, conservative choices made: D50 (no signature: sign the archive with
  a key kept in a repository secret?), the shell is not code-signed
  (SmartScreen may warn on a fresh install: buy a certificate?), D49 (no
  installer exe until the graphical installer is decided), no "last update"
  or rollback button in Settings (the outcome is in
  `%ProgramData%\GameCore\update\last-update.json` and
  `<data>\logs\update\update.log`; say if the UI should show it).
- Step 8, owner action once a box is installed: Settings → Update to the next
  release; then, from an elevated PowerShell,
  `& 'C:\Program Files\GameCore\update\apply-update.ps1' -Rollback` and back
  with another update. Check `last-update.json` after each.

- Step 7, conservative choices made: D42 (Explorer stays the shell: should the
  console account boot without it?), D44 (automatic sign-in asks the password
  at the PC; with a Microsoft account, `USER_NAME` may have to be the account's
  e-mail, untested), D46 (no tiles seeded until there are Windows packs).
- Step 7, not done (say if wanted, and when): Windows Update active hours /
  restart deferral, the Xbox Game Bar on the Guide button, notifications and
  Focus Assist while GameCore is in front, the lock-screen policy (Home ignores
  it), the graphical installer (the Linux `install/installer-gui` is still in
  the tree, `gen-catalog.py` writes its catalogue), the CLIs `gamecore-theme`
  (and `gamecore-emu` with the packs, `gamecore-addon` at step 9), and the
  Linux `scripts/check-install.sh` / `boot-timeline.sh`. The README's user
  manual still describes the Linux box (step 10).
- Step 7, owner action: the installer has only run as a dry run. A real run on
  a PC that never had GameCore (or this one, knowing it changes its power plan,
  sign-in, firewall and tasks), from an elevated or normal PowerShell in the
  checkout: `.\install\install.ps1 -DryRun`, then `.\install\install.ps1`, a
  restart, then `.\install\uninstall.ps1` to check the way back.

- Step 6, conservative choices made: D34 (a Modern Standby box "sleeps" with
  the screen off and the PC running, since `SetSuspendState` hibernates it;
  hibernating instead would save power but only the power button could wake
  it), D36 (nothing disarmed: should mice and keyboards lose their wake?),
  D37 (pads are not switched off before a sleep; Windows offers no call. If
  an Xbox pad wakes the box right after Rest mode, disabling its HID service
  around the sleep is the only lever, and it re-enumerates the device).
- Step 6: Electron's `powerMonitor` `resume` re-asserts the window after an S3
  wake; a Modern Standby Rest never sleeps the PC, so it does not fire there
  and the window is not re-asserted (the backend's `power:resumed` still
  plays the splash). Untested whether that matters on the TV.
- Step 5, conservative choices made: D28 (Bluetooth Connect reports instead
  of forcing), D29 (standby off = Windows' own timers), D32 (battery in SDL2
  bands; exact percent through SDL3 or the DualShock HID report later?).
- Step 5: Windows 11 24H2 asks for location access before a desktop app may
  scan Wi-Fi; the installer (step 7) may have to grant it, or the scan is
  empty. This PC (23H2) scans without it.
- Step 5: `configgen`'s `evdev_driven` / `guid_read_through_evdev` names and
  the "no capture under a HIDAPI GUID" rule date from Linux; on Windows a
  capture comes from the backend's own SDL. Revisit with the packs.

- Answered 20/10: games stay elevated (D8), no kill-on-close (D17), the
  overlay and its backend leave at step 4 (D22).
- D9: no `Documents\GameCore` default in code, the installer sets the data
  root. Confirm, or say if a run without the installer should default there.
- D22: the pack schema keeps its `overlay` field (and `roms.consoles`) for the
  Linux packs; drop them when the packs are rewritten? Conservative choice:
  kept.
- D26: if the shell runs elevated (same logon task as the backend), the
  Explorer it starts for Desktop mode is elevated too. Step 7 can start the
  interface unelevated, or start Explorer with the user's filtered token. Say
  which.

## Tested on the real Windows PC

| What | When | Result |
|---|---|---|
| Step 8: the real release pipeline, locally: `npm run build`, `npm run pack` (with `-c.win.signAndEditExecutable=false`: this account may not create the symbolic links electron-builder's signing tools unpack), `package-release.py` → `gamecore-ota.zip`, 112 MB, 1 787 files | 24/10/2026 | green |
| Step 8: end-to-end update in a temp folder (no GameCore service, task or `%ProgramData%\GameCore` on this PC, checked first): stage 1 against a local HTTP server (SHA-256 and digest), stage 2 started detached for real: swap, `python\ .venv\ caddy\` carried, real pip into the venv (31 s), Shelf 0.0.1 replaced and kept in `.prev`, a 503 from `/api/ready` accepted, `installed` in 46 s; then `-Rollback` run from inside the code root (copied itself out): v0.1.0 back, v0.2.0 in `.failed`, venv back. 12/12 checks | 24/10/2026 | green; it found the stderr bug (fixed) |
| Step 8, not tested: an update on an installed box (Program Files, the logon task, the Caddy service stopped and started, `Start-ScheduledTask` from stage 2, the job breakaway under Task Scheduler), the release workflow itself (it runs only on `main`; read, linted by its tests, simulated locally), a real GitHub release | — | not run (owner action) |
| Step 7: `install.ps1 -DryRun` on this PC, not elevated: every step planned (38 changes: code root, Python, data root, session.env, Caddy, firewall, OpenSSH, power values read through WMI for both plans, Fast Startup, automatic sign-in, the logon task), nothing written (`%ProgramData%\GameCore` still absent); `uninstall.ps1 -DryRun` without a record refuses | 24/10/2026 | green |
| Step 7: a child started with `CREATE_NO_WINDOW` has a console and no window (`test_gamecore_session.py`); two `SingleInstance` copies find each other and the event wakes the first | 24/10/2026 | green |
| Step 7, not tested: a real install, uninstall, the logon task at a real sign-in, Caddy as a service, OpenSSH, automatic sign-in, the shortcut from an unelevated desktop to an elevated session, 24H2 location access (this PC is 23H2) | — | not run (owner action) |
| Step 6: this PC's sleep model read (`modern_standby`: no S3, `AoAc`, hibernation file present); a power-event registration started, reported the console display state at once, stopped | 24/10/2026 | green |
| Step 6: Rest mode's Modern Standby sleep (`sleep.suspend()` = screen off) and `power.screen_on()` 4 s later: `display_on` received, `classify` read the sequence as a sleep then a wake. The screen was already off by Windows' timer when the check began, so the `display_off` came from the registration | 24/10/2026 | green |
| Step 6: `SetSuspendState` on this Modern Standby PC with a 45 s wake timer (on AC): `display_off`, `SUSPEND` at 0.5 s, `RESUME` at 81.7 s; the System log says S4 (hibernation), woken by `python.exe` (the timer), type 6; `last_wake()` read it | 24/10/2026 | green, and why D34 avoids it |
| Step 6, not tested: an S3 PC, a real pad waking the box (none connected), the wake settings written (needs elevation; this session was not elevated, and they are the owner's devices), the whole Rest mode through the backend and the interface on the TV, the power button | — | not run |
| Step 5, audio: outputs listed, volume read and written back unchanged, the default output set again, a bogus id refused | 24/10/2026 | green |
| Step 5, Wi-Fi: status (SSID, IP, gateway, DNS, MAC, radio), a scan (15 SSIDs, security, channel, band), `WlanDeleteProfile` on a missing profile answers "not found" | 24/10/2026 | green. Not run: join, leave, radio off (they cut this session's network), deleting a real profile |
| Step 5, Bluetooth: 10 paired devices read (address, connected), a 6 s scan (unnamed LE advertisers dropped) | 24/10/2026 | green. Not run: pair, unpair (the owner's devices) |
| Step 5, display: 1920x1080 120 Hz → 60 Hz → 120 Hz through `apply_mode`, the current mode written to the registry, an unsupported mode refused, monitor reported active | 24/10/2026 | green |
| Step 5, power: idle timers read (900 / 180 / 0 / 10 800 s), set to never and restored (checked with `powercfg`), screen off then on | 24/10/2026 | green; the screen state itself was not observable from here |
| Step 5, storage: the system NVMe (bus 17) excluded from the list | 24/10/2026 | green. Not run: a real USB disk, eject (none plugged in) |
| Step 5, pads: the SDL thread starts with background events, the probe subprocess loads the bundled `SDL2.dll` | 24/10/2026 | green. Not run: a real pad (none connected): guide double-press, chord, battery, wizard capture |
| Step 5, LAN IP: the Wi-Fi address chosen over the VMware and VirtualBox host-only adapters | 24/10/2026 | green |
| The checks above | 19/10/2026 | as in the baseline table |
| Junctions made, followed, removed and swept by `rmtree` on NTFS from an account without elevation (`test_platform_filesystem.py`) | 19/10/2026 | green |
| `BootId` read from the registry without admin; `psutil` start times of live and dead processes; the adoption identity end to end on real child processes (`test_platform_processes.py`) | 19/10/2026 | green |
| The suite cannot touch this PC's emulator configs: the account's profile, read from the process token, differs from every redirected folder; dropping USERPROFILE from `conftest.py` turns `test_home_isolation.py` red (mutation tried, reverted) | 19/10/2026 | green, mutation caught |
| `backend.main` imports with no Linux module loaded (`test_main_imports.py`); `/api/ready` answers under `TestClient` with the lifespan running and conftest's stubs in place | 19/10/2026 | green. The real lifespan was never run outside the suite |
| The whole backend and pack suite | 19/10/2026 | see "After step 2" |
| Step 3, `process_manager` driven from a script in this user's console session under a throwaway `GAMECORE_DATA`, the real lifespan never run: a Tk "game" that starts a child process, launched through `launch()` (argv with a quoted path holding spaces and backslashes), brought to the front by `window_focus.present()`, made borderless fullscreen; the script then exited (a backend restart) and a second process adopted the job by name from `session.json`, suspended it (all 5 processes `stopped`, the stand-in interface window raised over it), resumed it (all `running`, game window back in front), killed it (all 5 processes gone, child included, `session.json` removed). Screenshots in `%USERPROFILE%\Downloads\gamecore-port-step3\shots` | 19/10/2026 | green |
| Step 4: the real Electron shell (`electron.exe .`, kiosk) against the read-only dev server (`devserve.py`, `GAMECORE_MANAGED=1`, throwaway data root): boot to RUNNING in ~1.8 s, fullscreen 1920x1080, on all five themes. Screenshots in `%USERPROFILE%\Downloads\gamecore-port-step4\shots` | 20/10/2026 | green |
| Step 4: a real HUD toast (`electron/hud.js`) over a borderless fullscreen stand-in game window that had the foreground: drawn on top, the game kept the foreground before, during and after | 20/10/2026 | green |
| Step 4: legibility audit of the game options panel on the five themes | 20/10/2026 | 3 FAIL on Shelf (white at 35 %) fixed; 0 FAIL after |
| Step 4, not tested: Windows scaling above 100 % (this PC runs 96 dpi), the UI sounds, the pointer (screen captures do not show it; bench only), Desktop mode starting Explorer (Explorer already runs here), an exclusive-fullscreen game, `npm run pack` | — | not run |
| Raising another window over a frozen game window that has the foreground: hung with `AttachThreadInput`, refused without; raising before the freeze works (D19, `test_platform_window_control.py`) | 19/10/2026 | design changed |
