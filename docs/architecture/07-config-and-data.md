# 7 — Config & data

Everything the box stores, and who writes it.

## Path resolution — `backend/services/paths.py`

Nothing hardcodes `/opt/GameCore`, and nothing outside this one module decides
where anything lives. There are **two roots**:

| Root | Env | Holds | Writable |
|---|---|---|---|
| `GAMECORE_ROOT` | `$GAMECORE_PATH` or the repo root | backend, venv, frontend build, catalogue, installers | no (that is the goal) |
| `GAMECORE_DATA` | `$GAMECORE_DATA`, **defaulting to `GAMECORE_ROOT`** | ROMs, generated config, covers, scraped media, playtime | yes |

**The default is the whole safety property, not a placeholder.** The release
that introduced the split reaches production boxes over OTA with nobody in
front of them, and `update/linux.sh` rsyncs into `GAMECORE_PATH` while
excluding `emu/` and `config/` — the two directories a split moves. If the new
code looked for data anywhere other than where the rsync left it, every box
would boot with an empty library and no settings, and the rollback would hand
the *old* code a tree the *new* code had moved. So the pointer moves and the
bytes do not: after the update every path resolves to the byte-identical
location it did before. Moving the bytes is a separate, human-typed operation
(`scripts/migrate-userdata.py`), deliberately unreachable from the updater.

`paths._LAYOUT` is the table of writable locations, and it deliberately keeps
today's names (`emu/`, `config/`, `assets/logos/`) rather than the eventual
`roms/ logos/ media/`. Renaming there either moves bytes on every installed
box or ships a second layout no test and no real box has exercised. The rename
is one edit to that table, and it belongs with the move.

`backend/tests/test_paths.py` enforces both halves mechanically — no module
outside `paths.py` may join a writable directory onto the code root, and none
may read the root variables from the environment. It works on the AST, not on
a grep, because half these modules discuss `config/` and `emu/` at length in
their docstrings.

### On Windows

| Root | Installed PC | A checkout (dev, tests, dev server) |
|---|---|---|
| `GAMECORE_ROOT` | `C:\Program Files\GameCore`, read-only for the console user | the repository |
| `GAMECORE_DATA` | chosen at install: `Documents\GameCore` by default, or a folder on another NTFS volume (`D:\GameCoreData`) | unset: the repository too, as on Linux |

- The installer sets both (port step 7). The code root is not writable by the
  console user, so "unset" never happens on an installed PC; the default only
  keeps a checkout self-contained ([dev log](../dev-log/windows-port.md), D9).
- `pathlib` everywhere. Windows paths compare case-insensitively, as NTFS
  names do: `C:\GameCore` and `c:/gamecore/` are one root, not a split.
- A drive path in `systems.json` (`E:\Roms\psx`) is kept as written; a
  relative one (`emu/duckstation/`, either slash) resolves under the data root.
- Folder links (a profile's saves, the suite's `catalog/`) are NTFS
  junctions, not symlinks: `services/platform/filesystem.py`.

### The two roots, drawn — including what is still ambiguous

```mermaid
flowchart TB
    subgraph ROOT["GAMECORE_ROOT — the installation (code)"]
        direction LR
        r1["backend/ · frontend/dist<br/>electron/ · .venv/"]
        r2["catalog/<br/>packs, seeds, generators"]
        r3["assets/<br/>shipped fonts, sounds"]
        r4["install/ · scripts/ · update/"]
    end

    subgraph DATA["GAMECORE_DATA — the player's data (writable)"]
        direction LR
        d1["config/<br/>systems.json · playtime.db<br/>auth · catalog.d/ · themes/"]
        d2["emu/<br/>ROMs · covers · scraped media"]
        d3["assets/logos/"]
        d4["addons/ · volumes/<br/>config/per-game/"]
    end

    AMB["lib/xenia<br/><b>classified two ways — issue #36</b>"]

    ROOT -.->|"launch args resolve it under<br/>@GAMECORE_PATH@, and paths._LAYOUT<br/>has no key that can name it"| AMB
    DATA -.->|"uninstall.sh keeps it as player data:<br/>lib/xenia/content/&lt;XUID&gt;/&lt;TitleID&gt;/<br/>holds Xbox 360 <b>saves</b>"| AMB

    style AMB fill:#b3261e,color:#fff,stroke:#7f1d1d,stroke-width:2px
```

**The ambiguity is real and it is not resolved.** `GAMECORE_DATA` defaults to
`GAMECORE_ROOT`, so today they are one directory and nothing breaks. It breaks
the moment the roots genuinely split, or the install root goes read-only:

| Says | What it says about `lib/` |
|---|---|
| `catalog/xenia/pack.json` | `"dest": "lib/xenia"`, and launch args `'@GAMECORE_PATH@/lib/xenia/xenia_canary.exe'` — the **code** root |
| `paths._LAYOUT` | **no entry at all.** There is no logical name that resolves to it, so `data_dir()` cannot express it |
| `install/uninstall.sh` | keeps it, in the same breath as the player's data: `emu\|config\|assets\|lib) continue` |
| `update/linux.sh` | does **not** exclude it from the deploy rsync |

