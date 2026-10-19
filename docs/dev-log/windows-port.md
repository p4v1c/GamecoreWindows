# Windows port — dev log

GameCore for Windows: the Linux console front-end (`p4v1c/GamecoreRenew`)
ported to Windows 10/11 x64. **A port, not a rewrite**: every file that is not
OS-specific stays as it is; OS calls move behind `backend/services/platform/`.

Resume point for anyone picking the work up: read **Status**, then **Next**.

## Status

| Step (brief §3) | State | PR |
|---|---|---|
| 1. Repo, skills, docs read, dev log, CI skeleton | in review | `chore/windows-port-setup` |
| 2. Platform layer + paths, backend passes the ported suite | next | — |
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
| `git ls-files` | no symlink, no case collision, no reserved character in a tracked name |

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
- `ci.yml` on `windows-latest`; the Linux `release.yml` and
  `verify-catalog.yml` removed.
- Docs read: `README.md` (outline), `CONTRIBUTING.md`, `docs/TESTING.md`,
  `docs/architecture/01`–`13`. `docs/themes/README.md` and the README's user
  manual are read in full at step 4, before the frontend is touched.

## Next

Step 2: `backend/services/platform/` skeleton and `paths.py` for Windows roots;
`conftest.py` without symlinks (junction or copy); every backend test either
passes on Windows or goes with the Linux code it tested; pytest joins `ci.yml`.

## Open questions for the owner

- None blocking. D8 (session model) is confirmed or changed at step 3.

## Tested on the real Windows PC

| What | When | Result |
|---|---|---|
| The checks above | 19/10/2026 | as in the baseline table |
