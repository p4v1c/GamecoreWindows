# Testing

On Windows (PowerShell, from the repository root):

```powershell
$env:PYTHONUTF8 = '1'      # as CI and the installed backend run (dev log D14)
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt pytest
.venv\Scripts\python -m pytest backend/tests catalog -m "not network"   # what CI runs
.venv\Scripts\python -m pytest backend/tests catalog                    # with the network tests
```

Run `backend/tests` and `catalog` together: `backend/tests/conftest.py`
isolates the roots and the home, and skips the marked tests below; a catalog
pack's tests rely on it. The owner may be playing on the same PC: start a long
run at low priority (`Start-Process -Priority BelowNormal`).

The tree holds about 2 600 tests, 4 of which need the network. On Windows
371 are Linux-only for now (next section) and 27 skip on missing data or tools:
seeds with no controller block, maps already gone, and a release tag (the
Windows repository has none until step 8).

## Linux code the port has not replaced yet — `linux_until_port`

The Windows port removes a Linux mechanism in the step that lands its
replacement ([dev log](dev-log/windows-port.md), D7). Until then its tests
stay, marked with that step, and are skipped on Windows with the step named:

```python
@pytest.mark.linux_until_port(5)                 # one test
pytestmark = pytest.mark.linux_until_port(7)     # every test in the module
linux_until_port_module(7)                       # a module Windows cannot import (pwd, fcntl):
                                                 # call it before those imports
```

- The step must be 3-10 (`backend/tests/linux_until_port.py`); anything else
  fails the collection.
- Mark the test that needs Linux, not its file, when the rest of the file runs
  on Windows. Pure logic in a Linux service is made to run, not marked: fake
  the OS boundary. For uids that is the `posix_boundary` fixture
  (`backend/tests/posix_boundary.py`: `os.getuid`, `os.geteuid`), used by the
  installer tests until step 7.
- Every use is listed in
  [`dev-log/windows-port-linux-tests.md`](dev-log/windows-port-linux-tests.md);
  `test_linux_until_port.py` fails when the list and the source disagree.
- The step that replaces the code deletes its tests, and their rows.

A Windows replacement gets its own tests in the same PR
(`test_platform_*.py`): the pure part with fakes, the Win32 call for real on
the runner.

### Games, jobs and windows

- Tests of `process_manager` and its callers never start a game: the
  `spawner` fixture (`backend/tests/fake_jobs.py`) replaces `jobs.spawn`,
  passes `session.launch_blocker()` and hands out `FakeJob`s, which record
  every `suspend`, `resume` and `terminate` as `(pid, call)`; `job.exit()` is
  the game ending on its own. `quiet_manager()` is a manager that writes no
  session file, broadcasts nowhere and moves no window.
- `test_platform_jobs.py`, `test_session_robustness.py` and
  `test_session_adoption_identity.py` run real process trees in real Job
  Objects (Python children); `test_platform_window_control.py` opens real Tk
  windows and moves the foreground. They need the interactive desktop the PC
  and the `windows-latest` runner both have, and terminate every job they start.

## The home, on Windows

`Path.home()` and `os.path.expanduser` read **USERPROFILE**, never HOME, and
Windows emulators keep configs and saves under APPDATA and LOCALAPPDATA.
`conftest.py` points HOME, USERPROFILE, APPDATA and LOCALAPPDATA into the
throwaway root before any import; `test_home_isolation.py` compares them with
the account's real profile read from the process token, not from the
environment (`SHGetKnownFolderPath` expands `%USERPROFILE%` from it, so it would
agree with conftest whatever it did).

`%TEMP%` sits inside the real profile, so the throwaway root does too: the
guarantee is "inside the suite's fresh temp root", never "outside the real
home".

## Windows file semantics a test meets

- A file another handle holds cannot be deleted: close it first. The shared
  playtime connection (`db._DB`) and the log files (`logs._handlers()`) stay
  open after a `TestClient` lifespan; `test_covers.py` closes both before it
  wipes its root.
- Folder links are junctions (`platform/filesystem.link_dir`); a symlink needs
  admin (`WinError 1314`). `rmtree` and `unlink` remove a junction without
  entering it.
- A name may not hold `:`: sysfs fixtures such as `3-1:1.0` cannot exist on
  NTFS (the Linux tests that built them left with their code).