Xenia Canary is portable — it keeps its content beside its own executable rather
than under `$HOME` — so for this one system the ROMs are on the data side and the
**saves are not**. `lib/xenia/content/<XUID>/<TitleID>/` is a data directory
sitting physically inside the code root, and `/lib/` is gitignored, so nothing in
the repo hints at it either.

This is [issue #36](https://github.com/p4v1c/GamecoreRenew/issues/36), and it
needs a decision rather than a patch. Do not "tidy" it by adding a `_LAYOUT`
entry on its own: the launch args, the uninstaller's keep-list and the rsync
excludes all have to move together, and a migration has to carry existing saves.

`backend/config.py` re-exports the names the backend has always imported:

| Constant | Value |
|---|---|
| `GAMECORE_ROOT` | the installation |
| `GAMECORE_DATA` | the data root |
| `SYSTEMS_FILE` | `<DATA>/config/systems.json` |
| `APPS_FILE` | `<DATA>/config/apps.json` |
| `PLAYTIME_DB` | `<DATA>/config/playtime.db` |
| `COVERS_DIR` | `<DATA>/emu/covers` |
| `LOGOS_DIR` | `<DATA>/assets/logos` |
| `art_dir()` | `<DATA>/assets/art` — `<id>/<name>.webp\|png\|jpg\|svg` replaces that pack picture; excluded from updates like `assets/logos` |
| `ASSETS_DIR` | `<ROOT>/assets/` — the shipped tree |
| `BACKEND_PORT` | `$GAMECORE_BACKEND_PORT` or 8765 |
| `APP_VERSION` | contents of `VERSION` (written by the OTA script) |
| `GITHUB_REPO`, `UPDATE_ASSET` | `p4v1c/GamecoreRenew`, `gamecore-ota.tar.gz` |
| `THEGAMESDB_API_KEY` | env only — never committed |
| `SCRAPER_LANG` | `$GAMECORE_SCRAPER_LANG` or `en,fr` — language of the scraped text |
| `DEBUG` | must be `false` on a device |

### Environment

Read straight from the process environment, never from a file in the repo. The
installer writes them into one systemd drop-in,
`/etc/systemd/system/gamecore-backend.service.d/override.conf`, mode 600.

| Variable | For |
|---|---|
| `GAMECORE_PATH` | the install root (code) |
| `GAMECORE_DATA` | the data root. Unset = the install root, which is what every box installed before the split runs |
| `GAMECORE_BACKEND_PORT` | the API port |
| `THEGAMESDB_API_KEY` | `services/scraper.py`, `services/metadata.py` |
| `SCREENSCRAPER_DEV_ID` / `SCREENSCRAPER_DEV_PASSWORD` | **developer** credentials, granted per software on the ScreenScraper forum. The id is the developer's *pseudonym*, not the number in the `devinfos.php` URL |
| `SCREENSCRAPER_USER` / `SCREENSCRAPER_PASSWORD` | a **member** account. It carries the daily quota and the thread count |
| `GAMECORE_SCRAPER_LANG` | comma-separated, most preferred first. Default `en,fr` |
| `GAMECORE_STANDBY_VIDEO_CAP_GB` | disk budget for the standby TV's game clips, in GB (decimals allowed). Default `5`; `0` removes every clip ([why](04-backend-services.md#standby_videospy--standby_pickspy)) |
| `GAMECORE_WARM_MEDIA` | which media `prefetch` downloads at boot beyond the cover. Comma-separated, empty to warm nothing. Default `box-front,box-3d,box-spine,box-back,screenshot-gameplay,screenshot-game-title` ([why](04-backend-services.md#prefetchpy-82-l)) |

### Moving the data out — who has to be told

`scripts/migrate-userdata.py` copies the bytes and stops; the epilogue it prints
is the procedure, and it is longer than "set one variable" because the data
root is read by more than one process, and they do not all learn it the same
way. Each of these was found by reading the code that resolves the root, and
each was wrong on the day the two trees first differed:

| who | reads the root from | what goes wrong if it is missed |
|---|---|---|
| the backend | `gamecore-backend.service` (drop-in `userdata.conf`) | the whole point; nothing else matters until this is set |
| Electron | `gamecore-ui.service` (same drop-in) — `bootBackground()` joins `config/theme.json` onto it | reads the abandoned copy; boots to the old theme's colour |
| `update/linux.sh` | inherited from the backend that spawns it (the Settings button); **the backend's unit** when typed at a shell, which has none | the catalogue merge went into `$GAMECORE_PATH/config/systems.json` — the abandoned copy — so the live grid never gained a new emulator, a repaired launcher or a console list again. Fixed: the merge is handed both roots |
| `gamecore-addon` | **the backend's unit**, when the caller's shell has none | typed at a shell, and the shell has no `GAMECORE_DATA`. It used to fall straight back to the install: the first `update` after the move would have handed every addon's `install.sh` the old root and baked it into their units. ROM uploads into the tree the box no longer reads |
| each addon's unit | rewritten by `gamecore-addon update` from what the CLI knows | stays on the old root until `update` is run again |
| `/usr/local/bin/gamecore-addon` itself | — it is a root-owned copy that only `install/arch.sh` writes | the OTA cannot refresh it and now says so, with the command. A copy from before the split does not know `GAMECORE_DATA` at all |
| every Flatpak emulator's sandbox | `flatpak override --filesystem=…` — a list of literal paths the installer wrote (`installer/providers.py`, `catalog-query.py sandbox`) with the install root in it | **found the hard way, on the reference box, the evening it moved**: every launch handed RPCS3 a path under `/userdata`, RPCS3 said the game did not exist, the file was right there. The installer now grants both roots when they differ; an existing box adds `--filesystem=<data>` per emulator — no root needed — and the migration script prints the loop |
| the emulators' own settings | their config files: PCSX2's game-list folders, RPCS3's last-explored path, mGBA's recent files, **Ryujinx's DLC and update paths** | keep working only while the old tree still exists. Not rewritten by anything — they are the emulator's — so the script says to grep for them before the old side is ever deleted |

The migration script prints all of it in order, plus a check to run before
playing anything and the way back. `backend/tests/test_migration.py` asserts
the epilogue names every row above; `test_ota_merge_roots.py` runs the OTA's
merge with two roots that genuinely differ; `test_addon_contract.py` runs the
CLI against a fake `systemctl`; `test_paths.py` reads `electron/*.js`.

Two operational notes the script also prints: symlinks are **not** copied and
are listed with their targets (`rglob` neither follows nor descends into them),
and a live SQLite file should not be copied under a running backend. On btrfs
`shutil.copy2` (Python ≥ 3.14, `copy_file_range`) is a reflink — measured on the
reference box: 321 GiB copied, nothing written — so the copy is instant and the
old side frees nothing when deleted until the new side diverges.

**The N64 slot is keyed `gopher64` and runs Rosalie's Mupen GUI.** That
mismatch is deliberate (a Linux pack, kept until the packs are rewritten for
Windows): gopher64 set no `WM_CLASS` on its window, which the Linux bezel
overlay needed.

The id was not renamed because `config/` is excluded from the OTA. A renamed id
reaches new installs only, and leaves every existing box pointing at an
emulator the installer no longer installs — the same failure mode as the PS1
`*.cue` extension. It is also the key for `emu/gopher64/`, the covers, the
metadata cache and the playtime rows, so renaming it means a
four-store migration for no user-visible gain: the label reads "Nintendo 64"
either way.

`update/linux.sh` reports a box whose N64 entry still launches gopher64, with
the commands to switch it.

RMG's controller config is **a saved snapshot when one exists, and RMG's own
generic profile otherwise.** It used to be capture-only, and that made it the
last red system on the 15 August 2026 bench: at its zero point the game says
*"connect a controller to socket 1"*, and the only way out was to map the pad by
hand in RMG. Two earlier attempts to write it failed against what RMG produces:

```ini
[Rosalie's Mupen GUI - Input Plugin Profile 0]
PluggedIn = True
DeviceName = "PS4 Controller"
DevicePath = "/dev/hidraw0"
DeviceSerial = "40:1b:5f:b9:ea:8d"
A_Name = "cross"        # not the generic "a" of the shipped fallback_profile
```

`PluggedIn` is what attaches a controller to the N64 port. Without it the game
itself refuses to start — *"connect a controller to socket 1"* — however
complete the rest of the section looks, and `ControllerMode 0` ("automatic")
does not supply it.

What the two attempts were missing is that **RMG identifies a pad by three
strings compared at once** (`Source/RMG-Input/main.cpp:647`): `DeviceName`,
`DevicePath` and `DeviceSerial`, read from `SDL_GetGamepadName / Path / Serial`.
One of the three wrong and the profile does not attach, in the same silence a
wrong GUID disposes Ryujinx's slot. None of the three is derivable from a
vendor:product — SDL names the node of whichever driver it reads the pad
through, and only a HIDAPI-driven pad has a serial at all. Measured, same
instant:

| pad | `DevicePath` | `DeviceSerial` |
| --- | --- | --- |
| DualShock 4 `054c:09cc` | `/dev/hidraw0` | `40:1b:5f:b9:ea:8d` |
| Xbox Wireless `045e:02fd` | `/dev/input/event14` | `""` |

`controllers.sdl3_identity()` takes the path and the serial from one SDL3 probe;
`resolve_name()` answers the name, as it does for every other SDL3 consumer. A
restored snapshot gets the same treatment, because a captured `DevicePath` pins
the number the pad happened to hold that day and hidraw numbering follows
connection order.

The **bindings**, by contrast, are the same on every controller and are not
guessed per pad. RMG's `InputType` 0 and 1 carry `SDL_GameControllerButton` and
`SDL_GameControllerAxis` *constants*, not raw indices — types 2/3/4 are the raw
space and nothing writes them. So the table is RMG's own `fallback_profile` from
`Data/InputProfileDB.json`, which is also what RMG itself answers for any pad
outside the three entries its database holds.

The button *names* stay generic: they are cosmetic, RMG rewrites them to the
pad's own vocabulary (`"cross"` for a DualShock 4's `a`) when the owner maps by
hand, and a guessed label is a claim about a pad nobody measured.

**A saved snapshot always wins.** A snapshot saved by the former **Scan
mapping** button (since removed) is restored whenever the pad reconnects — the
reference box carries one whose N64 `B` sits on SDL `b` rather than the generic
profile's `x`. To keep a new hand-made RMG mapping, turn the N64's autoconfig
off in Settings → Controllers → Per-emulator exceptions. The test is
`snapshots.exists()`, never a falsy return from `restore()`, which answers None
both for "no snapshot" and for "already applied". `extract` takes the
`[Rosalie's Mupen GUI - Input Plugin…]` sections and nothing else, so neither
path rolls back the video settings living in the same `mupen64plus.cfg`.

**Language.** ScreenScraper localises synopses **and genre names**, so a French
preference gives `Course, Conduite` where an English one gives
`Racing, Driving`. English is the default because the interface is: a library
whose buttons read "PLAY TIME" and whose synopses are in French is not a choice
anyone made. Upstream `gamescrape` prefers French — right for its own users,
wrong here.

It is applied **at scrape time**, not at display time: the chosen text is what
lands in the cache. So the language is recorded with the entry (`lang`, in both
the gamemedia manifest and the metadata cache) and an entry in another language
is reconsidered on read. Changing the variable therefore re-scrapes the library
by itself — one `jeuInfos` per game, and the already-downloaded media are kept,
so no artwork is transferred twice.

The two ScreenScraper levels are not interchangeable and both are needed:
`jeuInfos.php` answers `403` without the first, and gives a level-0 quota
without the second. Confusing them is the usual cause of a scrape that returns
nothing. With none of them set, the gamemedia tier reports itself unavailable
and covers resolve exactly as they did before it existed.

The developer credential is shared by everyone running the same softname, so it
is never committed and never read from the repo — that is also why the 1.2 s
rate limit in `services/gamemedia` is not negotiable: exceeding it blacklists
the id for every GameCore box, not just this one.

`resolve_path(raw)` turns a config-relative string into an absolute `Path`;
absolute inputs pass through unchanged. Every `romsPath` and `iconPath` goes
through it.

## External disks — `services/storage.py`, `services/storage_monitor.py`

"I plug my ROM disk in" is one of the first three things anyone expects from a
console in a living room. Two roots, and the difference between them is the
whole design:

| | what it is |
|---|---|
| the **mount point** | where udisks decided to put the filesystem. Not ours, **not stable**, and not something to record anywhere |
| the **stable link** | `<DATA>/volumes/<slug>` — a symlink GameCore owns, named after the disk's label, re-pointed at wherever it landed today |

The split exists because udisks mounts at `/run/media/<user>/<label>` and the
name it picks is not reproducible: plug the same disk in twice without a clean
unmount in between and the second mount is `LABEL 1`. A `romsPath` recorded
against a real mount point is a library that works until someone pulls the
cable, and then silently scans nothing.

`<DATA>/volumes/my-disk/nintendo` is a relative path like any other in
`systems.json`, so `resolve_data_path` already resolves it and **no consumer
needed changing** — that is the P3 split paying for itself.

`storage_monitor` polls every 3 s: it mounts an arrival, re-points its link, and
broadcasts `storage:mounted` / `storage:removed` / `storage:failed`. Polled and
not udev-driven on purpose — reaching udev events means either a netlink socket
held open with the right group membership or a rule running something as root on
every device change, and that is a privilege the box does not otherwise need to
save a few seconds on an event that happens when a human walks across a room.

Nothing is cache-invalidated on the way, and that is not an omission: games are
scanned per request from `romsPath`, so the moment the link is re-pointed the
next scan already reads the new disk.

### A disk pulled out mid-game

Expected, not exceptional — a living-room box meets it sooner or later, and
pulled without warning is the default. `storage:lost` names the game and says
what was lost. It cannot be repaired from here: the emulator holds an open
descriptor on a device that is gone, so it fails on its next read and its next
save does not land. Saying so beats a freeze with no explanation, which is what
this looked like before.

Every reader treats a vanished path as a normal state — `iter_rom_files`
already returns nothing for a directory that is not there — so hot removal
costs an empty system, never a traceback that takes the grid down.

### ⚠ exFAT and NTFS carry no POSIX permissions

**ROMs are fine on them. Emulator saves are not.**

Every file on exFAT takes the uid, gid and mode the mount options impose, for
the whole filesystem. A Flatpak emulator writing a save there does not behave as
it does on ext4: permissions cannot be preserved, a lock file cannot be trusted,
and an interrupted write has no atomic rename to fall back on.

`storage.NO_POSIX_PERMISSIONS` lists them and `describe()` attaches
`saves_warning`, which the storage screen shows in amber — never red. A disk
formatted the way every disk in a shop is formatted must not read as broken;
it must read as "put your ROMs here, keep your saves on the internal disk".

GameCore does **not** currently relocate saves onto an external disk, and this
is why. See also the open decision on Flatpak saves below.

## What lives in `config/`

| File | Written by | Read by | In git? |
|---|---|---|---|
| `systems.json` | installer, from `install/generated/systems.json.dist` | `routers/systems.py` | **yes** |
| `apps.json` | installer, from `install/generated/apps.json.dist` | `routers/systems.py` | **yes** |
| `themes/` | shipped + `update/linux.sh` (adds only what is missing) | `services/themes.py` | **yes** |
| `theme.json` | `services/themes.set_active()`, atomically | `services/themes.get_active()` | no |
| `addons.json` | `gamecore-addon` CLI | `routers/addons.py` | no |
| `standby.json` | `POST /api/standby/config`, atomically | `services/standby.py` | no |
| `session.json` | `services/process_manager.py`, atomically | idem, at startup | no |
| `auth.json`, `auth_secret` | `services/auth.py`, mode 0600 | idem | no |
| `playtime.db` | backend (SQLite) | backend | no |
| `profiles/profiles.json` | `services/profiles.py`, atomically | idem | no |

**The OTA rsync excludes `config/` entirely** — but "not in git" is *not* true of
all of it, and the distinction matters when deciding whether overwriting a file
is data loss:

- The **catalogues** (`systems.json`, `apps.json`, and the
  bundled themes) are versioned. `install/arch.sh` regenerates the first two from
  `install/generated/*.dist` on **every** run, so editing them in place is not durable —
  edit the `.dist` files.
- The **state** (`theme.json`, `addons.json`, `standby.json`, `session.json`,
  `auth.json`, `auth_secret`, `playtime.db`, `profiles/`) is never in git and exists only on
  the box. That is its identity: credentials, installed addons, play history,
  the selected theme. Treat overwriting one as data loss.

Everything written here uses the tmp-file + `os.replace` pattern from
`auth._write_private()`. `write_text()` truncates before it writes, so an
interrupted write left a JSON file that could not be parsed — and the selected
theme, or the standby timings, silently reverted to defaults.

---

## `config/systems.json`

An array. One entry per emulator:

```jsonc
{
  "id": "azahar",                    // primary key used everywhere
  "type": "emulator",
  "label": "Nintendo 3DS",           // shown on the tile
  "platform": "3DS",                 // short badge
  "color": "#ff0096",                // UI accent for this system
  "iconPath": "assets/logos/3ds.png",
  "path": "flatpak",                 // "flatpak" or an absolute binary path
  "args": "run org.azahar_emu.Azahar -f",
  "romsPath": "emu/azahar/",
  "extensions": ["*.3ds", "*.zip"],
  "libretroSystems": ["Nintendo - Nintendo 3DS"],   // cover scraping key
  "scanDirs": false                  // true → games are folders (PS3, PS4)
}
```

| Key | Consumed by |
|---|---|
| `id` | everything — `list_all()` lookups, cover cache paths, per-game config |
| `path` + `args` | `process_manager.launch()`; `args` goes through `shlex.split`, the ROM path is appended |
| `romsPath` | `list_games()`, **and the containment check in `launch_game()`** |
| `extensions` | `rom_scanner.matches_ext` (glob patterns) |
| `scanDirs` | `iter_rom_files` yields directories; enables `local_media.get_title()` |
| `libretroSystems` | `scraper._get_index()` |
| `color`, `iconPath`, `label`, `platform` | UI only |

Two optional keys the code honours but the shipped config does not currently
use:

| Key | Effect |
|---|---|
| `"gamepadTrigger": true` | ignored on Windows (it re-fired udev for Flatpak apps); kept so a Linux tile still loads |
| `"fullscreen": {…}` | `fullscreen_enforcer.enforce()` makes the launched job's window borderless fullscreen, for apps with no fullscreen flag |

### Splitting a system — `scripts/split-systems.py`

`dolphin` and `mgba` each covered several consoles. They are now one pack per
system (`gamecube`, `wii`; `gb`, `gbc`, `gba`, see
[10](10-catalog-and-install.md#one-emulator-several-systems)). A box installed
before that keeps its old tile and it keeps launching; **nothing moves on its
own**, not at an update, not at boot. The owner runs the move, by hand:

| Step | Command |
|---|---|
| 1. back up the data root | `tar -czpf ~/userdata-before-split.tgz -C / userdata` |
| 2. read the plan (writes nothing) | `/opt/GameCore/.venv/bin/python3 /opt/GameCore/scripts/split-systems.py --data /userdata` |
| 3. close every game, then apply | same command with `--apply`, as the box's user (it refuses root) |
| 4. reload the grid | restart GameCore or reboot; every game should be on its system's tile and launch |

What it moves, **by rename on the same disk only** (never a copy, a delete or
an overwrite):

| Filed under the old id | Where it goes |
|---|---|
| the ROM, plus every file sharing its name (`.sav`, save states) | `emu/<system>/`; the dry run lists each `.sav` |
| covers, metadata, scraped media | `emu/covers|metadata|gamemedia/<system>/` |
| per-game settings | `config/per-game/<system>/` |
| playtime rows (and `dolphin:settings`) | same row, new `system_id` |
| `controller-autoconfig.json` keys | the new ids |
| the old tile in `systems.json` | replaced in place by the new tiles, in catalogue order |

Which system a dump belongs to: its extension when only one successor claims
it, the file inside a single-file `.zip`, else the GameCube/Wii magic word in
the disc header (`.iso`, `.rvz`, `.wia`, `.ciso`, `.wbfs`). A dump it cannot
place, or whose destination already exists, **stays**, with its companions —
and so does the old tile, so it remains playable. Re-running is safe: it finds
nothing, or the same leftovers. Saves under `~/.var/app` (Dolphin's memory
cards and NAND) are keyed by game id and are not touched; a `ryujinx` →
`switch` move keeps Ryujinx, so its saves stay where they are. Not carried: Orbit's
and Jelly's favourites, which live in the kiosk browser's storage
(`orbit-favourites`, `jelly-favourites`, both `system:filename`) where no script
reaches; a favourite on a moved game has to be marked again.

## `config/apps.json`

Same shape, minus the ROM keys, plus `"kind": "app"`:

```jsonc
{ "id": "steam", "kind": "app", "type": "application", "label": "Steam",
  "platform": "Steam", "color": "#1f6fb3", "iconPath": "assets/logos/steam.png",
  "path": "flatpak", "args": "run com.valvesoftware.Steam" }
```

`list_all()` concatenates systems + apps. `list_games()` returns `[]` for
anything with `kind == "app"` or `type == "application"`.

## `config/addons.json`

Written by the `gamecore-addon` CLI from each addon's `addon.json`. Served
verbatim by `GET /api/addons` and `GET /gc/addons`. Fields that matter
downstream: `name`, `label`, `port`, `path` (`/roms`, `/saves`, `/rpcs3`),
`type` (`web` addons get a nav link), `version`, `api`.

It lives under `<DATA>/config/`, and the CLI and `routers/addons.py` had to
move together — the CLI writes it, the backend reads it, and a box where only
one of them had moved would show an empty Addons screen while having addons
installed.

### The hook contract — `api: 1`

**This is a public contract, and versioning it is a breaking change.** It is
the part of the split that reaches outside this repository: third-party addons
were written when `$GAMECORE_PATH` was writable, and a read-only root breaks
them at install time, on a box in somebody's living room, with the Addons
screen showing a script's stderr.

So an addon must declare `"api": 1` in its `addon.json`. One that does not is
**refused before anything runs**, by name, with the porting instructions — the
person reading that message is a player on a television, not the addon's
author.

What `install.sh` / `uninstall.sh` receive:

| Variable | Meaning |
|---|---|
| `GAMECORE_DATA` | the data root. **Write here.** |
| `ADDON_DATA_DIR` | `<DATA>/addons/<id>/`, created before the hook runs — an addon should not have to know the layout to keep a config file |
| `GAMECORE_PATH` | the install root. **Read-only.** Passed so an addon can find shipped code, never so it can write |
| `GAMECORE_ADDON_API` | the version the CLI speaks |
| `ADDON_DIR`, `USER_NAME`, `GAMECORE_BACKEND_PORT`, `OFFLINE`, `PAYLOAD_DIR` | unchanged |

**The gate is on install and update, never on remove.** A box updated to this
release has addons installed by the old CLI, none of which declares a version.
Refusing to remove them would strand the player with something they cannot
uninstall from the screen that installed it — the update would have taken away
the exit.

### `/opt/gamecore-addons` — the fourth category

The checkout is git-managed code, but the player installs and removes it at
runtime, so it can live on neither a read-only root nor the OTA. It is
**mutable code**, which the code/data split had no name for, and it belongs on
the data side: new installs put it at `<DATA>/addons/_repo` (addon ids cannot
start with `_`, so it cannot collide with an addon's own directory).

An existing `/opt/gamecore-addons` keeps being used. Boxes installed before
this release have their addons there with services already pointing into it;
relocating it from under them would break running addons for no gain, and the
player would have no way to tell why.

## `config/standby.json`

`{enabled, screensaver_mins, sleep_mins, wake_settle_secs}` — shape enforced by
the `StandbyConfig` pydantic model in `routers/standby.py`. `wake_settle_secs`
(0-8, default 2.5) is how long the screen stays black after a wake once the TV
link is back (`services/display_ready.py`); Settings → System edits it.

## `config/auth.json` + `auth_secret`

`auth.json` = `{hash: argon2id, generation: int}`. `auth_secret` = 32 random
bytes, the HMAC key for session cookies. Both 0600, written atomically by
`_write_private()`. Bumping `generation` invalidates every live session.

`_auth()` swallows `UnicodeDecodeError` as well as the JSON and OS errors:
`read_text()` raises that one on a non-UTF-8 file, and every LAN request goes
through here — a truncated or foreign `auth.json` used to 500 the whole proxied
surface, `/login` included, leaving no way back in short of SSH.

## `config/profiles/profiles.json`

`{active, auto_login, profiles: [{id, name, color, avatar, created, primary, theme, controllers}]}`
(`auto_login`: "Log in automatically", absent = off; `theme`: the theme put on
when the profile is picked, an id or null for the built-in look; a new profile
starts on `themes.shipped()`, and a pick in Settings → Themes is the active
profile's only; an `avatar` no longer offered reads as null; `controllers`: `[{id, name}]`, the pads
shown on the profile's page, display only, `id` = MAC or `vendor:product`). `id` is 16
random hex digits and never derived from the name, so a rename moves nothing.
`primary` marks the profile that owns what the box held before profiles, and
keeps today's save locations; other profiles' saves go to
`emu/profile-saves/<id>/` ([Assets](#assets)). Created
with one unnamed primary profile on first read: unnamed means "no profiles"
(players shown as P1-P4), and no other profile can be added until it has a
name. A lone primary still called "Player 1" (the old default) reads as
unnamed. Schema and checks:
[`profiles.py`](04-backend-services.md#profilespy--who-plays-on-this-box).

## `config/session.json`

`{"sessions": [...]}`, one row per resident session: `job` (the Job Object's
name, `Local\GameCore-<uuid>`), `pid` (the job's first process), game/system
identity, executable and arguments, start time, foreground/background state,
suspended-time counters, and the first process's identity: `boot_id` (the
registry `BootId` counter) and `leader_start` (its creation time,
`platform/processes.py`). It is written atomically whenever a session
launches, suspends, resumes or exits; the file disappears when no session
remains.

It exists so a **restarted backend can still reach a game it did not start**.
Without it the new process came up with no session, so `is_running` was
false, `kill()` returned at its first line, and the emulator stayed fullscreen
and unkillable: the double-PS shortcut could never close it again. The UI is a
separate process and does not restart with the backend, so it kept asking and
nothing answered.

`adopt_orphan()` runs in the lifespan: a row is adopted (reported by
`/api/games/session`, killable via `POST /api/games/kill`) only when its job
opens again by name, still holds a process, and both identity fields match
(`backend/services/process_identity.py`); otherwise the row is dropped. A job
name dies with the job and with the boot, and a PID is reused after a power
cut: adopting a stranger's processes would kill them on double-PS. The job's
name outlives the backend because the game's first process holds a handle to
its own job (`platform/jobs.py`): a game whose first process has exited (a
launcher that quit) is not adopted, as on Linux. This was chosen over killing
the game on shutdown, which would take unsaved progress with it on every
update **and** would still leave a crash, where no shutdown code runs at all,
stranding the player.

Rows written by the Linux build (`pgid`, legacy flat fields) are not read:
no Windows box ever wrote one. New launches enforce one resident game:
selecting the same suspended identity resumes it and selecting another is
refused by name.

---

## `playtime.db`

Created by `db.py:init_db()`.

> **`game_key` is a filename.** It is whatever the library listed, which makes
> it a *reference to something outside the database* — so anything that changes
> what gets listed orphans the rows keyed on the old name. They are not
> deleted, they simply stop being reachable, and a game played for hours
> reports as never played. That is not hypothetical: hiding a `.bin` behind its
> `.cue` did it to one row on the reference box. Any future change to what
> `rom_scanner` lists needs the same treatment
> ([`playtime_repair`](04-backend-services.md#playtime_repairpy-105-l)).

```sql
CREATE TABLE playtime (
    game_key      TEXT PRIMARY KEY,
    system_id     TEXT NOT NULL,
    total_secs    INTEGER NOT NULL DEFAULT 0,
    session_count INTEGER NOT NULL DEFAULT 0,
    last_played   TEXT
);
CREATE TABLE sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key   TEXT NOT NULL,
    system_id  TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    duration   INTEGER
);
```

`_watch()` writes with an upsert:

```sql
INSERT INTO playtime (…) VALUES (…)
ON CONFLICT(game_key) DO UPDATE SET
    total_secs = total_secs + excluded.total_secs,
    session_count = session_count + 1,
    last_played = excluded.last_played
```

`game_key` is the ROM filename (or the system id for an app), which is why
renaming a ROM resets its history.

**Per profile.** `playtime` holds the primary profile's rows, which are every
row from before profiles; a finished game of another profile goes to
`profile_playtime` (same columns plus `profile_id`, keyed by
`(profile_id, system_id, game_key)`), and `/api/playtime` reads the active
profile's ([`playtime_rows`](04-backend-services.md#playtime_rowspy--playtime-per-profile)).
The playtime repair and `split-systems` re-key `playtime` only.

`get_db()` re-opens the handle if the cached connection has gone stale — a
long-lived aiosqlite connection can die under the box's suspend cycles.

---

## Caches on disk

| Path | Contents | Invalidation |
|---|---|---|
| `emu/covers/<system>/<name>.png\|jpg` | resolved cover art | manual — **drop a file here to pin a cover** |
| `emu/covers/<system>/<name>.miss` | negative cache | 7 days, or `?refresh=1` |
| metadata cache | game text, incl. negative results | as above |
| `emu/gamemedia/<system>/<key>/game.json` | one game's manifest — metadata + every media it has; `steam` = the store app id asked for a PC spec (`null`: not on the store) | none. A game does not change; `?refresh=1` rescrapes |
| `emu/gamemedia/<system>/<key>/<type>.png\|mp4\|pdf` | the media actually downloaded | idem |
| `emu/gamemedia/systems.json` | ScreenScraper's 250-system registry (~4 MB) | none — it is the source of the console aliases |
| `emu/gamescrape/launchbox.sqlite` | offline LaunchBox index, 185 k games (234 MB) | built once by `install/steps/build-media-index.sh` (full installs only), then never touched. The backend does NOT rebuild it: a stale schema disables the tier rather than triggering a 234 MB download from an HTTP handler. `gamescrape.py --refresh` is the manual cure |

Everything gamemedia writes lives under `emu/`, with the ROMs and the covers,
because that directory is excluded from both git and the OTA rsync. A manifest,
a 234 MB index and the artwork all survive an update. Upstream defaults to
`~/.cache`, which under systemd resolves against whichever `HOME` the unit
happens to have.
| `~/.config/gamecore-electron/Cache` | Chromium HTTP cache | cleared by the OTA script and on every Electron start |
| `logs/<section>/…` | backend log, each launch's output (`services/logs.py`) | capped per file; Settings → System → Logs purges all of it |

## OPEN DECISION — Flatpak saves are in neither tree

**This one is not settled, and it is deliberately not settled here.** It needs a
call from the project owner; what follows is the material to make it with.

The split promises that `tar czf backup.tgz /userdata` restores a box. Today it
does not, and the reason is that the emulators are Flatpaks: they write under
`~/.var/app/<the installed appIds entry>/`, which is neither the installation
nor the data tree.

Worse, saves are **not** in a directory of their own — several sit inside what
looks like a configuration tree, which is why "back up the config directory,
skip the saves" is not an available option:

| Emulator | Where the saves are |
|---|---|
| RPCS3 | `config/rpcs3/dev_hdd0/home/*/savedata/` — inside the config tree |
| Dolphin | `data/dolphin-emu/GC/`, `.../Wii/` |
| PCSX2, DuckStation | `memcards/` |
| Cemu | `mlc01/usr/save/` |
| Ryujinx | `bis/user/save/` (+ the index `bis/system/save/8000000000000000/`) |
| Eden (before Ryujinx came back) | `nand/user/save/`, only read by the Eden import |
| azahar | `sdmc/`, `nand/` |
| mGBA, melonDS | `.sav` files **next to the ROMs** (melonDS: a non-primary profile's in `emu/profile-saves/`) |

The last row is the one bright spot: those `.sav` files live in `emu/<system>/`
and are therefore already inside `/userdata` and already backed up. Every other
row is outside it.

The `save-manager` addon covers backup and restore, and it works — but
`install/installer-gui/gamecore_installer.py` offers it with `"default": False`,
so a box installed by clicking through the installer has no save backup at all
and nothing says so.

### Option A — turn `save-manager` on by default

Change the one `default` flag. The addon already exists, is already tested, and
already knows each emulator's layout.

- Backups become opt-out rather than opt-in, which matches what a player
  expects from a console.
- It is a *backup*, not a relocation: saves still live under `~/.var/app`, so
  `tar czf backup.tgz /userdata` still does not capture them. The promise of the
  split stays half-true, and the restore procedure stays two commands.
- Costs a service and its port on every box, including ones whose owner would
  never open the screen.

### Option B — redirect the save directories into `/userdata`

Point each emulator's save location at `<DATA>/saves/<system>/`, by
configuration where the emulator supports it and by symlink where it does not.

- `tar czf backup.tgz /userdata` becomes literally true, which is the property
  the whole phase is for.
- The redirection has to be right for **each** emulator, and being wrong is
  silent in the worst way: the emulator starts, finds no save, and offers a new
  game. That is indistinguishable from a corrupt save to the person holding the
  pad.
- Symlinks into a Flatpak sandbox need a filesystem override per app, so this
  also enlarges the Flatpak permission surface.
- It touches live save data on existing boxes, so it needs its own migration —
  with the same care as `scripts/migrate-userdata.py`, and probably after it.

### What is true either way

Nothing in this phase touches `~/.var/app`. Both options are additive and can
be taken later; taking neither leaves saves exactly as safe (or not) as they
were before the split, which is the status quo and not a regression.

## Assets

| Path | Contents |
|---|---|
| `assets/logos/` | system tiles — **excluded from OTA**, users add their own |
| `backend/data/gamecontrollerdb.txt` | vendored SDL_GameControllerDB, exported as `SDL_GAMECONTROLLERCONFIG_FILE` |
| `emu/<system>/` | ROMs — excluded from OTA |
| `emu/profile-saves/<profile id>/<system>/` | saves of a profile other than the primary, for packs with `profileSaves` (`paths.profile_saves_dir()`, `services/profile_saves.py`); created on demand, never moved or deleted by GameCore, kept when the profile is deleted. `emu/profile-saves/.primary.json`: the emulator options a profile replaced, put back for the primary (`profile_save_paths.py`). A folder swapped for a link waits beside it as `<name>.gamecore-primary` while another profile plays. Under `emu/` on purpose: outside git, the OTA rsync **and** the update's `.prev` snapshot, whose restore would roll a save back |
