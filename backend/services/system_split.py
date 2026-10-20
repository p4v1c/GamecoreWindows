"""Move a split pack's games to one tile per system — `scripts/split-systems.py`.

`dolphin` became `gamecube` + `wii`, `mgba` became `gb` + `gbc` + `gba`. A box
installed before that keeps its old tile, which still launches (the old packs
are `supersededBy` packs). This moves its games, and everything filed under
the old system id, to the new systems — when the owner runs it, never on its
own: nothing in the update path calls this.

Every change is a rename on the same disk. Nothing is copied, nothing is
deleted, and nothing is overwritten: a file whose destination already exists
stays where it is and is reported. What cannot be told apart (a `.iso` with an
unreadable header) stays too, and so does the old tile while anything is left
in its folder. A second run finds nothing to do, or the same leftovers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..utils import atomic_write
from . import gameid, paths
from .catalog.merge import entry_from_pack, load_removed
from .gamemedia import helpers as media_helpers
from .gamemedia import paths as media_paths
from .rom_scanner import matches_ext

# GameCube and Wii discs carry a magic word at a fixed offset of their header.
_DISC_MAGIC = ((0x18, bytes.fromhex("5d1c9ea3"), "wii"),
               (0x1C, bytes.fromhex("c2339f3d"), "gamecube"))
# Where the disc header starts inside a container format.
_HEADER_AT = {b"RVZ\x01": 0x58, b"WIA\x01": 0x58, b"CISO": 0x8000}
_COVER_SUFFIXES = (".png", ".jpg", ".webp", ".miss")
# The config files this may edit, each written back in the shape its owner writes.
_CONFIG_FORMAT = {
    "controller-autoconfig.json": {"indent": 2},
}


@dataclass
class Move:
    src: Path
    dst: Path
    what: str                      # "rom", "save", "cover", "media"…


@dataclass
class Split:
    old: str                       # the superseded system id
    new: list[str]                 # its successors
    owner: str                     # the successor that owns the emulator
    games: dict[str, str] = field(default_factory=dict)   # ROM filename → new id
    moves: list[Move] = field(default_factory=list)
    kept: list[tuple[Path, str]] = field(default_factory=list)
    playtime: list[tuple[str, str]] = field(default_factory=list)  # (game_key, new id)

    @property
    def keeps_tile(self) -> bool:
        """The old tile stays while a game is left in its folder."""
        return any(why != "not a game" for _, why in self.kept)


# ── which system a dump belongs to ───────────────────────────────────────────

def disc_system(rom: Path) -> str | None:
    """'gamecube' or 'wii' from the disc header, None when it cannot be read."""
    try:
        with open(rom, "rb") as f:
            head = f.read(4)
            if head == b"WBFS":
                return "wii"
            f.seek(_HEADER_AT.get(head, 0))
            header = f.read(0x20)
    except OSError:
        return None
    for offset, magic, system in _DISC_MAGIC:
        if header[offset:offset + 4] == magic:
            return system
    return None


def _zip_member_ext(rom: Path) -> str | None:
    try:
        with zipfile.ZipFile(rom) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return None
    return Path(names[0]).suffix.lower() if len(names) == 1 else None


def classify(rom: Path, by_ext: dict[str, str], new: list[str]) -> str | None:
    """The successor a ROM belongs to: its extension when only one system
    claims it, else what is inside the zip, else the disc header."""
    ext = rom.suffix.lower()
    if ext == ".zip":
        ext = _zip_member_ext(rom) or ext
    if ext in by_ext:
        return by_ext[ext]
    system = disc_system(rom)
    return system if system in new else None


def _unique_extensions(packs: dict, new: list[str]) -> dict[str, str]:
    claims: dict[str, list[str]] = {}
    for sid in new:
        for pattern in packs[sid].data["roms"]["extensions"]:
            claims.setdefault(pattern.lstrip("*").lower(), []).append(sid)
    return {ext: sids[0] for ext, sids in claims.items() if len(sids) == 1}


# ── the plan: reads only ─────────────────────────────────────────────────────

def _roms_dir(pack) -> Path:
    return paths.resolve_data_path(pack.data["roms"]["dir"])


def _game_files(old: str, new: str, rom: Path, pack) -> list[Move]:
    """Everything filed under the old id for this one game."""
    name, stem = rom.name, Path(rom.name).stem
    moves = []
    for suffix in _COVER_SUFFIXES:            # the key cover_pipeline.resolve() builds
        src = (paths.covers_dir() / old / stem).with_suffix(suffix)
        moves.append(Move(src, (paths.covers_dir() / new / stem).with_suffix(suffix), "cover"))
    moves.append(Move(paths.metadata_dir() / old / f"{stem}.json",
                      paths.metadata_dir() / new / f"{stem}.json", "metadata"))
    moves.append(Move(media_helpers.entry_dir(old, name),
                      media_helpers.entry_dir(new, name), "media"))
    strategy = (pack.data.get("perGame") or {}).get("key")
    if strategy and (gid := gameid.identify(strategy, rom)):
        moves.append(Move(paths.pergame_dir() / old / f"{gid}.json",
                          paths.pergame_dir() / new / f"{gid}.json", "per-game"))
    return [m for m in moves if m.src.exists()]


def _plan_folder(split: Split, tile: dict, packs: dict) -> None:
    src_dir = paths.resolve_data_path(tile.get("romsPath", ""))
    if not src_dir or not src_dir.is_dir():
        return
    pack = packs[split.old]
    exts = list(tile.get("extensions") or []) + pack.data["roms"]["extensions"]
    by_ext = _unique_extensions(packs, split.new)
    entries = sorted(p for p in src_dir.iterdir() if not p.name.startswith("."))
    roms = [p for p in entries if p.is_file() and matches_ext(p.name, exts)]
    claimed = set(roms)
    for rom in roms:
        target = classify(rom, by_ext, split.new)
        dst = _roms_dir(packs[target]) / rom.name if target else None
        companions = [p for p in entries if p not in claimed and p.is_file()
                      and Path(p.name).stem == Path(rom.name).stem]
        claimed.update(companions)
        why = ("cannot tell which system it is" if not target
               else "already exists in " + str(dst.parent) if dst.exists()
               else "on another disk" if not _same_disk(rom, dst.parent) else None)
        if why:
            split.kept += [(rom, why)] + [(c, "stays with its game") for c in companions]
            continue
        split.games[rom.name] = target
        split.moves.append(Move(rom, dst, "rom"))
        split.moves += [Move(c, dst.parent / c.name, "save" if c.suffix.lower() == ".sav"
                             else "companion") for c in companions]
        split.moves += _game_files(split.old, target, rom, pack)
    split.kept += [(p, "not a game") for p in entries if p not in claimed]
    # Two dumps can share a cover file (the cache keys on a truncated stem):
    # the first game to claim it takes it.
    seen: set[Path] = set()
    split.moves = [m for m in split.moves if not (m.src in seen or seen.add(m.src))]


def _same_disk(src: Path, dst_dir: Path) -> bool:
    probe = dst_dir
    while not probe.exists():
        probe = probe.parent
    return os.stat(src).st_dev == os.stat(probe).st_dev


def _plan_playtime(split: Split, by_ext: dict[str, str]) -> None:
    db = paths.config_dir() / "playtime.db"
    if not db.is_file():
        return
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        keys = [k for (k,) in con.execute(
            "SELECT game_key FROM playtime WHERE system_id = ?", (split.old,))]
    for key in keys:
        if key == f"{split.old}:settings":   # the emulator's own window, opened from the tile
            split.playtime.append((key, split.owner))
        elif target := split.games.get(key) or by_ext.get(Path(key).suffix.lower()):
            split.playtime.append((key, target))


def plan(packs: dict) -> list[Split]:
    """What would move, for every superseded tile on this box's grid. Writes nothing."""
    media_paths.set_cache_root(paths.media_cache_dir())
    grid = json.loads((paths.config_dir() / "systems.json").read_text(encoding="utf-8"))
    splits = []
    for tile in grid:
        pack = packs.get(tile.get("id"))
        if not pack or not pack.superseded_by:
            continue
        new = [s for s in pack.superseded_by if s in packs]
        split = Split(tile["id"], new, pack.emulator_owner)
        _plan_folder(split, tile, packs)
        _plan_playtime(split, _unique_extensions(packs, new))
        splits.append(split)
    return splits


