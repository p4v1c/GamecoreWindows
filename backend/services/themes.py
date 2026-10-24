"""Theme discovery and selection.

A theme is a directory under config/themes/ holding a manifest, an ES module and
its assets. It lives in the data root, which an update does not replace;
update/finish.py installs and upgrades the bundled ones there.

Nothing here executes theme code — the browser imports the module. This module
only reads manifests, validates them and remembers which theme is active.
See docs/themes/README.md for the manifest contract.
"""
import json
import logging
import os
import re
from pathlib import Path

from .paths import config_dir, themes_dir

log = logging.getLogger(__name__)

THEMES_DIR = themes_dir()
STATE_FILE = config_dir() / "theme.json"

# Bumped only when a surface or an SDK key is removed. Adding one does not.
# Must match frontend/src/lib/themeSdk.ts — a test pins them together. Bumped
# to 2 when `sdk.defaults.createSettings` and `createPowerView` became required
# by the shipped themes; see that file for what went wrong when it did not.
# 6 adds the aggregate/spatial library contract required by Orbit.
# 8 removes gp:menu/gp:power from theme input and libraryOmit 'options'.
# 11 adds defaults.CrtStandby (themes feature-detect it).
# 12 adds defaults.useLocalWake and format.playedAgo (Jelly feature-detects them).
SDK_VERSION = 12

# What a box shows when nobody has chosen — a fresh install, or one whose config
# directory was replaced. Not a hardcoded look: it is a theme id like any other,
# checked against what is actually installed before it is handed out, and any
# selection the owner makes overrides it permanently.
#
# It is deliberately NOT what safe mode falls back to. That path still lands on
# the built-in UI, because the whole point of a rescue is to reach something
# that is not the theme you are escaping.
SHIPPED_DEFAULT = "shelf"

# The two surfaces a theme owns: the boot animation and the frontend body.
# Both are mandatory — a theme dresses the whole UI or it does not load. Half a
# theme (a beach dashboard behind the stock splash) is what made the first
# version feel broken, so incompleteness is an error, not a fallback.
#
# It used to be nine interleaved surfaces, which is what made themes brittle:
# the theme's tree fought the host's over stacking, the modal stack and the
# containers default pages expect.
SURFACES = {"splash", "shell"}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Sound names and settings-page ids: short lowercase slugs, both keys into a
# frontend map rather than anything that touches the filesystem.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _safe_id(theme_id: str) -> str | None:
    """A theme id is a plain directory name — never a path."""
    return theme_id if _ID_RE.match(theme_id or "") else None


# The dashboard grid a theme may ask for. `None` means "whatever the host
# uses", which is what every theme written before this said by saying nothing —
# so adding this cannot change how any of them looks.
#
# It exists because the grid is a layout decision and layout is the theme's
# side of the line. A theme that wants one long row of big icons cannot fake
# it: HomeScreen.navigate() walks COLS × ROWS and wraps at the row end, so a
# rail drawn as one continuous line would skip half its contents the moment
# ROWS > 1 — and a row that lies about where the cursor goes is worse than a
# visible second row.
#
# Bounded rather than trusted. A theme is code the owner installed, but a grid
# of 0 divides by zero in `pageCount` and a grid of 400 asks the host to render
# every system on one page; neither is a look, both are a broken screen.
_GRID_MAX = 16


def _home_grid(raw, theme_id: str) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        log.warning("theme %s: `home` must be an object — ignored", theme_id)
        return None
    out = {}
    # `"paged": false` means "the whole list on one page" — no L1/R1, no
    # boundary to walk into. The host then derives `cols` from how many items
    # there actually are, which a manifest cannot know: a number written here
    # would be right until the owner installs a seventeenth system.
    if raw.get("paged") is False:
        out["paged"] = False
    elif "paged" in raw and raw["paged"] is not True:
        log.warning("theme %s: home.paged must be a boolean — ignored", theme_id)
    for key in ("cols", "rows"):
        if key not in raw:
            continue
        v = raw[key]
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= _GRID_MAX:
            log.warning("theme %s: home.%s must be an integer 1-%d, got %r — ignored",
                        theme_id, key, _GRID_MAX, v)
            continue
        out[key] = v
    return out or None


# How long a theme's launch ceremony runs, before the emulator is allowed to
# start. `None` means "start it immediately", which is what every theme written
# before this said by saying nothing — so adding this cannot change how any of
# them behaves.
#
# It exists because the host owns the moment the game starts and the theme owns
# the animation announcing it, and nothing connected the two: LibraryScreen set
# `launching` and called the API on the same line, so the emulator's window took
# the screen while the cartridge was still going in. The theme cannot fix that
# from its side — it never calls the launch, it only watches the flag.
#
# Bounded rather than trusted, same as the grid above: a theme that asks for a
# minute of ceremony has turned a console into something that ignores the
# button for a minute. Five seconds is longer than any boot animation worth
# watching, and it is a ceiling, not a target.
_LAUNCH_MS_MAX = 5000


