---
name: gamecore-tests
description: Testing rules for GameCore for Windows — pytest backend and pack tests, vitest frontend, Electron bench, network marker, characterisation before refactor. Use whenever adding logic, fixing a bug, refactoring, or before a PR.
---

# GameCore tests

## Where

| Code | Test |
|---|---|
| `backend/**` | `backend/tests/test_<module>.py` |
| `catalog/<id>/**` | `catalog/<id>/tests/test_*.py` (travels with the pack) |
| `frontend/src/**` | `<file>.test.ts(x)` next to the file |
| `config/themes/<name>/**` | `frontend/src/themes/<name>*.test.tsx` |
| `electron/**` | `electron` bench (`npm test`) |

## Rules

1. **A bug fix starts with a test that fails on the old code.**
2. Filesystem: take the root from `GAMECORE_TEST_ROOT` or `tmp_path` —
   never `Path.home()`, never `%APPDATA%`, never `C:\Program Files\GameCore`,
   never the data root.
9. **A Windows replacement gets its own tests** (platform layer, jobs,
   pads, power, display, installer steps, updater): the pure part with fakes
   on every runner, the real Win32 call behind `@pytest.mark.windows_only`
   only when it cannot be faked, and checked once on the real PC.
10. A Linux-only test goes **with the code it tested** — removed, not
    skipped. A `skip` added by the port is a finding in review.
3. Internet → `@pytest.mark.network`. CI runs `-m "not network"`.
4. **Refactor = characterisation first**: snapshot the old output
   (`backend/tests/characterisation.py`), replay against the new code.
5. A test that imports a script does not prove the script runs — also run it.
6. Test names say the behaviour, in English:
   `test_unplug_during_launch_releases_slot`, not `test_case_3`.
7. No sleeps for sync; wait on the condition. Flaky = bug, find the shared state.
8. Test files follow the size budget ×1.5 (`gamecore-file-size`).

## Run (what CI runs)

```powershell
.venv\Scripts\ruff check .
.venv\Scripts\python -m pytest backend/tests catalog -m "not network"
Push-Location frontend; npm run test:run; npm run build; Pop-Location
Push-Location electron; npm test; Pop-Location
```

CI runs the same on `windows-latest` (`.github/workflows/ci.yml`).

Full list: `CONTRIBUTING.md` "Before you open a pull request", `docs/TESTING.md`.
