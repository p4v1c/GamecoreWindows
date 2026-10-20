# Contributing to GameCore

## The one rule

**A box is not a test environment.** Most of what breaks here breaks only on a
real machine, on a *fresh* install, weeks after the change that caused it. The
whole of this file is downstream of that.

## Getting a working checkout

```bash
git clone https://github.com/p4v1c/GamecoreRenew.git
cd GamecoreRenew

python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt pytest
cd frontend && npm ci && cd ..
cd electron && npm ci && cd ..
```

Running it (three terminals — see the README's *Development setup*):

```bash
.venv/bin/uvicorn backend.main:app --port 8765 --reload
cd frontend && npm run dev            # Vite :5173
cd electron && ELECTRON_DEV=1 npx electron .
```

## Before you open a pull request

Everything below runs in CI and fails the build. Running it locally costs a
minute.

```bash
ruff check .                                    # E4, E9, F — see pyproject.toml
shellcheck -S warning $(git ls-files '*.sh') install/bin/*
python3 scripts/check-catalog.py                # every pack against the schema
python3 scripts/gen-catalog.py --check          # generated files are in sync
python3 -m pytest backend/tests catalog -m "not network"
cd frontend && npm run test:run && npm run build # vitest, tsc, then the bundle
cd electron && npm test                         # the shell, in a VM with fakes
```

The Electron bench is also run by `pytest` (`test_electron_bench.py`), which
skips it when there is no `node`. It is the only place the shell's boot, its
HUD toasts and its power commands are checked at all: nothing in Python can
see them.

`gen-catalog.py --check` is the one people forget. Three committed files are
**generated** from `catalog/*/pack.json` — `install/generated/*.dist` and
`install/installer-gui/catalog_data.py`. Edit a pack without regenerating and
your system exists, validates, and appears in no tick box in the installer.

## Adding an emulator or an application

Drop a directory. Nothing else in the tree should need touching:

```
catalog/<id>/
├── pack.json     the declaration — validated by catalog/_schema/pack.schema.json
├── logo.png      the tile
├── seed/         curated config, optional
├── generator.py  controller bindings, optional
├── files/        what `files` and `services` refer to
├── steps/        what `postInstall` refers to
└── tests/        collected by CI with the rest
```

Then `python3 scripts/gen-catalog.py` and commit both the pack and what it
regenerated. Full reference:
[`docs/architecture/10-catalog-and-install.md`](docs/architecture/10-catalog-and-install.md).

If your change needs a line in `install/arch.sh`, that is a signal the pack
model is missing something — say so in the PR rather than adding the line.

## Writing the code

**Comment the why, never the what — in English, and short.** A comment names
the constraint or the failure it prevents, in one to three lines; the story of
how it was found belongs in the commit body or `docs/reports/`. A comment that
says *what* the line does is noise. Rules and examples:
`.claude/skills/gamecore-comments/SKILL.md`.

**One source of truth.** Every serious bug in this project has the same shape:
a fact recorded in two places, one of them updated. If you find yourself typing
a list of emulator ids, a file path, or a set of fields that already exists
somewhere — stop, and read the existing one instead. `catalog/tiles.py` exists
because that lesson cost three separate outages.

**A failure that costs a tile must not cost the install.** `install/arch.sh`
warns and carries on for a dozen recoverable failures, and reports them in its
closing summary. A missing emulator is a degraded box; an aborted installer at
66 % is a machine that is neither installed nor clean.

## Tests

- `backend/tests/` for the backend, `catalog/<id>/tests/` for a pack — a pack's
  tests travel with the pack.
- `-m "not network"` is the CI marker. A test that reaches the internet must
  carry `@pytest.mark.network`, so a Flathub outage cannot fail the build.
- Characterisation before refactoring: snapshot the *old* implementation's
  output, replay it against the new one. `backend/tests/characterisation.py` is
  the harness, and the controller refactor is the worked example.

## Commits

Conventional prefixes (`fix:`, `feat:`, `refactor:`, `docs:`, `chore:`, `ci:`)
with the touched area in parentheses, **in English**. The body is where the value is: say what
broke, how, and what the reader would otherwise re-derive. `git log` is the only
document nobody forgets to update.

**Every push to `main` publishes a release** that installed boxes pick up over
the air. `.github/workflows/release.yml` runs the lint, the catalogue checks and
the test suite before it tags — a red step means no tag and no release, which is
the intended behaviour. Work on a branch.

## Project skills

`.claude/skills/` holds the working rules as Claude Code skills: comments,
docs, code quality, naming, file size (`check-file-size.sh`), new features,
catalogue packs, tests, commits, review, and human touch (no AI-looking UI
or copy; `slop-audit.py`). They apply to humans too.

## Licence

GPL-3.0-or-later. By contributing you agree your work ships under it.