def _launch_ms(raw, theme_id: str) -> int | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        log.warning("theme %s: `launch` must be an object — ignored", theme_id)
        return None
    if "ms" not in raw:
        return None
    v = raw["ms"]
    # `isinstance(True, int)` is True in Python, and `"ms": true` is a mistake,
    # not a duration.
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= _LAUNCH_MS_MAX:
        log.warning("theme %s: launch.ms must be an integer 0-%d, got %r — ignored",
                    theme_id, _LAUNCH_MS_MAX, v)
        return None
    return v


# The UI sounds a theme replaces, as `name -> file inside the theme folder`.
#
# It exists because the five host sounds are fired by the input bus, which sits
# *under* the theme layer: a shell that redrew every screen still answered every
# press with the stock bip, and there was no hook anywhere to take that over.
# The bus keeps deciding *when* a sound plays; this decides *what* plays.
#
# A name missing here falls through to the host's synthesized sound, so a theme
# replaces one sound without inheriting the job of synthesizing the other four.
def _sounds(raw, d: Path) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        log.warning("theme %s: `sounds` must be an object — ignored", d.name)
        return None
    out = {}
    for name, rel in raw.items():
        if not isinstance(name, str) or not _SLUG_RE.match(name):
            log.warning("theme %s: sound name %r must be lowercase alphanumeric — ignored",
                        d.name, name)
            continue
        if not isinstance(rel, str) or not rel:
            log.warning("theme %s: sound %s must be a path string — ignored", d.name, name)
            continue
        # A manifest names files inside its own folder and nowhere else. Without
        # this, `"move": "../../../etc/passwd"` is a path the frontend would
        # happily turn into a fetch — the theme directory is the boundary, and
        # it is checked here rather than trusted because a theme is code
        # somebody downloaded.
        if rel.startswith("/") or ".." in Path(rel).parts:
            log.warning("theme %s: sound %s path %r leaves the theme folder — ignored",
                        d.name, name, rel)
            continue
        if not (d / rel).is_file():
            # Dropped rather than fatal: the cascade falls back to the host's
            # sound, which is a theme with one missing bip instead of a theme
            # that will not load.
            log.warning("theme %s: sound %s file %r not found — ignored", d.name, name, rel)
            continue
        out[name] = rel
    return out or None


# Which host settings pages a theme's own menu can open.
#
# Declared rather than detected, because a theme's menu is arbitrary JS and
# there is nothing to introspect. The backend does not know what the full set
# is either — `DefaultSettingsPages` lives in the frontend — so this only
# carries the claim across; the diff happens where the real list is.
#
# It exists because omitting an entry has already shipped, twice. `catalog` was
# missing from the map and both bundled themes had no way to install an
# emulator; `storage` was missing from the map entirely, so no theme could
# offer safe-eject even if it wanted to. The page existed, the route existed,
# and nothing could open them.
def _settings(raw, theme_id: str) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        log.warning("theme %s: `settings` must be an object — ignored", theme_id)
        return None
    pages = raw.get("pages")
    if pages is None:
        return None
    if not isinstance(pages, list):
        log.warning("theme %s: settings.pages must be a list — ignored", theme_id)
        return None
    out = [p for p in pages if isinstance(p, str) and _SLUG_RE.match(p)]
    dropped = len(pages) - len(out)
    if dropped:
        log.warning("theme %s: %d settings.pages entr(ies) are not page ids — ignored",
                    theme_id, dropped)
    return {"pages": out}


