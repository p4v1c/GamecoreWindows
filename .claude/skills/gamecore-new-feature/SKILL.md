---
name: gamecore-new-feature
description: Workflow for adding any new feature to GameCore for Windows — decide where the code goes, build it in layers, test it, document it, ship it. Use at the start of every feature, new endpoint, new setting page, new emulator/app, new theme surface, or new install step. Chains the other gamecore-* skills.
---

# GameCore new feature

## 1. Locate — where does it live?

| Feature is about… | Code goes in | Doc (see `gamecore-docs`) |
|---|---|---|
| an emulator / app / system | `catalog/<id>/` only → `gamecore-catalog-pack` | 10 |
| a backend capability | `services/<name>.py` + thin `routers/<name>.py` | 03 + 04 |
| an OS setting (wifi, audio, BT, display) | `routers/settings/<name>.py` + service; the Win32/WinRT call in `services/platform/` | 03 + 04 |
| a push to the UI | `ws.broadcast("<domain>:<event>")` | 05 event table |
| UI behaviour | screen `index.tsx` / hook / `lib/` | 05 |
| UI look | `Default*View.tsx`, or the theme in `config/themes/<name>/` | 05 / themes |
| a theme-visible API | `frontend/src/lib/themeSdk.ts` (SDK version bump) | themes README |
| Electron window / IPC | `electron/` | 06 |
| a box config file | `config/<name>.json` via `paths.py` | 07 |
| install / OTA | pack model first; an `install/steps/*.ps1` step only if the pack model cannot express it | 10 / 11 / 13 |
| an addon hook | contract `api` in `docs/architecture/12-addon-contract.md` | 12 |

Grep first: the helper, list, or constant you need probably exists
(`gamecore-code-quality` rule 1).

## 2. Build — smallest vertical slice

1. Pure logic in a service (+ test).
2. Router: validate, call service, return.
3. `frontend/src/api/` typed wrapper.
4. UI: decisions in the screen/hook, markup in the view.
5. Gamepad-first: every new screen reachable and escapable with the pad
   (`gp:back` works, focus never lost). TV at 3 m: readable, no hover-only UI.
6. Failure path: what does the player see if it fails? Degrade, never hang.

Budgets while building: `gamecore-file-size`, `gamecore-naming`,
`gamecore-comments`.

## 3. Test — `gamecore-tests`

## 4. Document — `gamecore-docs` (same commit)

## 5. Ship — `gamecore-commit-pr`

## Definition of done

- [ ] code in the right layer, no file over budget grew
- [ ] tests for the new logic, suite green
- [ ] docs + CHANGELOG updated, `check-docs.py` green
- [ ] English comments, short
- [ ] works with a pad only, on a fresh install, and after an OTA
      (config/ untouched, `frontend/dist` rebuilt — see gotchas "OTA rebuild trap")
- [ ] said in `docs/dev-log/windows-port.md`: what was tested on the real
      Windows PC and what was not