# ── the config files: pure, so a dry run can print them ──────────────────────

def _read_json(name: str) -> dict:
    try:
        data = json.loads((paths.config_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def edit_configs(splits: list[Split], packs: dict) -> tuple[dict[str, dict], list[str]]:
    """{file name: new content} for the files that change, and what changes."""
    files = {name: _read_json(name) for name in _CONFIG_FORMAT}
    notes: list[str] = []
    autoconfig = files["controller-autoconfig.json"]
    for s in splits:
        switches = autoconfig.get("packs") or {}
        if s.old in switches and s.owner not in switches:
            switches[s.owner] = switches.pop(s.old)
            notes.append(f"controller-autoconfig.json: {s.old} -> {s.owner}")
    changed = {n: d for n, d in files.items() if d != _read_json(n)}
    return changed, notes


def edit_grid(splits: list[Split], packs: dict) -> tuple[list[dict], list[str]]:
    """systems.json with each old tile replaced, in place, by its successors."""
    grid = json.loads((paths.config_dir() / "systems.json").read_text(encoding="utf-8"))
    removed = load_removed(paths.GAMECORE_DATA)
    have = {t.get("id") for t in grid}
    by_old = {s.old: s for s in splits}
    out, notes = [], []
    for tile in grid:
        s = by_old.get(tile.get("id"))
        if s is None:
            out.append(tile)
            continue
        # The order a fresh install lists them in (gen-catalog.py).
        for sid in sorted(s.new, key=lambda i: (packs[i].data.get("order", 10_000), i)):
            if sid in have or sid in removed:
                continue
            out.append(entry_from_pack(packs[sid], paths.GAMECORE_ROOT))
            notes.append(f"systems.json: {sid} tile added")
        if s.keeps_tile:
            out.append(tile)
            notes.append(f"systems.json: {s.old} tile kept — games are left in its folder")
        else:
            notes.append(f"systems.json: {s.old} tile removed")
    return out, notes


# ── apply ────────────────────────────────────────────────────────────────────

def _move(m: Move) -> str | None:
    """Rename, never overwrite. Returns why it did not happen."""
    if not m.src.exists():
        return "gone"
    if m.dst.exists():
        return "destination exists"
    m.dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(m.src, m.dst)
    except OSError as e:
        return str(e)
    return None


def _apply_playtime(s: Split, log: list[str]) -> None:
    db = paths.config_dir() / "playtime.db"
    if not s.playtime or not db.is_file():
        return
    with sqlite3.connect(db) as con:
        for key, sid in s.playtime:
            new_key = f"{sid}:settings" if key == f"{s.old}:settings" else key
            try:
                con.execute("UPDATE playtime SET system_id = ?, game_key = ? "
                            "WHERE system_id = ? AND game_key = ?", (sid, new_key, s.old, key))
            except sqlite3.IntegrityError:
                log.append(f"KEPT playtime {s.old}/{key}: {sid} already has a row")
                continue
            con.execute("UPDATE sessions SET system_id = ?, game_key = ? "
                        "WHERE system_id = ? AND game_key = ?", (sid, new_key, s.old, key))
            log.append(f"MOVED playtime {s.old}/{key} -> {sid}/{new_key}")


def apply(splits: list[Split], packs: dict) -> list[str]:
    """Carry the plan out. Returns one line per thing done or left."""
    if not splits:
        return []                        # a box already split: touch nothing
    log: list[str] = []
    for s in splits:
        failed = set()
        for m in s.moves:
            why = _move(m)
            log.append(f"KEPT {m.what} {m.src}: {why}" if why else f"MOVED {m.what} {m.src} -> {m.dst}")
            if why and m.what == "rom":
                # Its hours and settings stay with it, and so does the old tile.
                failed.add(m.src.name)
                s.games.pop(m.src.name, None)
                s.kept.append((m.src, why))
        s.playtime = [(k, sid) for k, sid in s.playtime if k not in failed]
        _apply_playtime(s, log)
    changed, notes = edit_configs(splits, packs)
    for name, data in changed.items():
        atomic_write(paths.config_dir() / name, json.dumps(data, **_CONFIG_FORMAT[name]) + "\n")
    grid, grid_notes = edit_grid(splits, packs)
    # merge.merge_file's shape: an OTA after this must not reformat the file.
    atomic_write(paths.config_dir() / "systems.json",
                 json.dumps(grid, indent=2, ensure_ascii=False) + "\n")
    return log + notes + grid_notes