def _read_manifest(d: Path) -> dict | None:
    """Parsed, validated manifest for one directory, or None with a logged reason."""
    f = d / "theme.json"
    if not f.is_file():
        return None
    try:
        m = json.loads(f.read_text())
    except Exception as e:
        log.warning("theme %s: unreadable manifest (%s)", d.name, e)
        return None

    missing = [k for k in ("id", "name", "version", "api", "provides") if k not in m]
    if missing:
        log.warning("theme %s: manifest missing %s", d.name, ", ".join(missing))
        return None
    if m["id"] != d.name:
        log.warning("theme %s: manifest id is %r — must equal the directory name", d.name, m["id"])
        return None
    if not isinstance(m.get("provides"), list):
        log.warning("theme %s: 'provides' must be a list", d.name)
        return None

    unknown = [s for s in m["provides"] if s not in SURFACES]
    absent = sorted(SURFACES - set(m["provides"]))
    entry = m.get("entry", "index.js")
    if not (d / entry).is_file():
        log.warning("theme %s: entry %r not found", d.name, entry)
        return None

    try:
        api_version = int(m["api"])
    except (TypeError, ValueError):
        log.warning("theme %s: 'api' must be an integer", d.name)
        return None

    preview = m.get("preview", "preview.png")
    styles = m.get("styles", "theme.css")
    launch_ms = _launch_ms(m.get("launch"), d.name)
    return {
        "id": m["id"],
        "name": m["name"],
        "version": str(m["version"]),
        "api": api_version,
        "author": m.get("author", ""),
        "description": m.get("description", ""),
        "entry": entry,
        "preview": preview if (d / preview).is_file() else None,
        "styles": styles if (d / styles).is_file() else None,
        "provides": [s for s in m["provides"] if s in SURFACES],
        "schedule": m.get("schedule"),
        "home": _home_grid(m.get("home"), d.name),
        "launch": {"ms": launch_ms} if launch_ms is not None else None,
        "sounds": _sounds(m.get("sounds"), d),
        "settings": _settings(m.get("settings"), d.name),
        # The UI needs a reason, not just a boolean.
        "compatible": api_version <= SDK_VERSION and not absent,
        "warnings": (
            ([f"unknown surface(s) ignored: {', '.join(unknown)}"] if unknown else [])
            + ([f"incomplete: does not provide {', '.join(absent)}"] if absent else [])
        ),
    }


def list_themes() -> list[dict]:
    """Every selectable theme on the box, alphabetical. Invalid ones are skipped, not fatal.

    A leading underscore marks a template, not a theme: `_skeleton` is there to
    be copied, not applied. It was being listed and then refused on selection —
    `_safe_id` rejects the leading underscore — so the picker offered something
    that could not work. Hidden here instead, which keeps the folder available
    to whoever is writing a theme without putting it in front of a player.
    """
    if not THEMES_DIR.is_dir():
        return []
    out = []
    for d in sorted(THEMES_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
            continue
        m = _read_manifest(d)
        if m:
            out.append(m)
    return out


def get_active() -> str | None:
    """The selected theme id, or None for the built-in default.

    A box that has never been asked gets `SHIPPED_DEFAULT`, which is the
    difference between "nobody chose" and "somebody chose the built-in UI".
    Those two used to be the same answer — both landed here as None — and
    conflating them is what would have broken the rescue: holding L1+R1 writes
    `{"active": null}` on purpose, and a fallback that could not tell that from
    a missing file would have put the themed UI straight back on a box whose
    owner was trying to escape it.

    So the fallback applies ONLY when the file cannot be read at all. An
    explicit null is honoured as an explicit null, for ever.
    """
    try:
        raw = json.loads(STATE_FILE.read_text())
    except Exception:
        # Never chosen — a fresh install, or a state file lost with the config
        # directory. Verified rather than assumed: a shipped default that is
        # missing or refused would otherwise leave the box quoting an id that
        # loads nothing, and the built-in UI is the honest answer then.
        return shipped()
    return raw.get("active") or None


def shipped() -> str | None:
    """The look a new box shows: `SHIPPED_DEFAULT` when it loads, else the built-in."""
    if SHIPPED_DEFAULT and any(t["id"] == SHIPPED_DEFAULT and t.get("compatible")
                               for t in list_themes()):
        return SHIPPED_DEFAULT
    return None


def check(theme_id: str | None) -> None:
    """Raise ValueError or LookupError unless `theme_id` can be selected.
    None, the built-in default, always can."""
    if theme_id is not None:
        if not _safe_id(theme_id):
            raise ValueError("invalid theme id")
        match = next((t for t in list_themes() if t["id"] == theme_id), None)
        if match is None:
            raise LookupError("no such theme")
        # Refused here rather than at boot: an incomplete theme would otherwise
        # be selectable, fail to load, and leave the player on the default UI
        # wondering why their choice did nothing.
        if not match["compatible"]:
            raise ValueError("; ".join(match["warnings"]) or "theme is not compatible")


def set_active(theme_id: str | None) -> str | None:
    """Persist the selection. None means the default theme."""
    check(theme_id)
    # tmp + os.replace, like auth._write_private: write_text truncates first,
    # so a power cut mid-write left a half-written theme.json that get_active()
    # could not parse — and the player's theme silently reverted to the default.
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"active": theme_id}, indent=2))
    os.replace(tmp, STATE_FILE)
    return theme_id
