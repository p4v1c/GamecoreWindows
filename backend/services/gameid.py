"""Which game this is — the one name everything downstream files it under.

A per-game config is a file named after a game, so before anything can be
written there has to be an answer to "which game is this". That answer differs
per system in the only way that matters — where it is READ FROM — and not at
all in what it is for:

    ps3, ps4, psp   a Title ID in PARAM.SFO
    gcwii           the 6 characters at the top of a disc image
    playstation     the serial in SYSTEM.CNF
    wiiu            the title id in the dump's own meta.xml
    hash            the CRC32 of the file itself
    filename        the normalised name, and nothing else was available

The bottom two rows are why this is a registry and not a chain of `if`s. A
cartridge dump carries NO identifier: an N64, SNES or Mega Drive ROM is the
game and nothing more, so the only identity it has is the bytes. Any design
that assumed "read the id out of the dump" would work for the thirteen systems
here and then be rewritten the first time somebody added a retro console —
which is exactly the direction this catalogue keeps going.

Where the readers come from, and why they are not re-typed here
---------------------------------------------------------------
`local_media.py` already opens all five container layouts, because covers and
titles needed them first. Those parsers are the source of truth and this module
CALLS them; it does not carry a second copy of the PARAM.SFO offsets or the
WIA header layout. A ROM that scrapes as one game and configures as another is
worse than either answer being wrong on its own, and two copies of a parser is
how that happens.

What it does NOT do is go through `local_media.disc_id()`. That function
answers a different question — "what should the scraper look this up by" — and
gives PS4 and PSP no answer at all, deliberately. Reaching past it to the
per-format readers keeps this module from changing what the scraper sees.

`wiiu` is the one reader written here rather than there, and the reason is the
same rule read the other way: `localMedia` means icons and titles and online
lookups, none of which this adds for the Wii U. A format in that enum with two
of its three readers missing would be a promise the file does not keep.

One implementation
------------------
The per-game configs and the system split (`system_split.py`) both ask here,
so a game cannot be one thing to its settings and another to its move.
"""
from __future__ import annotations

import logging
import re
import zlib
from collections.abc import Callable
from pathlib import Path

from . import local_media
from .gamemedia.parser import normalize, parse_rom

log = logging.getLogger(__name__)

# CRC32 reads the whole file, and the whole file is the point: a partial digest
# of a ROM is not a weaker identity, it is a different game's identity waiting
# to happen — cartridge dumps of the same series share long identical stretches
# of header and engine.
#
# So the protection is a refusal rather than a shortcut. `hash` is declared by
# cartridge systems, where the largest dumps that exist are a few hundred
# megabytes; a file past this cap is a disc image whose pack picked the wrong
# strategy, and hashing it would stall a launch for tens of seconds behind a
# black screen with nothing on it.
_MAX_HASH_BYTES = 512 * 1024 * 1024
_HASH_CHUNK = 1024 * 1024

# (path, size, mtime_ns) → digest. In memory only, and keyed on the stat so a
# ROM replaced in place is re-read: the alternative is a box that keeps
# applying one game's settings to another after the owner swapped the file.
_hash_memo: dict[tuple[str, int, int], str] = {}

_TITLE_ID_RE = re.compile(r"[0-9A-Za-z]{9,16}")
# <title_id type="hexBinary" length="8">0005000010143500</title_id>
_WIIU_TITLE_ID_RE = re.compile(
    rb"<title_id[^>]*>\s*([0-9A-Fa-f]{16})\s*</title_id>")
# A meta.xml is a few kilobytes. The cap is the same lesson sfo.py already
# learned the expensive way — this reads a file out of a game dump.
_MAX_META_BYTES = 256 * 1024


# ── the readers ──────────────────────────────────────────────────────────────

def _sfo_field(format_name: str, field: str) -> Callable[[Path], str | None]:
    """A PARAM.SFO field, read by the layout reader `local_media` already has.

    PS3 and PS4 file a game under TITLE_ID; PSP uses DISC_ID. One function
    because the difference between them is a dictionary key, and three copies
    of `parse(...).get(...)` would be three places for the cap and the error
    handling to drift apart.
    """
    def read(rom: Path) -> str | None:
        reader = local_media.format_reader(format_name, "title")
        if reader is None:
            return None
        value = str((reader(rom) or {}).get(field, "")).strip()
        return value if _TITLE_ID_RE.fullmatch(value) else None
    return read


def _from_disc_reader(format_name: str) -> Callable[[Path], str | None]:
    """The id a container carries in its header, straight from `local_media`."""
    def read(rom: Path) -> str | None:
        reader = local_media.format_reader(format_name, "disc")
        return reader(rom) if reader else None
    return read


