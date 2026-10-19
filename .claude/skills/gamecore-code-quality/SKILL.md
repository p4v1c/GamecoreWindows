---
name: gamecore-code-quality
description: Code quality rules for GameCore — modular, clean, one source of truth, respect the layers (routers/services, kernel/shell/views, catalog packs). Use whenever writing, refactoring or reviewing code in GameCore for Windows (Python backend, React/TS frontend, Electron, PowerShell installer, catalog packs, themes).
---

# GameCore code quality

Goal: small modules with one job, one owner per fact, no layer leaks.
Pair with `gamecore-naming`, `gamecore-file-size`, `gamecore-comments`.

## Architecture boundaries (hard)

| Layer | Owns | Must not |
|---|---|---|
| `backend/routers/` | parse, validate, HTTP status | hold logic — move it to a service |
| `backend/services/` | decide and act; refuse with `ServiceError(status, detail)` (`services/errors.py`) | import FastAPI |
| `backend/services/paths.py` | both roots (`GAMECORE_ROOT`, `GAMECORE_DATA`) | — nobody else joins a writable dir onto a root; no `C:\Program Files\GameCore` literal |
| `backend/services/platform/` | every OS call (Win32, WinRT, WMI, `powercfg`, `netsh`) behind the names the services already call | be bypassed: no `os.name == 'nt'`, `ctypes.windll` or `win32*` import anywhere else |
| `frontend/src/App.tsx` (kernel) | input bus, WS, error boundaries | be touched by themes |
| `HomeScreen`/`LibraryScreen` `index.tsx` | every decision (focus, paging, launch) | render markup a theme could need to change |
| `Default*View.tsx` | markup only | hold behaviour |
| `catalog/<id>/` | everything about one system/app | need a line in `install/install.ps1` (if it does, the pack model lacks something — say so) |
| `config/` | the box's identity | be in git or touched by OTA |

## Rules

1. **One source of truth.** Before typing a list of emulator ids, a path, a
   field set, a regex, a constant: grep. If it exists, import it
   (`catalog/tiles.py`, `backend/utils.py`: `atomic_write`, `SYSTEM_ID_RE`,
   `fmt_size`). Intentional duplicates are listed in
   `docs/reports/refactoring-diagnostic.md` — do not "fix" those.
2. **One module, one job.** If you describe a file with "and", split it.
3. **Functions:** ≤ ~50 lines, ≤ 3 nesting levels, early returns.
   Longer = extract named helpers.
4. **No magic values.** Named constant at module top, `UPPER_SNAKE`.
5. **Pure core, thin edges.** Parsing/deciding in pure functions (testable);
   subprocess/filesystem/network calls at the edge.
6. **Writes are atomic** (`backend.utils.atomic_write*`) — power cuts happen.
7. **Recoverable failure degrades, never aborts** an install or a launch:
   log, warn, carry on, report in the summary.
8. **No speculative abstraction.** No interface with one implementation, no
   config for a value that never changes. Three real call sites before a
   helper.
9. **Frontend:** gamepad input through the bus (`onGp('gp:confirm', fn)`),
   not props. Global state in the single Zustand store. Inline styles live
   with the component; themable values go through the theme tokens.
   Typed `api/` wrappers only — no raw `fetch` in components. No `any`.
10. **Python:** type hints on public functions, `pathlib`, `logging` (no
    `print` outside scripts), stdlib first. Scripts run by the installer
    before the venv exists stay stdlib-only on the embedded Python. Paths
    compare case-insensitively (`os.path.normcase`) — NTFS does.
11. **PowerShell:** `Set-StrictMode -Version Latest` and
    `$ErrorActionPreference = 'Stop'` in new scripts, 4-space indent,
    PSScriptAnalyzer clean at `Warning`, `-LiteralPath` for every path that
    came from data (brackets are wildcards otherwise).
12. **Windows processes:** a game lives in a **Job Object**; kill the job,
    never one PID. Asyncio subprocesses need the Proactor loop. A file held
    open by another process cannot be replaced: stop the holder, or retry with
    backoff — never loop forever.
13. **Delete dead code** — with proof (no callers in scripts, CI, units,
    docs, tests), like `refactoring-diagnostic.md` does.
14. **Leave it cleaner:** a file you touch should not get longer and messier
    than necessary; extract before you add to a crowded file.

## Linters that already gate CI

`ruff check .` · PSScriptAnalyzer · `tsc` via `npm run build` ·
`scripts/check-catalog.py` · `scripts/gen-catalog.py --check` ·
`scripts/check-theme.mjs`. Green linters are the floor, not the goal.
