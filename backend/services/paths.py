r"""Where GameCore reads code, and where it writes the player's data.

Two roots, deliberately separate:

  ``GAMECORE_PATH``   the installation — backend, venv, frontend build,
                      catalogue, installers. Read-only once the box is up.
                      `C:\Program Files\GameCore` on an installed PC.
  ``GAMECORE_DATA``   everything the player owns — ROMs, generated config,
                      covers, scraped media, overlays, playtime. Writable.
                      Chosen at install: `Documents\GameCore` by default, or a
                      folder on another NTFS volume such as `D:\GameCoreData`.

On Windows the installer always sets both (port step 7); the code root is
not writable by the console user, so the default below only ever serves a
checkout: development, the test suite, the read-only dev server. Paths are
`pathlib` throughout, and Windows paths compare case-insensitively, as NTFS
names do (`is_split()` included).

On the Linux box `/opt/GameCore` mixed the two, which is why it could not have
an immutable image, could not be backed up by copying one directory, and why
every OTA had to tiptoe around `emu/` and `config/` with rsync excludes.

Why the default is "the same directory"
---------------------------------------
`GAMECORE_DATA` defaults to `GAMECORE_ROOT`. Not to `/userdata`, not to
anything else — to exactly where the data already sits. That is not a
placeholder, it is the safety property of this whole change.

The release that introduces this module reaches production boxes over OTA,
unattended. `update/linux.sh` rsyncs into `GAMECORE_PATH` while excluding
`emu/` and `config/` — the two directories a split would move. If the new code
expected the data somewhere the OTA had not put it, every box would come up
with an empty library and no settings, and the rollback (which restores the
OLD code) would face data the NEW code had moved. Two halves out of step, and
no procedure to recombine them.

So the pointer moves first and the bytes do not move at all. After the update
the box resolves every path to the byte-identical location it used before; the
only thing that changed is that there is now one variable to set. Moving the
bytes is a separate, human-typed operation — see `scripts/migrate-userdata.py`,
which is dry-run by default and is deliberately not reachable from the updater.

The layout below keeps today's directory names
----------------------------------------------
`emu/`, `config/`, `assets/overlays/` — not `roms/`, `overlays/`, `media/`.

Renaming them here would mean one of two things, and both are worse than
waiting. Either the names change under the default root, which moves bytes on
every installed box; or the layout forks — legacy names when `GAMECORE_DATA`
is unset, new names when it is set — which ships a second layout that no test
run and no real box has ever exercised, on the release that goes out
unattended. The rename is a one-line edit to `_LAYOUT` once the data actually
moves, and it belongs with the move.

Adding a path
-------------
Put it in `_LAYOUT` and expose an accessor. Nothing outside this module should
join a data directory onto a root by hand — `backend/tests/test_paths.py`
fails the build when something does, because the failure mode is invisible:
the code works perfectly until the root goes read-only, and then it does not.
"""
from __future__ import annotations

import os
from pathlib import Path

# The installation. `Path(__file__).parents[2]` is the repo root when running
# from a checkout, which is what the test suite and a dev machine both want.
GAMECORE_ROOT = Path(os.environ.get("GAMECORE_PATH",
                                    str(Path(__file__).resolve().parents[2])))

# The player's data. Defaults to the installation — see the module docstring:
# this default is what makes the release non-destructive.
GAMECORE_DATA = Path(os.environ.get("GAMECORE_DATA", str(GAMECORE_ROOT)))

# Every writable location, relative to GAMECORE_DATA, spelled once.
#
# Keys are what the code asks for; values are where that lands today. A
# consumer names the key, never the value — that is what makes the eventual
# move a change to this table and nothing else.
_LAYOUT = {
    "config":    "config",            # systems.json, apps.json, auth, playtime.db
    "roms":      "emu",               # one subdirectory per system
    "covers":    "emu/covers",        # cover art cache
    "media":     "emu/gamemedia",     # scraped media cache
    "index":     "emu/gamescrape",    # ScreenScraper index (~234 MB)
    "metadata":  "emu/metadata",      # scraped synopses and genres
    "overlays":  "assets/overlays",   # bezels uploaded by the player
    "logos":     "assets/logos",      # logo replacements uploaded by the operator
    "art":       "assets/art",        # pack picture replacements, <DATA>/assets/art/<id>/<name>.*
    "themes":    "config/themes",     # installed themes (mutable code)
    "addons":    "addons",            # per-addon writable state, <DATA>/addons/<id>/
    "volumes":   "volumes",           # symlinks to external disks, one per label
    "pergame":   "config/per-game",   # per-game settings, <system>/<game id>.json
    "logs":      "logs",              # one folder per section, see services/logs.py
    "profiles":  "config/profiles",   # who plays on this box, see services/profiles.py
    # Under emu/ like the .sav files beside the ROMs: outside git, the OTA
    # rsync and the update's .prev snapshot, whose restore would roll saves back.
    "profile_saves": "emu/profile-saves",  # <id>/<system>/, see services/profile_saves.py
}


