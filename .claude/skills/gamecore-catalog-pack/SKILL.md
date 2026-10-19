---
name: gamecore-catalog-pack
description: Add or change an emulator, system or application pack in GameCore's catalog/<id>/ (pack.json, generator.py, seed, files, steps, tests). Use whenever a new console, emulator, app (Stremio, YouTube, Twitch…) or pack field is involved in GameCore for Windows.
---

# GameCore catalog pack

The Windows edition ships **no packs yet** (out of scope for the port):
the machinery is kept so packs can be added later. The first pack of each
provider sets the pattern the others copy.

A system or app is **one directory**. Nothing else in the tree should change
except what `gen-catalog.py` regenerates.

```
catalog/<id>/
├── pack.json     declaration — catalog/_schema/pack.schema.json
├── logo.png      tile
├── art/          pictures for themes: console.webp (hardware photo) + SOURCE.md
├── seed/         curated emulator config (optional)
├── generator.py  controller bindings (optional)
├── files/        what `files` / `services` refer to
├── steps/        what `postInstall` refers to
└── tests/        test_*.py — collected by CI
```

## Steps

1. Copy the closest existing pack (same provider: winget / github-asset /
   github-archive (zip, portable) / download). Read `docs/architecture/10-catalog-and-install.md` §2 for each block.
2. `id` = directory name, lowercase, no separator.
3. `perGame` is **required** on emulator packs (§2 explains why).
4. Controller: prefer an existing generator strategy; `generator.py`
   re-exports what the dispatcher calls (F401 is ignored there on purpose).
5. Daemons in `files/` run on the embedded Python → **stdlib only**, English
   comments, split if > 600 lines (`gamecore-file-size`).
6. Regenerate and validate:
   ```powershell
   .venv\Scripts\python scripts/gen-catalog.py
   .venv\Scripts\python scripts/check-catalog.py
   .venv\Scripts\python scripts/gen-catalog.py --check
   .venv\Scripts\python -m pytest catalog/<id> -m "not network"
   ```
7. Commit the pack **and** the regenerated files together.

## Red flags

- You need a line in `install/install.ps1` → the pack model is missing a feature.
  Say so in the PR instead.
- You hardcode `C:\Program Files\GameCore`, `%APPDATA%` or a drive letter →
  use the template vars / `paths.py`.
- The system appears nowhere in the installer → you forgot `gen-catalog.py`.
- The tile shows before the emulator is installed → see fix 700d86c.
- The emulator runs in exclusive fullscreen → the HUD cannot draw over it;
  set borderless fullscreen in the seed.