def _wiiu_title_id(rom: Path) -> str | None:
    """The 16 hex digits a Wii U dump states about itself.

    Only the extracted layout — `code/`, `content/`, `meta/meta.xml` — which is
    what a Cemu library is actually made of. A `.wux` or `.wud` is an encrypted
    disc image, and reading a title id out of one means holding the keys and
    decrypting: possible, but it would put key handling on the launch path to
    name a config file, and a title nobody can identify simply gets no per-game
    settings, which is the state it is in today.

    Lowercase, because that is how Cemu spells the file it looks for. Filing
    the settings under the same digits in the other case is a file the emulator
    walks straight past, and nothing anywhere reports a miss.
    """
    candidates = []
    if rom.is_dir():
        candidates += [rom / "meta" / "meta.xml", rom / "meta.xml"]
    else:
        # `.../<game>/code/foo.rpx` — the ROM entry a scan hands over is the
        # executable, and the metadata is its grandparent's business.
        candidates += [rom.parent / "meta" / "meta.xml",
                       rom.parent.parent / "meta" / "meta.xml"]
    for meta in candidates:
        try:
            if not meta.is_file() or meta.stat().st_size > _MAX_META_BYTES:
                continue
            m = _WIIU_TITLE_ID_RE.search(meta.read_bytes())
        except OSError:
            continue
        if m:
            return m.group(1).decode("ascii").lower()
    return None


def _crc32(rom: Path) -> str | None:
    """The file's own CRC32, for a dump that carries no identifier at all."""
    try:
        st = rom.stat()
    except OSError:
        return None
    if not rom.is_file():
        return None
    if st.st_size > _MAX_HASH_BYTES:
        log.warning("gameid: %s is %d bytes — too large to be a cartridge dump, "
                    "not hashing", rom, st.st_size)
        return None
    memo_key = (str(rom), st.st_size, st.st_mtime_ns)
    if memo_key in _hash_memo:
        return _hash_memo[memo_key]
    crc = 0
    try:
        with open(rom, "rb") as f:
            while chunk := f.read(_HASH_CHUNK):
                crc = zlib.crc32(chunk, crc)
    except OSError:
        return None
    digest = f"{crc & 0xFFFFFFFF:08X}"
    _hash_memo[memo_key] = digest
    return digest


def from_filename(name: str) -> str:
    """The identity of a game that has told us nothing but its file name.

    `parse_rom` + `normalize` from the scraper's own parser, and deliberately
    not a second regex: the region table, the language table, `TAG_RE` and the
    articles are one vocabulary that has already been argued about once. The
    result is `[a-z0-9]` only, so it is safe as a file name by construction and
    not by a second pass nobody would remember to keep.

    Returns "" for a name that normalises away to nothing. The callers treat
    that as "no identity", which is right: a per-game file called `.ini` would
    collect every unnameable game on the system into one bucket.
    """
    return normalize(parse_rom(name)["title"])


def _from_name(rom: Path) -> str | None:
    return from_filename(rom.name) or None


# strategy → reader. `perGame.key` in the pack schema is an enum over exactly
# these names, and `test_pergame_contract.py` fails the build when the two
# drift: a key the registry does not implement is a per-game file that is never
# written, and never says why.
READERS: dict[str, Callable[[Path], str | None]] = {
    "ps3":         _sfo_field("ps3", "TITLE_ID"),
    "ps4":         _sfo_field("ps4", "TITLE_ID"),
    "psp":         _sfo_field("psp", "DISC_ID"),
    "gcwii":       _from_disc_reader("gcwii"),
    "playstation": _from_disc_reader("playstation"),
    "wiiu":        _wiiu_title_id,
    "hash":        _crc32,
    "filename":    _from_name,
}


def identify(strategy: str, rom: Path) -> str | None:
    """Name this game under `strategy`, or None if it cannot be named.

    Never raises. A launch that cannot identify its game must still be a launch
    — the per-game settings are an improvement on a working system, not a
    precondition of it — so every failure here is a log line and an absence.

    An UNKNOWN strategy is logged as a warning rather than passed over, because
    the only way to reach one is a pack from a newer release than this backend:
    mid-OTA, or a local pack written against a later schema. It degrades to no
    per-game config, which is survivable, but "the settings stopped arriving"
    has to be attributable to something.
    """
    reader = READERS.get(strategy)
    if reader is None:
        log.warning("gameid: no reader for strategy %r — this backend is older "
                    "than the pack that declared it", strategy)
        return None
    try:
        value = reader(Path(rom))
    except Exception:
        log.exception("gameid: %s reader failed on %s", strategy, rom)
        return None
    return value or None