def use_roots(code_root, data_root=None) -> None:
    """Repoint both roots after import.

    The roots are read from the environment once, at import, because almost
    everything downstream caches a path built from them at import too. A test
    that wants a throwaway library therefore has to say so explicitly, and
    this is that seam.

    It exists because the alternative bit: `test_playtime_repair` used to
    redirect the library by assigning `config.GAMECORE_ROOT`, and once path
    resolution moved in here that assignment silently stopped doing anything.
    The tests still passed — they were asserting on a repair that now found
    nothing to repair. A seam that can be no-opped by accident is worse than
    no seam, so there is one function, and it is the only supported way.
    """
    global GAMECORE_ROOT, GAMECORE_DATA
    GAMECORE_ROOT = Path(code_root)
    GAMECORE_DATA = Path(data_root if data_root is not None else code_root)


def data_dir(name: str) -> Path:
    """A writable directory, by logical name. KeyError on an unknown one —
    a typo must not silently create a directory nobody reads."""
    return GAMECORE_DATA / _LAYOUT[name]


# ── The writable side ────────────────────────────────────────────────────────
def config_dir() -> Path:      return data_dir("config")
def roms_root() -> Path:       return data_dir("roms")
def covers_dir() -> Path:      return data_dir("covers")
def media_cache_dir() -> Path: return data_dir("media")
def media_index_dir() -> Path: return data_dir("index")
def metadata_dir() -> Path:    return data_dir("metadata")
def overlays_dir() -> Path:    return data_dir("overlays")
def logos_dir() -> Path:       return data_dir("logos")
def art_dir() -> Path:         return data_dir("art")
def themes_dir() -> Path:      return data_dir("themes")
def addons_dir() -> Path:      return data_dir("addons")
def volumes_dir() -> Path:     return data_dir("volumes")
def logs_dir() -> Path:        return data_dir("logs")
def profiles_dir() -> Path:    return data_dir("profiles")
def profile_saves_dir() -> Path: return data_dir("profile_saves")


def pergame_dir() -> Path:
    """Where a game's own settings live, and why it is not the emulator's tree.

    The file the emulator reads is DERIVED. This is the original, and the
    difference shows up the day somebody reinstalls a Flatpak: `flatpak
    uninstall --delete-data` takes `~/.var/app/<id>` with it, and every
    per-game setting the player ever placed would go with it — silently, and
    at the exact moment they were trying to fix something.

    Under the DATA root, so it is also the thing a backup already copies and
    the thing the OTA rsync already leaves alone.
    """
    return data_dir("pergame")


# ── The read-only side ───────────────────────────────────────────────────────
# Named for symmetry, so a consumer never has to reach for GAMECORE_ROOT and
# guess which side of the line it is on.
def catalog_dir() -> Path:     return GAMECORE_ROOT / "catalog"
def assets_dir() -> Path:      return GAMECORE_ROOT / "assets"
def install_bin_dir() -> Path: return GAMECORE_ROOT / "install" / "bin"
def backend_data_dir() -> Path: return GAMECORE_ROOT / "backend" / "data"
def frontend_dist_dir() -> Path: return GAMECORE_ROOT / "frontend" / "dist"


def is_split() -> bool:
    """True once the data actually lives outside the installation.

    The honest question to ask before claiming the root can be read-only: with
    the default in force these are the same directory, and nothing is split.
    """
    return GAMECORE_DATA.resolve() != GAMECORE_ROOT.resolve()


def resolve_data_path(raw: str) -> Path | None:
    """A path from a config file, resolved against the DATA root.

    `romsPath` in systems.json is relative (`emu/duckstation/`), and this is
    the single place that decides what it is relative TO. That is the hinge of
    the whole split: pointing the library at moved ROMs is this function, not
    a sweep through every caller.
    """
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else GAMECORE_DATA / p
