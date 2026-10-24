#!/usr/bin/env python3
"""gamescrape — every image of a game, from its ROM filename.

    python3 gamescrape.py "fifa-19-(europe)(fr,en).nds" -o output

Two sources, picked by --source (auto by default):

  LaunchBox      the official Metadata.zip dump, downloaded once then indexed
                 into SQLite under ~/.cache/gamescrape — everything is instant
                 and offline from then on. No credentials. Matches by NAME: 92 %
                 hit rate measured over 1950 ROMs, 90 % of them exact.
  ScreenScraper  matches by file HASH (CRC/MD5/SHA1): when the ROM is on disk,
                 the game found is certain even if the file is misnamed. Needs
                 DEVELOPER credentials (devid/devpassword) — see the
                 ScreenScraper section below; without them the script silently
                 fell back to LaunchBox.

Media available: 3D box, box front/back/spine, cartridge, disc, clear logo,
fanart, banner, screenshots (gameplay, title screen, game over…), flyers, arcade
marquee and cabinet, poster…

The filename is enough, the ROM need not exist: the parser strips separators,
region and language tags, disc number and revision, then infers the console from
the extension.

No dependencies: standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ── The façade ──────────────────────────────────────────────────────────────
#
# This file was 1 308 lines. Four seams were already drawn in it as section
# headers, and they are now four modules:
#
#     parser.py       the ROM-name vocabulary and the parser over it
#     lb_index.py     downloading and indexing the LaunchBox dump
#     search.py       finding a game in that index, and reading its record
#     media_types.py  what a media type is called, whichever source answered
#     common.py       the constants, `out()` and `slug()` all four share
#
# What stayed is the ScreenScraper client, the downloader and the CLI — the
# parts that talk to the network with credentials, and the entry point.
#
# Everything the modules define is re-exported HERE, under the names it had
# before, because `gamemedia.py` reaches into this module as `gs.<name>` for
# some forty of them and the vendored layout is documented in
# services/gamemedia/__init__.py. A split that changed one import outside this
# package would not be a split, it would be a rewrite of its callers.
# Two import paths, and both are used. Inside the backend this is a package;
# run as `python3 gamescrape.py <rom>` — the CLI this file's own docstring
# documents — it is a loose script with no parent package. Same try/except
# gamemedia.py already uses to reach this module, rather than a second
# mechanism. It is also what the split had to preserve: a caller outside this
# package changing one import would not be a split.
try:
    from . import common                                           # noqa: F401
    from .common import (                                          # noqa: F401
        IMAGE_CDN,
        MAX_AGE_DAYS,
        METADATA_URL,
        SCHEMA_VERSION,
        TIMEOUT,
        UA,
        json_mode,
        out,
        set_json_mode,
        slug,
    )
    from .lb_index import (                                        # noqa: F401
        LB_DETAIL_COLS,
        IndexUnavailable,
        build_index,
        lb_index_ready,
        open_db,
    )
    from .media_types import (                                     # noqa: F401
        SS_MEDIA,
        SS_TO_SLUG,
        lb_media_info,
        ss_media_info,
        ss_media_slug,
    )
    from .parser import (                                          # noqa: F401
        ARTICLES,
        EXT_MAP,
        LANGS,
        PLATFORMS,
        REGION_PREF,
        REGIONS,
        TAG_RE,
        TYPE_ALIASES,
        without_platform,
        normalize,
        parse_rom,
    )
except ImportError:                                    # plain-script CLI
    import common                                           # noqa: F401
    from common import (                                          # noqa: F401
        IMAGE_CDN,
        MAX_AGE_DAYS,
        METADATA_URL,
        SCHEMA_VERSION,
        TIMEOUT,
        UA,
        json_mode,
        out,
        set_json_mode,
        slug,
    )
    from lb_index import (                                        # noqa: F401
        LB_DETAIL_COLS,
        IndexUnavailable,
        build_index,
        lb_index_ready,
        open_db,
    )
    from media_types import (                                     # noqa: F401
        SS_MEDIA,
        SS_TO_SLUG,
        lb_media_info,
        ss_media_info,
        ss_media_slug,
    )
    from parser import (                                          # noqa: F401
        ARTICLES,
        EXT_MAP,
        LANGS,
        PLATFORMS,
        REGION_PREF,
        REGIONS,
        TAG_RE,
        TYPE_ALIASES,
        without_platform,
        normalize,
        parse_rom,
    )

# Re-exported so `gs.CACHE_DIR` and `gs.DB_PATH` keep answering, and kept in
# step by set_index_dir() below — services/gamemedia/__init__.py moves the index
# and backend/tests/test_gamemedia.py asserts on where it landed.
CACHE_DIR = common.CACHE_DIR
DB_PATH = common.DB_PATH


def resolve_index_dir(explicit: str | None = None):
    """Where THIS run should keep the index, or None to leave the default.

    ONE answer to "where does the index live", and this function is what makes
    it one.

    It was two. The backend imports services/gamemedia/__init__.py, which moves
    the index into GAMECORE_PATH/emu/gamescrape — inside the installation, and
    excluded from the OTA rsync so it survives updates. This file run as a
    plain script never executes that __init__, so `--refresh` built the 234 MB
    index in ~/.cache/gamescrape instead, where the backend never looks.

    Which made the remedy a lie: when the index was missing the backend printed
    "run `gamescrape.py --refresh`", and doing exactly that rebuilt 234 MB at
    the wrong path and changed nothing. Found on a real box, where the
    LaunchBox tier had been silently off since the day it was populated —
    `status()` reported launchbox_index: False with the index sitting on disk
    two directories away, and every lookup fell through to ScreenScraper alone.

    GAMECORE_PATH set means "this is a GameCore install", and then there is
    exactly one right answer. Unset, standalone behaviour is untouched:
    ~/.cache/gamescrape, as the module docstring promises.

    The index is a 234 MB cache — data, so it follows GAMECORE_DATA when the
    installation and the player's data are separate trees. Reading the
    environment here rather than importing `services/paths.py` is deliberate
    and is the one exception `backend/tests/test_paths.py` grants: this file
    is run as a plain script by `install/steps/build-media-index.ps1`, where a
    package-relative import has no package to resolve against. The fallback
    keeps it agreeing with `gamemedia/__init__.py`, which does go through
    paths.py — the two must name the same directory or the backend reports a
    media tier it does not have.
    """
    if explicit:
        return Path(explicit)
    root = os.environ.get("GAMECORE_DATA", "") or os.environ.get("GAMECORE_PATH", "")
    return Path(root) / "emu" / "gamescrape" if root else None


def set_index_dir(directory) -> None:
    """Move the LaunchBox index. The ONLY supported way to move it.

    Assigning `gs.DB_PATH` used to work because there was one module; there are
    five now, and an assignment here would leave lb_index.py reading the old
    location — the box would report a media source it does not have, which is
    precisely the failure this call exists to make unexpressible.
    """
    global CACHE_DIR, DB_PATH
    common.set_index_dir(directory)
    CACHE_DIR, DB_PATH = common.CACHE_DIR, common.DB_PATH


try:
    from .search import (                                      # noqa: E402, F401
        ROMAN, _show_details, find_game, game_details, game_images, numbers_of,
    )
except ImportError:                                            # plain-script CLI
    from search import (                                       # noqa: E402, F401
        ROMAN, _show_details, find_game, game_details, game_images, numbers_of,
    )

# ── ScreenScraper (alternate source, searches by file hash) ─────────────────
#
# Why it beats searching by name: ScreenScraper indexes ROMs by CRC32/MD5/SHA1.
# If the file is there, the match is CERTAIN — a misnamed, renamed or untagged
# file still lands on the right game, and LaunchBox's ~2 % of fuzzy matches go
# away.
#
# DEVELOPER credentials (devid/devpassword) are required, granted on request on
# the ScreenScraper forum; a plain member account is not enough and the API
# answers 403. The member account (optional) goes on top to raise the quotas.
#
#   export SCREENSCRAPER_DEV_ID=...        export SCREENSCRAPER_USER=p4v1c
#   export SCREENSCRAPER_DEV_PASSWORD=...  export SCREENSCRAPER_PASSWORD=...
#
# Check them once they arrive with:  gamescrape.py --ss-check
SS_API = "https://api.screenscraper.fr/api2/"
SS_SOFTNAME = "gamecore"
SS_HASH_LIMIT = 4 << 30          # above this, no hashing (compressed formats)

# ScreenScraper regions, in preference order given the ROM tag.
SS_REGION_PREF: dict[str | None, list[str]] = {
    "eu": ["eu", "fr", "uk", "de", "sp", "it", "wor", "us", "jp", "ss"],
    "us": ["us", "wor", "eu", "uk", "jp", "ss"],
    "jp": ["jp", "wor", "us", "eu", "ss"],
    "wor": ["wor", "eu", "us", "jp", "ss"],
    None: ["wor", "eu", "us", "jp", "ss"],
}
# ScreenScraper systemeid. Optional (hash search does without it); cross-check
# against the official list with --ss-systems.
SS_SYSTEM_IDS: dict[str, int] = {
    "nds": 15, "3ds": 17, "gba": 12, "gbc": 10, "gb": 9, "nes": 3, "snes": 4,
    "n64": 14, "gamecube": 13, "wii": 16, "wiiu": 18, "switch": 225,
    "psx": 57, "ps2": 58, "ps3": 59, "psp": 61, "vita": 62,
    "megadrive": 1, "mastersys": 2, "gamegear": 21, "saturn": 22,
    "dreamcast": 23, "segacd": 20, "xbox": 32, "x360": 33, "arcade": 75,
}


CRED_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) \
    / "gamescrape" / "credentials"


def _cred_file_values() -> dict[str, str]:
    """Read CRED_FILE (`KEY=value` per line), or {}.

    Environment variables alone meant re-exporting four secrets in every shell,
    which invariably ends up in a ~/.bashrc — so in backups and in shell
    history. A 0600 file is safer and more convenient. The environment still
    wins (useful in CI).
    """
    try:
        if CRED_FILE.stat().st_mode & 0o077:
            print(f"gamescrape: {CRED_FILE} is readable by other accounts — "
                  f"`chmod 600 {CRED_FILE}`", file=sys.stderr)
        raw = CRED_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    vals = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        vals[k.strip()] = v.strip().strip("'\"")
    return vals


def ss_credentials() -> dict[str, str] | None:
    """ScreenScraper credentials, or None if dev access is not configured."""
    filev = _cred_file_values()

    def val(key: str) -> str:
        return (os.environ.get(key) or filev.get(key) or "").strip()

    devid, devpw = val("SCREENSCRAPER_DEV_ID"), val("SCREENSCRAPER_DEV_PASSWORD")
    if not (devid and devpw):
        return None
    creds = {"devid": devid, "devpassword": devpw, "softname": SS_SOFTNAME,
             "output": "json"}
    if user := val("SCREENSCRAPER_USER"):
        creds["ssid"] = user
        creds["sspassword"] = val("SCREENSCRAPER_PASSWORD")
    return creds


def ss_call(endpoint: str, params: dict, verbose: bool) -> dict | None:
    """API call. Returns None on failure — the caller falls back to LaunchBox."""
    url = SS_API + endpoint + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()[:120]
        # 403 credentials, 426 API closed, 429/430/431 quota reached
        if verbose or e.code in (426, 429, 430, 431):
            print(f"  screenscraper : HTTP {e.code} — {detail or e.reason}",
                  file=sys.stderr)
        return None
    except (urllib.error.URLError, OSError) as e:
        if verbose:
            print(f"  screenscraper : {e}", file=sys.stderr)
        return None
    try:
        return json.loads(body)
    except ValueError:
        if verbose:
            print(f"  screenscraper: unreadable response — {body[:120]}",
                  file=sys.stderr)
        return None


def file_hashes(path: Path, limit: int = SS_HASH_LIMIT) -> dict[str, str] | None:
    """CRC32 + MD5 + SHA1 in a single read, or None if the file is missing or
    over the limit."""
    import hashlib
    import zlib
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
    except OSError:
        return None
    crc, md5, sha1 = 0, hashlib.md5(), hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 22):
                crc = zlib.crc32(chunk, crc)
                md5.update(chunk)
                sha1.update(chunk)
    except OSError:
        return None
    return {"crc": f"{crc & 0xFFFFFFFF:08x}", "md5": md5.hexdigest(),
            "sha1": sha1.hexdigest(), "romtaille": str(path.stat().st_size)}


def ss_lookup(parsed: dict, systems: list[str], rom_path: Path | None,
              wanted: list[str] | None, every: bool,
              verbose: bool) -> tuple[dict, dict[str, list[str]]] | None:
    """(game, {type: [url…]}) via ScreenScraper, or None if unavailable."""
    creds = ss_credentials()
    if not creds:
        return None

    params = dict(creds, romtype="rom", romnom=Path(parsed["source"]).name)
    hashes = file_hashes(rom_path) if rom_path else None
    if hashes:
        params.update(hashes)                    # empreinte = certitude
        if verbose:
            print(f"  screenscraper: searching by hash "
                  f"(crc {hashes['crc']})", file=sys.stderr)
    for key in systems:
        if sid := SS_SYSTEM_IDS.get(key):
            params["systemeid"] = str(sid)
            break

    data = ss_call("jeuInfos.php", params, verbose)
    if not data:
        return None
    try:
        jeu = data["response"]["jeu"]
    except (KeyError, TypeError):
        if verbose:
            print("  screenscraper: game not in the database", file=sys.stderr)
        return None

    noms = jeu.get("noms") or []
    name = next((n.get("text") for n in noms if n.get("region") == "wor"),
                noms[0].get("text") if noms else parsed["title"])
    game = {"id": jeu.get("id"), "name": name,
            "platform": (jeu.get("systeme") or {}).get("text", ""),
            "year": ((jeu.get("dates") or [{}])[0].get("text", ""))[:4],
            "matched_by": "hash" if hashes else "filename",
            # The response carries the account quota: the downloader obeys it
            # instead of guessing a parallelism (see main()).
            "source": "screenscraper",
            "maxthreads": ((data.get("response") or {}).get("ssuser") or {}).get("maxthreads")}

    order = SS_REGION_PREF.get(parsed["region"], SS_REGION_PREF[None])
    by_type: dict[str, list[tuple[int, str]]] = {}
    for media in jeu.get("medias") or []:
        name_slug = SS_TO_SLUG.get(media.get("type", ""))
        url = media.get("url")
        if not name_slug or not url:
            continue
        if wanted and name_slug not in wanted:
            continue
        region = (media.get("region") or "ss").lower()
        rank = order.index(region) if region in order else len(order)
        by_type.setdefault(name_slug, []).append((rank, url))

    images: dict[str, list[str]] = {}
    for typ, items in by_type.items():
        items.sort(key=lambda x: x[0])
        images[typ] = [u for _, u in (items if every else items[:1])]
    return (game, images) if images else None


# ── Downloading ─────────────────────────────────────────────────────────────

_SECRET_PARAMS = ("devpassword", "sspassword", "devid", "ssid")


def redact(url: str) -> str:
    """URL without the credentials. Use it EVERYWHERE a URL is displayed.

    ScreenScraper media URLs carry devid/devpassword/ssid/sspassword in their
    query: `mediaJeu.php?devid=…&devpassword=…&ssid=…&sspassword=…`. Without
    this filter, a plain `--dry-run` pasted into a ticket or a log published the
    whole account. It happened.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    kept = [(k, "***" if k.lower() in _SECRET_PARAMS else v)
            for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(kept)))


