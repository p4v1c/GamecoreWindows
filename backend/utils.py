"""Shared utilities used across multiple backend modules."""
import json
import os
import re
from pathlib import Path

from .config import resolve_path

TAG_RE = re.compile(r"[\(\[].*?[\)\]]")


def rom_in_root(system: dict, filename: str) -> Path | None:
    """The ROM `filename` names inside its system's ROMs directory, or None.

    `filename` arrives from a `{filename:path}` route parameter, and the :path
    converter happily accepts slashes and '..' — so it has to be confined
    before anything opens it, the same way launch_game confines rom_path
    (services/launch.py). docs/architecture/09-gotchas.md states the invariant.

    It lives here because three call sites need it — covers, metadata and
    media — and the third one is what turned two near-identical copies into a
    rule that has to be remembered rather than imported.
    """
    roms_root = resolve_path(system.get("romsPath", ""))
    if not roms_root:
        return None
    try:
        candidate = (roms_root / filename).resolve()
        candidate.relative_to(roms_root.resolve())
    except (ValueError, OSError):
        return None
    return candidate if candidate.exists() else None


def fmt_size(n: int) -> str:
    """1024-based, and the labels now say so (KiB, not KB).

    The arithmetic always divided by 1024; the labels claimed decimal units.
    A 4 GB card showing "3.7 GB" reads as missing space — with "3.7 GiB" the
    number and the unit finally agree. The rom-manager addon carries its own
    deliberate copy of this (self-contained by contract); its labels move in
    its own repository.
    """
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if x < 1024:
            return f"{x:.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024
    return f"{x:.1f} TiB"


# A system id names a directory and a file under a served root ("mgba" →
# emu/covers/mgba/, config/per-game/mgba/…). Anything outside this
# alphabet is not a system, and `..` in particular would make a resolver read
# files from anywhere the backend user can reach. One regex, imported by every
# router that takes a system id off the wire — two routers each carried their
# own identical copy, which is one edit away from two different boundaries.
SYSTEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def atomic_write(p: Path, text: str) -> None:
    """Write through a temp file in the same directory, then os.replace().

    `write_text()` truncates first and writes second. Much of what the backend
    writes is written at startup or at a launch — the exact moments someone can
    cut the power with the wall switch — and a JSON caught between truncate and
    write is an invalid file that takes every setting in it to defaults.
    os.replace() is atomic within a filesystem, so a reader sees either the
    whole old file or the whole new one. Same directory, same filesystem: that
    is what keeps the rename atomic.

    This lived in configgen/helpers/base.py and was re-grown, slightly
    differently, in pergame.py, merge.py and ota.py (and two bezel modules
    since removed). One implementation now; the callers keep saying WHY their file
    must not be torn, not how.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".gamecore-tmp")
    try:
        # utf-8 spelled out, not inherited: two of the six writers this
        # replaced (merge.py, ota.py) wrote ensure_ascii=False JSON with an
        # explicit encoding, precisely so a unit running under a non-UTF-8
        # locale cannot turn a pack label with an accent into a crash.
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_bytes(p: Path, data: bytes) -> None:
    """atomic_write for binary content (a game save): same temp-and-replace."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".gamecore-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(p: Path, data, **dumps_kwargs) -> None:
    """`atomic_write` for the JSON writers, dumps kwargs passed through
    unchanged so every caller keeps its exact on-disk shape (indent, key
    order, ascii) — a factoring that reformatted six files would show up as a
    diff in every player's config on the next write."""
    atomic_write(p, json.dumps(data, **dumps_kwargs))
