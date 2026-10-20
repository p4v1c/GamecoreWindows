"""Central configuration.

Paths are NOT derived here any more. `services/paths.py` owns the two roots —
the installation (code, read-only) and the data (writable) — and this module
re-exports the handful of names the backend has always imported from it, so
that a consumer keeps one import and the split stays in one file.

What is left here is genuinely configuration: version, ports, credentials,
scraper language.
"""
import os
from pathlib import Path

from .services import paths

# Hardcoded False, with no way to change it without editing the file on the
# box — which meant the whole backend logged nothing below WARNING, ever.
# Diagnosing standby cost a read of /proc/<pid>/fd to establish that the
# service was reading the controller at all, because every log.info that would
# have said so was being dropped. An env var in the unit file is enough.
DEBUG = os.environ.get("GAMECORE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Version is read from the VERSION file at the repo root so OTA updates
# only need to change that one file, not config.py.
_VERSION_FILE = Path(__file__).parent.parent / "VERSION"
APP_VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "v1.0.0"
GITHUB_REPO = "p4v1c/GamecoreRenew"
UPDATE_ASSET = "gamecore-ota.tar.gz"

GAMECORE_ROOT = paths.GAMECORE_ROOT
GAMECORE_DATA = paths.GAMECORE_DATA

SYSTEMS_FILE  = paths.config_dir() / "systems.json"
APPS_FILE     = paths.config_dir() / "apps.json"
PLAYTIME_DB   = paths.config_dir() / "playtime.db"
COVERS_DIR    = paths.covers_dir()
LOGOS_DIR     = paths.logos_dir()
# The shipped assets tree — fonts, sounds, the operator-replaceable logos live
# under it but resolve through LOGOS_DIR above, which is the writable one.
ASSETS_DIR    = paths.assets_dir()

BACKEND_PORT  = int(os.environ.get("GAMECORE_BACKEND_PORT", 8765))

THEGAMESDB_API_KEY = os.environ.get("THEGAMESDB_API_KEY", "")

# Language of the scraped text — synopses and genre names, which ScreenScraper
# localises. English by default because the interface is: a library whose
# buttons read "PLAY TIME" and whose synopses are in French is not a choice
# anyone made. Comma-separated, most preferred first, e.g. "fr,en".
#
# It is read at scrape time, not at display time: the chosen text is what lands
# in the cache. Changing it therefore only affects games scraped afterwards —
# see docs/architecture/04-backend-services.md for how to re-scrape a library.
SCRAPER_LANG = [c.strip().lower() for c
                in os.environ.get("GAMECORE_SCRAPER_LANG", "en,fr").split(",")
                if c.strip()] or ["en"]

# ScreenScraper — read straight from the environment by the vendored
# gamescrape (services/gamemedia), which is why nothing here imports them.
# They are listed for the reader and for /api/sysinfo: two credential levels are
# needed, and confusing them is the usual cause of a 403.
#
#   SCREENSCRAPER_DEV_ID / SCREENSCRAPER_DEV_PASSWORD
#       developer credentials, granted per software on the ScreenScraper forum.
#       DEV_ID is the developer's pseudonym, not the number in the devinfos URL.
#   SCREENSCRAPER_USER / SCREENSCRAPER_PASSWORD
#       a member account. It carries the daily quota and the thread count.
#
# Both are required: jeuInfos.php answers 403 without the first, and a level-0
# quota without the second. The installer writes them into the same systemd
# drop-in as THEGAMESDB_API_KEY (0600, never in git). Absent, the gamemedia
# tier is simply skipped and covers resolve exactly as they did before.


def resolve_path(raw: str) -> Path | None:
    """A relative path out of systems.json/apps.json, made absolute.

    Resolved against the DATA root: the only callers are `romsPath` readers
    (utils, prefetch, playtime_repair, routers/games), and ROMs are data. With
    GAMECORE_DATA defaulting to the installation this returns exactly what it
    always did.
    """
    return paths.resolve_data_path(raw)
