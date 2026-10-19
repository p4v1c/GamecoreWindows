---
name: gamecore-file-size
description: Prevent giant files in GameCore for Windows. Use before adding code to any file, when creating a module, when a file passes ~400 lines, and in every review. Gives the line budgets, the exemption list, and how to split Python services, React screens, Electron main, theme CSS and shell installers.
---

# GameCore file size

Big files are where bugs hide and where two features collide. Budget:

| Lines (code files) | Status | Action |
|---|---|---|
| ≤ 400 | target | — |
| 401–600 | warn | new code goes in a new module if it is a new concern |
| 601–800 | too big | extract **before** adding |
| > 800 | hard limit | no net growth; the PR that touches it must shrink it or explain why not |

Functions: ≤ ~50 lines. Components: one component per file (tiny private
helpers allowed).

## Run the check

```powershell
.venv\Scripts\python .claude/skills/gamecore-file-size/scripts/check-file-size.py          # whole repo
.venv\Scripts\python .claude/skills/gamecore-file-size/scripts/check-file-size.py --diff   # files changed vs origin/main
```

In `--diff` mode a FAIL on a file that was already over budget means: check
`git diff --stat` — it must not have grown.

## Exempt (with reason)

- Generated: `install/generated/*`, `install/installer-gui/catalog_data.py`
- Data / vendored: `backend/data/gamecontrollerdb.txt`, `backend/services/gamemedia/` vendored parts, seeds, fixtures (`**/tests/fixtures/**`, `catalog/*/seed/**`)
- Lockfiles, `LICENSE`, `CHANGELOG.md`
- `install/arch.sh` / `uninstall.sh`: the Linux installer, monolithic by
  decision upstream; removed by the port. The Windows installer is **not**
  exempt: one `install/steps/<step>.ps1` per step from the start.
- Tests: budget ×1.5; split by behaviour when over (`test_<module>_<behaviour>.py`).

## How to split

- **Python service** → a sibling module per concern next to it
  (`services/launch.py`, `services/gamepad_devices.py`,
  `configgen/sdl_probe.py`). Callers and tests import the owning module; do
  not keep re-exports only tests use (a second binding silently defeats a
  monkeypatch). A package (`services/<name>/`) only when the concern itself
  has several files.
- **Router** → move logic to a service raising `ServiceError(status, detail)`
  (`services/errors.py`; main.py maps it to HTTP), then split by resource
  (`routers/settings/` is the model).
- **React screen** → decisions stay in `index.tsx`, markup in
  `Default*View.tsx`, pure helpers in `lib/`, state logic in a `use*` hook.
- **`frontend/src/api/index.ts`** → one file per group (`api/games.ts`,
  `api/settings.ts`…) re-exported from `api/index.ts`.
- **`electron/main.js`** → one module per window/IPC domain; update the VM
  bench (`electron/test/`) that slices main.js by markers in the same change.
- **Theme CSS / settings.css** → `css/<concern>.css`, `@import`ed in cascade
  order from `theme.css`; tests read the whole sheet with `read_css`
  (`backend/tests/css_bundle.py`). Bump the theme version.
- **Platform layer** → one module per OS concern in
  `backend/services/platform/` (`processes.py`, `display.py`, `power.py`…),
  never one `windows.py` holding everything.
- **Pack daemon** (`catalog/*/files/*.py`) → sibling modules listed in
  `pack.json` `files`; stdlib only; a test runs the installed copy's `--help`.
- Snapshot behaviour first (`backend/tests/characterisation.py`) or compare
  the AST of every moved function (string contents masked) before and after.

## Never

- Split by line number ("part1/part2"). Split by concern.
- Create `utils2.py` / `helpers_misc.py` dumping grounds.