- No test may change this PC: the session guard in `conftest.py` replaces the
  display mode switch, the screen power, the power plan and idle timers, the
  disk eject, the sleep (`platform/sleep.py` `suspend()`, and `watch()`, which
  registers nothing) and the device wake settings; `fake_screen` and `disks`
  fake them per test, SDL's pad snapshot is faked by patching
  `platform/gamepads.py` `pads()`, and `test_resume.py` fires Windows' power
  events from a thread through a fake `watch()`.
- `glob("*.png")` matches `Back.PNG`: names compare case-insensitively.
- Without UTF-8 mode, `read_text()` with no encoding decodes as the ANSI
  code page (cp1252 on the runner and on a French Windows): every theme and
  the README fail. A PC whose system locale is UTF-8 hides it; CI does not.
- `mimetypes` reads the registry: `.webp` is missing on a bare Windows.
  `platform/media_types.py` pins what GameCore serves.
- The event loop's self-pipe is a socket pair built with `connect()`; patch
  `socket.connect` only once the app runs (`test_boot_ready.py`).

## The PowerShell installer

`backend/tests/test_install_*.py` drive `install/` through
`backend/tests/powershell_harness.py`: each case is a script that dot-sources
the installer, defines fake functions for every cmdlet and program the step
calls (a function shadows a cmdlet of the same name), and prints what the fakes
noted as JSON. Nothing reaches the machine: files go to `tmp_path`, and
`GAMECORE_STATE_DIR` moves the install record there. A fake that defines a
variable the step also uses shadows it: the harness's own table is `$GcTest`.
Each case starts a `powershell.exe` (about half a second). The details are in
[11](architecture/11-install-script-seams.md#tests).

## The suite used to be decorative

Every test file kept its whole body inside a `main()` behind
`if __name__ == "__main__"`. `pytest backend/tests/` answered **"no tests ran"** —
green, having executed nothing. A CI job built on it reported success on any
change whatsoever.

The assertions were fine and were kept as they were; only the structure changed:
`check(name, cond)` became `assert cond, name`, `setup_root()` and `reset()`
became fixtures, and the `__main__` blocks stayed, because running a file
directly is documented in its own docstring:

```bash
python backend/tests/test_covers.py         # still works, per-file
```

## `conftest.py` — read this before adding a test

`backend/config` reads `GAMECORE_PATH` **at import time**, and every path in the
application is derived from it. Whichever test module pytest imported first would
therefore decide where the whole suite wrote — and the cover tests would drop
their `.miss` markers into the real checkout, or worse into whatever
`GAMECORE_PATH` the shell already exported.

`conftest.py` is imported before any test module, which makes it the only place
the override is guaranteed to land in time. It points `GAMECORE_PATH` at a fresh
temp directory and publishes it as `GAMECORE_TEST_ROOT`.

It **sets** rather than defaults the variable: inheriting a `GAMECORE_PATH` from
the environment would aim the suite at a real installation.

If you add a module that touches the filesystem, take the root from
`GAMECORE_TEST_ROOT` (or a `tmp_path` fixture) — never from `Path.home()` or a
hardcoded path.

## The `network` marker

```ini
# pytest.ini
markers =
    network: hits an external service (GameTDB, xlenore)
```

Four tests carry it: the GameTDB disc-ID lookup, the two xlenore serial lookups,
and the negative-cache test.

That last one is marked for a reason worth knowing: after the `.miss` fix, a
marker is only written when a lookup actually came back **empty**. A failed
request no longer produces one, so "an unknown game gets negatively cached" is
only true with a working network.

CI runs `-m "not network"` — a release must not be blocked because GameTDB is
down. Verify the offline subset really is offline by blackholing HTTP:

```bash
HTTP_PROXY=http://127.0.0.1:1 HTTPS_PROXY=http://127.0.0.1:1 \
  pytest backend/tests -q -m "not network"
```

## What each file covers

| File | Subject |
|---|---|
| `test_auth.py` | password round-trip, per-IP backoff, and that the **global breaker does not lock out an innocent IP** |
| `test_battery.py` | the low-battery threshold logic: one toast per threshold, rearming, hysteresis |
| `test_bluetooth.py` | pairing vs reconnecting, and the cursor following the list past the screen |
| `test_catalog.py` `test_catalog_merge.py` `test_catalog_consumers.py` | the pack loader, shipped ∪ local merging, and that every consumer reads the catalogue instead of its own copy |
| `test_configgen_snapshots.py` `test_controller_characterisation.py` `test_generator_contract.py` | the per-emulator binding writers: snapshots produced from the OLD implementation and replayed on the new one, plus the surface every `generator.py` must expose |
| `test_installer_providers.py` | the four `install` providers and the download helper — fixed URL before the API, `.part` file, magic bytes, sha256 |
| `test_installer_applier.py` | `packages`/`sources`/`files`/`services`/`postInstall`: token expansion, `when`, `ifAbsent`, a pack refused when it reads outside its own directory, a failing step warning instead of aborting — **and that every pack carries every file it declares** |
| `test_controller_registry.py` | player-slot assignment, MAC normalisation, battery→slot join |
| `test_covers.py` | the cover pipeline end to end against synthetic dumps; path containment; `.miss` written only for a genuine miss |
| `test_cross_origin.py` | the core's origin guard and `/ws` — including that a LAN client through Caddy is still allowed |
| `test_gamepad_monitor.py` | detection of pads with no Home button; one pad owning several device nodes |
| `test_session_robustness.py` | standby wake, display-probe memoisation, adopting an orphaned game, 503 on a missing emulator |
| `test_systems_extensions.py` | `systems.json.dist` ↔ `config/systems.json` ↔ the README table stay in agreement |
| `test_themes.py` | manifest validation, the completeness rule, theme-id safety |
| `test_playtime_repair.py` `test_theme_versions.py` `test_gamemedia.py` | playtime repair, theme version bumps reaching a box, media/hash matching |
| `test_update.py` | one update at a time (409), the timeout killing the whole process group, `VERSION` written last |

`catalog/<id>/tests/` is collected too — a pack's tests arrive with the pack
rather than in a central directory nobody updates. CI runs
`pytest backend/tests catalog -m "not network"`.

`test_covers.py` builds real binary fixtures rather than mocking: a PARAM.SFO, an
ISO9660 image (2048-byte and raw 2352-byte layouts), a PS1 `.bin` with a
`SYSTEM.CNF`, a GameCube ID6 header. That is why it catches parser regressions
that a mocked test would not.

## The test that pins the *packs*

`test_installer_applier.py::test_every_pack_carries_everything_it_declares`
walks every pack and fails if a `files`, `services` or `postInstall` entry names
a file the directory does not contain.

It exists because `catalog/twitch/pack.json` declared six payload files and
shipped one, and because a refactor deleted `install/firefox-profiles/` while
`install/arch.sh` still read it. Neither showed up in CI, in review, or on a box
that already had GameCore. Both surfaced the same way: a **fresh** install dying
part-way through, months later.

## Tests that pin a *document*

`test_systems_extensions.py` parses the "Supported formats per system" table out
of `README.md` and compares it to `install/generated/systems.json.dist`. The table gained
an `emu/<id>/` folder column specifically so the pairing is unambiguous enough to
check mechanically.

It exists because that table had drifted: `.cue` was documented and not declared,
so a standard multi-track PS1 dump showed twelve `.bin` entries and hid the one
file that is meant to be launched. A doc-parsing test has one classic failure
mode — matching nothing and passing — so `test_readme_lists_every_system` asserts
the row count too.

## The other repos

```bash
# gamecore-addons
cd addons/save-manager
PYTHONPATH=../../shared/py python tests/test_api.py      # needs fastapi + httpx
PYTHONPATH=../../shared/py python tests/test_memcard.py  # stdlib only

# gamepad-tv-bridge
python -m pytest tests/
```

`shared/py` is on `PYTHONPATH` because `install.sh` copies `sfo.py` into the
addon directory at install time; from a checkout it has not been copied yet.

## Running a backend by hand

On the Windows PC, prefer the read-only dev server of the `gamecore-legibility`
skill (`.claude/skills/gamecore-legibility/scripts/devserve.py`): `uvicorn`
runs the real lifespan, which acts on the screen, the power settings and the
pads of the machine it runs on.

Never against the real install directory:

```bash
GAMECORE_PATH=~/scratch/gc GAMECORE_BACKEND_PORT=8899 \
  python -m uvicorn backend.main:app --host 127.0.0.1 --port 8899
```

`GAMECORE_BACKEND_PORT` matters as well as `--port`: the cross-origin guard
accepts a loopback `Origin` on the backend's **configured** port, so with only
`--port` set an `Origin: http://localhost:8899` would be refused.

Ports 8765, 8443, 8770-8799 and 8097 belong to a running installation. Use
something above 8800 for anything disposable.
