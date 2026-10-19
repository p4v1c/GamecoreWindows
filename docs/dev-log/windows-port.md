# Windows port — dev log

GameCore for Windows: the Linux console front-end (`p4v1c/GamecoreRenew`)
ported to Windows 10/11 x64. **A port, not a rewrite**: every file that is not
OS-specific stays as it is; OS calls move behind `backend/services/platform/`.

Resume point for anyone picking the work up: read **Status**, then **Next**.

## Status

| Step (brief §3) | State | PR |
|---|---|---|
| 1. Repo, skills, docs read, dev log, CI skeleton | in review | `chore/windows-port-setup` |
| 2. Platform layer + paths, backend passes the ported suite | in review | `feat/platform-layer` |
| 3. Process / session management (launch, suspend, resume, kill) | — | — |
| 4. Electron shell + frontend fullscreen, 5 themes, boot animation and sounds | — | — |
| 5. Pads (SDL), standby, display, audio, Wi-Fi, Bluetooth, storage, logs | — | — |
| 6. Rest mode (sleep, wake by pad, splash after wake) | — | — |
| 7. Services, Caddy, SSH, firewall, installer + uninstaller, autologon | — | — |
| 8. OTA update + rollback, release workflow | — | — |
| 9. Addons repo (`p4v1c/gamecore-addons-windows`) | — | — |
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
| D8 | Session model — **proposed**, confirmed at step 3 with a game window on the TV: the backend runs in the console user's session from a logon task (highest privileges) with a watchdog; Caddy and OpenSSH stay services | a service lives in session 0 and cannot show a window on the user's desktop; a per-session launcher agent would add a second process and an IPC channel for the one call (`CreateProcess`) the logon task makes directly |
| D9 | `GAMECORE_DATA` unset still means the code root; the installer always sets both roots (step 7). `paths.py` holds no Windows default folder | on an installed PC the code root is not writable by the console user, so "unset" only happens in a checkout (dev, tests, dev server), where the checkout is the right answer. A `Documents\GameCore` default in code would point a developer's checkout at the real data of a GameCore installed on the same PC |
| D10 | File names match case-insensitively, as NTFS does: `Back.PNG` is the `back` picture of a pack (`test_pack_art.py`) | the brief's rule; on Linux the same file was ignored only because `glob("*.png")` is case-sensitive there |
| D11 | Tests of Linux code a later step replaces carry `linux_until_port(step)` and are skipped on Windows with the step named; the full list is [`windows-port-linux-tests.md`](windows-port-linux-tests.md), checked against the source by `test_linux_until_port.py`. A test whose code is already gone is deleted, not marked | D7 says the test goes with its code; the marker keeps the count visible until then, and a mark missing from the list fails the suite |
| D12 | Folder links are NTFS **junctions** (`platform/filesystem.py`), never symlinks | a symlink needs admin or Developer Mode (`WinError 1314`); a junction needs neither and every program follows it. Limit: the target is a local absolute directory that exists |
| D13 | Boot identity = the registry `BootId` counter; process start time = `psutil` creation time (`platform/processes.py`) | `BootId` is exact and readable without admin; `psutil.boot_time()` is derived from the uptime and drifts by a second between calls, which would refuse to adopt a game the backend did start |

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

Measured on the Windows 11 PC, Python 3.13,
`pytest backend/tests catalog -m "not network"`:

| | Test items |
|---|---|
| The tree as ported from Linux (all of them run on the Linux box) | 2 901, 4 of them network |
| Windows before this step | 0: the suite could not be collected |
| Windows after this step: passed | 2 402 |
| skipped: Linux code a later step replaces (`linux_until_port`, D11) | 464 in 66 files ([list](windows-port-linux-tests.md)) |
| skipped on missing data or tools, as on Linux (ImageMagick, a frontend build, a release tag, seeds with no controller block) | 55 |
| deselected (network) | 4 |
| deleted: their code left in step 1 (`release.yml`) or here (`/proc` parsing) | 4 |
| added: platform layer, home isolation on Windows, Windows roots, the marker | 28 |

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
  nothing). The installer modules load on Windows (`pwd`/`grp` guarded), so 51
  of their 73 tests run. `paths.py` documents the Windows roots;
  `requirements.txt` makes evdev and python-xlib Linux-only and adds `psutil`;
  `ci.yml` gains the `backend` job.

## Next

Step 3: process and session management. `platform/processes.py` gains the
Job Object launch (kill-on-close, terminate the job), suspend and resume of
every process in the job (`NtSuspendProcess`/`NtResumeProcess`) and the
console-user session check (WTS); asyncio on the Proactor loop.
`process_manager.py`, `session.py`, `window_focus.py` and
`fullscreen_enforcer.py` lose their Linux paths, and the 91 step-3 items of
[`windows-port-linux-tests.md`](windows-port-linux-tests.md) are deleted or
ported with them. Two traps found here: launch args are split with POSIX
`shlex`, which eats the backslashes of a bare Windows path
(`test_launch_args_roots.py`); and `process_manager.py` is already over the
size budget (933 lines), so it must shrink.

## Open questions for the owner

- D8 (session model) is confirmed or changed at step 3.
- When do the Linux packs leave the tree? They are out of scope (brief §1);
  their 282 Linux-only test items are marked step 7, where the Windows install
  providers replace Flatpak. Removing them earlier, in a PR of their own, would
  shorten the list. Confirm, or pick the moment.
- The overlay window (out of scope) is marked step 4, where the brief removes
  it from Electron; its backend (`overlay_monitor.py`, `bezel_capture.py`,
  `routers/overlays.py`, `bezels.py`) would go in the same PR. Confirm.
- D9: no `Documents\GameCore` default in code, the installer sets the data
  root. Confirm, or say if a run without the installer should default there.

## Tested on the real Windows PC

| What | When | Result |
|---|---|---|
| The checks above | 19/10/2026 | as in the baseline table |
| Junctions made, followed, removed and swept by `rmtree` on NTFS from an account without elevation (`test_platform_filesystem.py`) | 19/10/2026 | green |
| `BootId` read from the registry without admin; `psutil` start times of live and dead processes; the adoption identity end to end on real child processes (`test_platform_processes.py`) | 19/10/2026 | green |
| The suite cannot touch this PC's emulator configs: the account's profile, read from the process token, differs from every redirected folder; dropping USERPROFILE from `conftest.py` turns `test_home_isolation.py` red (mutation tried, reverted) | 19/10/2026 | green, mutation caught |
| `backend.main` imports with no Linux module loaded (`test_main_imports.py`); `/api/ready` answers under `TestClient` with the lifespan running and conftest's stubs in place | 19/10/2026 | green. The real lifespan was never run outside the suite |
| The whole backend and pack suite | 19/10/2026 | see "After step 2" |