def safe_dirname(title: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", title).strip(" .") or "game"


# Signatures of the formats we agree to write to disk.
_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"BM",
          b"RIFF", b"%PDF", b"\x00\x00\x00", b"\x1aE\xdf\xa3")  # …ftyp mp4, webm


def _looks_like_media(data: bytes) -> bool:
    if len(data) < 16:
        return False
    if data[:4] == b"RIFF" and data[8:12] not in (b"WEBP", b"AVI "):
        return False
    return any(data.startswith(m) for m in _MAGIC) or data[4:8] == b"ftyp"


# ── a picture of nothing ─────────────────────────────────────────────────────
#
# ScreenScraper does not omit a media it lacks: for `box-2D-back` it answers 200
# with a CHROMA-KEY PLATE — a perfectly valid PNG of flat #00FF00, cut to the
# exact box dimensions of the system. It downloads, it decodes, it draws, so
# `_looks_like_media` waves it through and nothing downstream can tell.
#
# Measured on the reference box: nine titles across five systems, four distinct
# files, one per box shape — 513x458 for the DS and 3DS, 421x680 for the Switch,
# 578x680 for the PS3, 395x680 for the PSP. The shelf theme catches them in the
# BROWSER (`lib/accent.js`, quantise to 24x24 and call it blank when 95 % lands
# in one bucket) and draws its printed reverse instead. That rescue works and
# stays; what it cannot do is stop the file being stored as though it were a
# scan, which is what makes every other consumer — the default theme, the API,
# the next theme somebody writes — show a green slab.
#
# **Detected by compression, not by colour, and with no image decoder.** The
# backend has no Pillow (it is in neither requirements.txt nor either venv), and
# a flat fill is the one thing PNG compresses to nothing. Measured over the 61
# backs on that box:
#
#     the nine plates      at most  0.0093 byte per pixel
#     the 52 real scans    at least 1.3055 byte per pixel
#
# A factor of 141, with nothing whatsoever in between: even the plainest real
# back carries a barcode and a paragraph of small print. The threshold below
# sits 5x above the highest plate and 26x below the lowest real scan.
#
# PNG only. The dimensions come out of the IHDR, which is at a fixed offset and
# needs no decoding; JPEG would need the SOF scanned and every plate measured
# has been a PNG. Answering False for anything else is the safe side — it keeps
# today's behaviour, which is to trust the file.
FLAT_PLATE_BPP = 0.05

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_dimensions(head: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR, or None if it is not a PNG.

    The first chunk of a PNG is always IHDR and always at offset 8, so the
    first 24 bytes are enough. Nothing is decompressed.
    """
    if len(head) < 24 or not head.startswith(_PNG_MAGIC):
        return None
    if head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return (w, h) if w and h else None


def looks_like_flat_plate(path: Path) -> bool:
    """Whether this file is a picture of nothing — see the note above."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        size = path.stat().st_size
    except OSError:
        return False
    dims = png_dimensions(head)
    if not dims:
        return False
    return size / (dims[0] * dims[1]) < FLAT_PLATE_BPP


def sniff_ext(data: bytes) -> str:
    """Extension from the CONTENT, never from the URL.

    ScreenScraper serves every media through `mediaJeu.php?…`, so the extension
    inferred from the path was ".php": the whole library filled up with
    `box-front.php` files that no frontend and no image viewer opens — and that
    GameCore, which looks for .png/.jpg, simply ignored.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data[4:8] == b"ftyp":
        return ".mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".bin"


def fetch(url: str, dest: Path, verbose: bool) -> Path | None:
    """Download to `dest`, whose extension is fixed from the content.
    Returns the path actually written, or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read()
    except (urllib.error.URLError, OSError) as e:
        if verbose:
            print(f"    · {getattr(e, 'code', e)} {redact(url)}", file=sys.stderr)
        return None
    # ScreenScraper answers 200 with a TEXT BODY when the quota is spent or the
    # media is missing ("Erreur : …"). Without this check we wrote that message
    # into a .png, and the library filled up with unreadable thumbnails no
    # frontend can display — and nothing reported it.
    if not _looks_like_media(data):
        if verbose:
            snippet = data[:80].decode("utf-8", "replace").strip()
            print(f"    · non-media response ({len(data)} B): {snippet}",
                  file=sys.stderr)
        return None
    dest = dest.with_suffix(sniff_ext(data))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Every image of a game, from its ROM filename "
                    "(sources: LaunchBox, or ScreenScraper by file hash when "
                    "developer credentials are configured).",
        epilog="Examples:\n"
               "  python3 gamescrape.py \"fifa-19-(europe)(fr,en).nds\" -o output\n"
               "  python3 gamescrape.py \"Mario Kart Wii.rvz\" -t 3d,box,logo\n"
               "  python3 gamescrape.py rom.iso -s ps2 --all -o output\n"
               "  python3 gamescrape.py \"game.nds\" --info",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("rom", nargs="?", help="ROM name or path "
                                          "(the file need not exist)")
    p.add_argument("-o", "--output", default="output", help="output directory")
    p.add_argument("-s", "--system", help="force the console: short key "
                   "(--list-systems) or exact LaunchBox platform name")
    p.add_argument("-t", "--types", default="all",
                   help="comma-separated image types: 3d, box, back, cart, "
                        "disc, logo, snap, title, fanart, banner… "
                        "(--list-types; default: all)")
    p.add_argument("--all", action="store_true",
                   help="every image of each type, not just the best one")
    p.add_argument("--cutoff", type=float, default=0.72,
                   help="minimum title similarity, 0-1 (default: 0.72)")
    p.add_argument("--flat", action="store_true",
                   help="write into -o without a per-game subdirectory")
    p.add_argument("--info", action="store_true",
                   help="parse the name and stop (no request)")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="list the images found without downloading them")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--source", choices=("auto", "screenscraper", "launchbox"),
                   default="auto",
                   help="auto: ScreenScraper when developer credentials are "
                        "configured, LaunchBox otherwise or as a fallback")
    p.add_argument("--no-hash", action="store_true",
                   help="ScreenScraper: do not hash the file "
                        "(search by name only)")
    p.add_argument("--refresh", action="store_true",
                   help="re-download and re-index the LaunchBox database")
    p.add_argument("--ss-check", action="store_true",
                   help="test the ScreenScraper credentials and show the quotas")
    p.add_argument("--ss-systems", action="store_true",
                   help="official list of ScreenScraper systemeid values")
    p.add_argument("--list-systems", action="store_true")
    p.add_argument("--list-types", action="store_true")
    p.add_argument("--ss-media-types", action="store_true",
                   help="list the media types ScreenScraper declares and "
                        "confront SS_MEDIA with what it announces")
    p.add_argument("--index-dir", metavar="DIR",
                   help="where the LaunchBox index lives. Defaults to "
                        "$GAMECORE_PATH/emu/gamescrape on a GameCore box, and "
                        "to ~/.cache/gamescrape otherwise")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    # Through the setter, not by assignment: `out()` lives in common.py now and
    # is imported by four modules. Rebinding a name here would leave every one
    # of them printing to stdout, which is what `--json | jq` cannot survive.
    set_json_mode(args.json)

    if (chosen := resolve_index_dir(args.index_dir)) is not None:
        set_index_dir(chosen)

    if args.ss_check or args.ss_systems:
        if not ss_credentials():
            out("No ScreenScraper developer credentials.\n"
                  "  export SCREENSCRAPER_DEV_ID=...\n"
                  "  export SCREENSCRAPER_DEV_PASSWORD=...\n"
                  "  export SCREENSCRAPER_USER=...        (optional, quotas)\n"
                  "  export SCREENSCRAPER_PASSWORD=...    (optional)",
                  file=sys.stderr)
            return 2
        if args.ss_check:
            data = ss_call("ssuserInfos.php", ss_credentials(), True)
            if not data:
                print("Credentials rejected by the API.", file=sys.stderr)
                return 1
            u = data.get("response", {}).get("ssuser", {})
            out(f"Account : {u.get('id')}  (level {u.get('niveau')})")
            out(f"Threads : {u.get('maxthreads')}")
            out(f"Scrapes : {u.get('requeststoday')} / "
                f"{u.get('maxrequestsperday')} today")
            return 0
        data = ss_call("systemesListe.php", ss_credentials(), True)
        if not data:
            return 1
        for s in data.get("response", {}).get("systemes", []):
            noms = s.get("noms", {})
            out(f"  {s.get('id'):>4}  {noms.get('nom_eu') or noms.get('noms_commun', '')}")
        return 0

    if args.list_systems:
        for k, v in sorted(PLATFORMS.items()):
            out(f"  {k:12} {v}")
        return 0
    if args.list_types:
        out("Shortcuts:")
        for k, v in sorted(TYPE_ALIASES.items()):
            out(f"  {k:12} → {v}")
        out(f"\nScreenScraper — {len(SS_MEDIA)} types, by category:")
        by_cat: dict[str, list[tuple[str, str, str]]] = {}
        for ss_type, (short, cat, kind) in SS_MEDIA.items():
            by_cat.setdefault(cat, []).append((short, ss_type, kind))
        for cat in sorted(by_cat):
            out(f"  [{cat}]")
            for short, ss_type, kind in sorted(by_cat[cat]):
                out(f"    {short:30} {kind:9} ← {ss_type}")
        if common.DB_PATH.exists():
            out("\nLaunchBox — types present in the local index:")
            db = sqlite3.connect(common.DB_PATH)
            rows = db.execute("SELECT DISTINCT type FROM images ORDER BY type")
            for (t,) in rows:
                cat, kind = lb_media_info(slug(t))
                out(f"    {slug(t):30} {cat:11} ← {t}")
            db.close()
        return 0
    if args.ss_media_types:
        # The reference behind SS_MEDIA. Run it to see what ScreenScraper has
        # added since — anything missing from the table still resolves through
        # ss_media_slug(), it just has no category.
        creds = ss_credentials()
        if not creds:
            out(f"Developer credentials required — see {CRED_FILE}", file=sys.stderr)
            return 1
        data = ss_call("mediasJeuListe.php", dict(creds), args.verbose)
        entries = ((data or {}).get("response") or {}).get("medias") or {}
        if not entries:
            out("mediasJeuListe.php returned nothing.", file=sys.stderr)
            return 1
        known = missing = 0
        for v in sorted(entries.values(), key=lambda x: (x.get("categorie", ""),
                                                         x.get("nomcourt", ""))):
            name = v.get("nomcourt", "")
            mapped = SS_MEDIA.get(name)
            known += bool(mapped)
            missing += not mapped
            flag = f"→ {mapped[0]}" if mapped else "→ NOT IN SS_MEDIA"
            out(f"  {name:24} {v.get('type', ''):8} {v.get('fileformat', ''):5} "
                f"{flag:34} [{v.get('categorie', '')}]")
        out(f"\n{len(entries)} declared · {known} in SS_MEDIA · {missing} missing")
        out("Note: this endpoint is not exhaustive — pictoliste, pictomonochrome,"
            " pictocouleur and background arrive in jeuInfos without being"
            " declared here.")
        return 0
    if args.refresh:
        build_index(args.verbose)
        if not args.rom:
            return 0
    if not args.rom:
        p.error("a ROM name is required "
                "(or --refresh / --list-systems / --list-types)")

    parsed = parse_rom(args.rom)
    systems = parsed["systems"]
    if args.system:
        systems = [args.system]
        parsed["systems"] = systems

    if not args.json:
        out(f"Title    : {parsed['title']}")
        out(f"Console  : {', '.join(PLATFORMS.get(s, s) for s in systems) or 'unknown'}"
              f"   (extension .{parsed['ext'] or '?'})")
        if parsed["region"] or parsed["languages"]:
            out(f"Region   : {parsed['region'] or '?'}"
                  f"   Languages: {','.join(parsed['languages']) or '-'}")
        extra = [x for x in (f"disc {parsed['disc']}" if parsed["disc"] else "",
                             f"rev {parsed['revision']}" if parsed["revision"] else "",
                             ", ".join(parsed["flags"])) if x]
        if extra:
            out(f"Other    : {'   '.join(extra)}")
    if args.info:
        if args.json:
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return 0

    wanted: list[str] | None = None
    if args.types.strip().lower() != "all":
        wanted = []
        for raw in args.types.split(","):
            key = raw.strip()
            if not key:
                continue
            wanted.append(TYPE_ALIASES.get(key.lower(), key))

    game: dict | None = None
    images: dict[str, list[str]] = {}

    # 1. ScreenScraper first when configured: on a file that exists, the hash
    #    search is certain where the name stays a guess.
    if args.source in ("auto", "screenscraper"):
        # The fallback was silent: with no developer credentials, "auto"
        # switched to LaunchBox without saying so, and you believed you were
        # scraping by hash while guessing by name. One line removes the doubt.
        if args.source == "auto" and not ss_credentials():
            out("ScreenScraper: no developer credentials → LaunchBox "
                f"(matching by name). Config: {CRED_FILE}")
        rom_path = None if args.no_hash else Path(args.rom)
        hit = ss_lookup(parsed, systems, rom_path, wanted, args.all, args.verbose)
        if hit:
            game, images = hit
            out(f"\nScreenScraper: \"{game['name']}\" — {game['platform']}"
                  f"{' (' + game['year'] + ')' if game['year'] else ''}"
                  f"   [by {game['matched_by']}, id {game['id']}]")
        elif args.source == "screenscraper":
            sys.stdout.flush()
            out("ScreenScraper returned nothing (missing credentials, quota, "
                  "or game not in the database). Use `--source launchbox` for "
                  "the other source, `--ss-check` to test the credentials.",
                  file=sys.stderr)
            return 1

    # 2. LaunchBox: the default source, and the net when ScreenScraper cannot.
    if game is None:
        try:
            db = open_db()
        except IndexUnavailable as exc:
            sys.stdout.flush()
            out(f"{exc} — run this first: gamescrape.py --refresh",
                file=sys.stderr)
            return 1
        game, score = find_game(db, parsed, systems, args.cutoff)
        if not game:
            sys.stdout.flush()
            out(f"No game found (best similarity {score:.0%}). Try "
                  f"--cutoff 0.6, -s <console>, or a title closer to the "
                  f"official one.", file=sys.stderr)
            return 1
        out(f"\nLaunchBox: \"{game['name']}\" — {game['platform']}"
              f"{' (' + game['year'] + ')' if game['year'] else ''}"
              f"   [similarity {score:.0%}, id {game['id']}]")
        _show_details(game)
        images = game_images(db, game["id"], parsed, wanted, args.all)

    if not images:
        print("This game has no image of the requested type in the database.",
              file=sys.stderr)
        return 1

    outdir = Path(args.output)
    if not args.flat:
        outdir /= safe_dirname(game["name"])

    jobs: list[tuple[str, str, Path]] = []
    for typ, urls in sorted(images.items()):
        for i, url in enumerate(urls, 1):
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png"
            suffix = f"-{i}" if len(urls) > 1 else ""
            jobs.append((typ, url, outdir / f"{typ}{suffix}{ext}"))

    if args.dry_run:
        if args.json:
            print(json.dumps({"parsed": parsed, "game": game,
                              "images": {t: [redact(u) for u in us]
                                         for t, us in images.items()}},
                             ensure_ascii=False, indent=2))
        else:
            out(f"\n{len(jobs)} image(s), nothing downloaded (--dry-run):")
            for typ, url, dest in jobs:
                out(f"  {typ:28} {redact(url)}")
        return 0

    # Parallelism is whatever the SOURCE allows, not a fixed number.
    #
    # It was a hardcoded max_workers=8. The LaunchBox CDN does not care, but
    # ScreenScraper grants a thread count per account (`maxthreads`, 1 for a
    # level-1 member) and penalises going over — 8 concurrent downloads on a
    # quota of 1 is a suspended account, not a slowdown.
    workers = 8
    if game.get("source") == "screenscraper":
        workers = max(1, int(game.get("maxthreads") or 1))
        if workers == 1 and len(jobs) > 1:
            out(f"  (ScreenScraper: {workers} thread allowed on this account "
                f"— sequential download)")

    out(f"\n{len(jobs)} image(s) → {outdir}/")
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, url, dest, args.verbose): (typ, dest)
                   for typ, url, dest in jobs}
        for fut, (typ, dest) in futures.items():
            written = fut.result()
            if written:
                results[written.name] = typ
                out(f"  ✓ {written.name:34} {written.stat().st_size // 1024} Ko")
            else:
                out(f"  ✗ {dest.stem:34} failed")

    if results:
        (outdir / "info.json").write_text(json.dumps(
            {"parsed": parsed, "game": game, "files": results},
            ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps({"parsed": parsed, "game": game, "files": results},
                         ensure_ascii=False, indent=2))
    return 0 if results else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
