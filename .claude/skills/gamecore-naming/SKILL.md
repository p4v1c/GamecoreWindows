---
name: gamecore-naming
description: Naming conventions for GameCore for Windows — files, modules, functions, variables, constants, React components/hooks, tests, endpoints, WebSocket events, catalog pack ids, CLI tools, branches. Use whenever creating or renaming anything in the repo, or reviewing names.
---

# GameCore naming

Follow what the tree already does. English, descriptive, no abbreviations
except established ones (`gp`, `ws`, `db`, `emu`, `ota`, `bt`, `id`).

## Files

| Kind | Pattern | Example |
|---|---|---|
| Python module | `snake_case.py` | `controller_capture.py` |
| Python test | `test_<module>.py` | `backend/tests/test_pergame.py` |
| Pack test | `catalog/<id>/tests/test_*.py` | `catalog/rpcs3/tests/test_smart_launch.py` |
| Repo script | `kebab-case.py` / `.ps1` in `scripts/` | `check-catalog.py` |
| Installed CLI | `gamecore-<verb-or-noun>` Python entry point, `install/bin/`, with a `gamecore-<name>.cmd` shim on `PATH` | `gamecore-addon` |
| Platform module | `backend/services/platform/<concern>.py`, same function names the Linux services call | `platform/processes.py` |
| Installer step | `install/steps/<verb>-<noun>.ps1` | `install/steps/register-logon-task.ps1` |
| React component | `PascalCase.tsx` | `SessionBar.tsx` |
| Screen folder | `PascalCase/` + `index.tsx` + `Default<Name>View.tsx` + `types.ts` | `LibraryScreen/` |
| Hook | `useCamelCase.ts` | `useGamepad.ts` |
| TS lib | `camelCase.ts` | `themeLoader.ts` |
| TS test | next to the file, `<name>.test.ts(x)` | `format.test.ts` |
| Doc | `docs/architecture/NN-kebab-case.md`, `docs/UPPER_SNAKE.md` for top-level guides, `docs/reports/<topic>-YYYY-MM-DD.md` | — |

## Code

| Kind | Python | TS/JS |
|---|---|---|
| function | `snake_case`, verb first: `release_profile`, `resolve_bezel` | `camelCase`: `formatGameName` |
| boolean | `is_/has_/can_` prefix | `is/has/can` prefix |
| constant | `UPPER_SNAKE` | `UPPER_SNAKE` |
| private | leading `_` | not exported |
| class / type | `PascalCase` | `PascalCase` |
| handler | — | `onX` prop, `handleX` inside |

## Interfaces

| Kind | Pattern | Example |
|---|---|---|
| HTTP route | `/api/<noun>/...`, lowercase, `{system_id}` params, nouns not verbs | `GET /api/overlays/{system_id}/slots` |
| WebSocket event | `<domain>:<past-tense-or-noun>` | `game:started`, `catalog:updated`, `gp:connected` |
| Gamepad bus event | `gp:<action>` | `gp:confirm`, `gp:guide` |
| Pack id | lowercase, no separator, = directory name | `megadrive`, `pcsx2` |
| Config key (JSON) | match the file's existing case; never mix in one file | — |
| Env var | `GAMECORE_<NAME>` | `GAMECORE_DATA` |
| Scheduled task | `\GameCore\<Role>` | `\GameCore\Backend` |
| Windows service | `GameCore<Role>` (display name `GameCore <role>`) | `GameCoreCaddy` |
| Firewall rule | `GameCore - <what>` | `GameCore - Web access` |

## Git

- Branch: `feat/<topic>`, `fix/<topic>`, `refactor/<topic>`, `docs/<topic>` — kebab-case, English.
- Commit: see `gamecore-commit-pr`.

## Smells

- Generic names: `data`, `info`, `tmp`, `handle`, `manager2`, `utils2`, `new_*`, `*_final`.
- Name lies: `get_*` that writes, `is_*` that returns a non-bool.
- A theme name (`orbit`, `shelf`) inside a core file name → the code belongs in `config/themes/<name>/`.
- Two names for one concept (e.g. `system_id` vs `console_id` vs `pack_id` — they are
  distinct concepts here; use the right one, see `docs/architecture/06-electron-and-overlays.md`).
