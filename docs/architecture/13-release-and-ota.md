# 13 — Release and OTA

What a push to `main` sets in motion, what reaches a box, and how to go back.

Nobody should have to read `.github/workflows/release.yml` to know this — but
that file and `update/linux.sh` are the authorities, and this page is a map of
them.

> **A merge to `main` is a delivery, not a git operation.** There is no staging
> environment and no manual approval between the merge and a production box
> fetching the result. Read [The gate](#the-gate-before-you-merge) before merging.

## What triggers a release

`release.yml` fires on a push to `main` **only when it touches something that
ships**:

```
backend/**  frontend/**  electron/**  update/**  install/**
config/**   assets/**    catalog/**   scripts/**
.github/workflows/release.yml
```

A documentation-only push does **not** cut a release — that filter exists so
README edits do not spam every box with a fake update. Note what *is* in the
list: `scripts/**` and `catalog/**` are there, so a change that feels like
housekeeping still ships.

Also on `v*.*.*` tags and on `workflow_dispatch`.

> **Never dispatch `release.yml` from a branch.** The `Publish GitHub Release`
> step has no `if:` guard and its `tag_name` falls back to `github.ref_name`, so
> it would publish a release named after your branch — which every box would then
> take for the latest.

## Frozen branches

`switch-eden` is `main` as it was with Eden running the Switch (v1.3.12, before
Ryujinx came back): Eden pack, its generator and seed, profiles. Protected
(pull requests only, no force push, no deletion) and never released from:
`release.yml` runs on `main` only, and dispatching it from this branch would
publish it to every box. To bring Eden back, open a PR from it to `main`.

## The two jobs

```mermaid
flowchart LR
    push["push to main<br/>(touching a shipped path)"] --> build

    subgraph build["job: build"]
        v["validate catalogue<br/>ruff · shellcheck · pytest"] --> tag["tag: bump patch"]
        tag --> fe["build frontend"]
        fe --> pkg["package 3 assets"]
        pkg --> rel["Publish GitHub Release"]
    end

    rel --> box["a box polls, sees a newer tag,<br/>fetches gamecore-ota.tar.gz"]
```

`build` runs the same checks as the local baseline before it tags anything, so a
red suite never becomes a release. The version is a **patch bump per merge** —
more than 150 releases in a few days is normal for this repo, and it is why an
AUR package is not viable (see `distribution/packaging/README.md`).

## The three assets

| Asset | What it is | Who takes it |
|---|---|---|
| `gamecore-ota.tar.gz` | ~2.7 MB. `backend/ frontend/ config/ electron/ update/ install/ catalog/ scripts/` + `VERSION` | every installed box, automatically |
| `gamecore-full.tar.gz` | ~14 MB, adds the built frontend for a fresh install | `gamecore-setup`, the AUR PKGBUILD |
| `gamecore-installer` | ~75 MB PyInstaller binary | a human installing onto an existing Arch |

Two things the packaging step gets right on purpose, both of which cost a real
bug to learn:

- **`frontend/` whole, sources included** — not just `dist/`. A box left with
  sources older than the build it runs will silently roll its UI back the moment
  anything rebuilds there (the updater's own fallback, a hand-run `npm run
  build`). The "Scan mapping" button shipped in v1.0.62 was missing from a box
  running v1.0.66 while its backend route answered the whole time.
- **`VERSION` carries the tag, not the tracked file.** The tracked one still says
  `v1.0.0` — it is only ever written on a box — so shipping it would make every
  fresh install report itself out of date.

`node_modules/` and `__pycache__` are stripped, which is why `gamecore-setup` is
needed after a full-archive install: the box has files, not a working venv.

## What the updater does — `update/linux.sh`

In order, and the order is the interesting part:

1. **Check free space first.** It asks for twice the payload. An rsync that runs
   out of space part-way leaves `GAMECORE_PATH` half old and half new, and the
   updater cannot install a specific tag — there is no way back from that except
   by hand.
2. **Snapshot to `${GAMECORE_PATH}.prev`** — see below.
3. **rsync the release in**, with the excludes below.
4. **Install/update themes** by comparing `version` in `theme.json`.
5. **Merge `systems.json`**, keeping the previous file as
   `systems.json.bak-merge`.
6. **Refresh the root helpers**: `sudo -n systemctl start gamecore-session-migrate.service`
   runs the installed `/usr/local/bin/gamecore-session-migrate`, which execs
   `install/steps/setup-gamecore-session.sh` from the tree just rsynced. That
   is how a release adds root pieces with no manual step: Rest mode's sudoers
   rule, wake helper, boot unit and udev rule (`install/steps/setup-rest-mode.sh`)
   land on the first update after they ship, even though the migrate helper
   that runs is the old copy.
7. **Start `gamecore-restart.service` with `--no-block`** and exit.

That last step matters: `update/linux.sh` runs *inside the backend's cgroup*, so
a direct `systemctl restart` would kill the script mid-update. The restart
happens in its own unit, about two seconds after the script exits.

### What the rsync excludes, and why

```
--exclude='.venv/'  --exclude='emu/'  --exclude='config/'
--exclude='assets/overlays/'  --exclude='assets/logos/'
```

- `emu/` — the ROM library. **Dropping this exclude deletes it on the first
  update.**
- `config/` — excluded *wholesale*, which is what preserves `config/catalog.d/`
  (the operator's own packs) and all the box state across every update.
- `assets/logos/` — uploaded by the operator (the Linux script also excludes
  the Linux bezel folder, assets/overlays, gone from the Windows edition).
- `.venv/` — rebuilt separately.

**`catalog/` is deliberately NOT excluded**, and that is the whole point of the
pack migration. Its predecessor `emu-configs/` was excluded once and it cost: a
corrected `GCPadNew.ini` reached GitHub, a test locked the fix in, and the box
kept its keyboard D-pad for good. Shipping `catalog/` does not touch a running
emulator's config — deploying that stays a deliberate act
(`install/steps/install-emu-configs.sh`).

When `GAMECORE_DATA` is set to something outside the install, the script says so
in the log and notes the excludes are now redundant but harmless.

### Where the updater finds the data root

Launched from the Settings button it inherits the backend's environment and
knows. Typed at a shell it does not — a shell has no `GAMECORE_DATA` — so since
v1.2.13 it reads the variable **from the backend's systemd unit**
(`_data_root_from_backend_unit`, same shape as `install/bin/gamecore-addon`,
duplicated on purpose: the updater must not depend on the CLI's presence or
version). Without this, a hand-typed update on a migrated box merged the
catalogue into the abandoned `config/` under the install — green log, dead
grid. The catalogue merge itself is handed **both roots** (`merge_file(...,
data_root=...)`): the catalogue and `lib/` are code and come from
`GAMECORE_PATH`; `systems.json`, `catalog.d/` and `catalog-removed.json` are
the player's and come from `GAMECORE_DATA`. Themes install under the data root
for the same reason.

The updater also reports, with the exact command, when a root-owned copy it
cannot replace has gone stale: `/usr/local/bin/gamecore-addon` (only
`install/arch.sh` writes it), `/etc/caddy/Caddyfile`, and a backend unit whose
merged definition (drop-ins included — it checks `systemctl cat`, not the file)
lacks the X-display `ExecStartPre`.

## Going back — `${GAMECORE_PATH}.prev`

Before overwriting anything, the updater snapshots the install with
`rsync --link-dest`, so it is **hardlinks: directory entries, no data**.

To restore, exactly what the script prints:

```bash
sudo rsync -a /opt/GameCore.prev/ /opt/GameCore/
sudo systemctl restart gamecore-backend gamecore-ui
```

**No `--delete`.** The snapshot excludes `.venv/`, `node_modules/`, `emu/`,
`config/` and `VERSION`, and those must not be removed from the live install.

Two properties that are easy to get wrong:

- **The snapshot's scope is not arbitrary.** A hardlinked snapshot shares inodes
  with the live tree, so anything writing *in place* changes both copies at once.
  `rsync` is safe (temp file, then rename — a new inode); `pip install` into
  `.venv`, `npm install` into `node_modules`, and the `echo > VERSION` at the end
  of the script are **not**. They are excluded for that reason, not for size —
  they are rebuilt from the release anyway.
- **A failure after the snapshot restores it automatically.** `fail()` runs
  `restore_prev()`: `rsync -a --delete` from the snapshot with the snapshot's
  own excludes (so only files the release added are removed), plus the data
  dirs the deploy never writes (`assets/overlays|logos|art/`, `addons/`,
  `volumes/`). Then it exits non-zero with VERSION untouched, so the update is
  offered again. Before this, a pip or session-refresh failure left new code on
  old dependencies, and the next reboot ran it. Not rolled back: `.venv` and
  any helper the session refresh already copied to `/usr/local/bin`.
  `backend/tests/test_ota_failure_restores.py` runs the script with stubs.

There is **one** snapshot: the next update overwrites it. It gets you back one
release, not to an arbitrary one.

Themes follow the same single-snapshot rule at
`config/themes/.prev/<id>/`, for the same reason — a bundled theme with a bug
must be fixable by a release, and the version the box had is kept rather than
discarded.

If the snapshot could not be taken, the script says so loudly and a later
failure leaves the install half-updated. That message is the only warning you get.
If the automatic restore itself fails, the script prints the manual command above.

## The gate before you merge

Because there is nothing between a merge and a living-room box:

- the six baseline commands green (see [`../TESTING.md`](../TESTING.md));
- the test count has not **dropped**;
- no **new** `skip` or `xfail`;
- `git diff main..HEAD -- 'catalog/*/tests/fixtures/*'` empty, unless you added
  the scenarios yourself and can name them.

One point failing means do not merge. An unmerged branch merges tomorrow; a
broken release has already left.

And if the temptation is to pass the gate by deleting a test, adding a `skip` or
regenerating a fixture — that is the moment to stop. **The gate is the
thermometer, not the objective.**

Record the SHA of `main` before merging. It is the way back, and without it there
is not one.
