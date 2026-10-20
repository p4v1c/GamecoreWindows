"""scripts/split-systems.py: an unmigrated box's games, moved to one tile per system.

Built on a fake box laid out like the reference one (2026-09-27): Dolphin
dumps of both consoles in one folder, mGBA saves next to their ROMs, covers
and hours filed under the old ids. The rules under test are the owner's: a
dry run writes nothing, `--apply` only renames, never overwrites, and what it
cannot place stays where it is with the old tile.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import paths, system_split                    # noqa: E402
from backend.services.catalog import load_catalog                    # noqa: E402

WII_MAGIC = (0x18, bytes.fromhex("5d1c9ea3"))
GC_MAGIC = (0x1C, bytes.fromhex("c2339f3d"))


def _disc(path: Path, game_id: str, magic, container: bytes = b"") -> None:
    header = bytearray(0x40)
    header[:6] = game_id.encode()
    header[magic[0]:magic[0] + 4] = magic[1]
    pad = bytes(0x58 - len(container)) if container else b""
    path.write_bytes(container + pad + bytes(header))


def _tile(sid, extensions, **kw):
    return {"id": sid, "type": "emulator", "label": sid, "platform": sid,
            "color": "#000000", "path": "flatpak", "args": "run @APPID@",
            "romsPath": f"emu/{sid}/", "extensions": extensions, **kw}


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "userdata"
    emu, cfg = data / "emu", data / "config"
    (emu / "dolphin").mkdir(parents=True)
    (emu / "mgba").mkdir()
    cfg.mkdir()
    _disc(emu / "dolphin" / "Wind Waker.rvz", "GZLP01", GC_MAGIC, b"RVZ\x01")
    _disc(emu / "dolphin" / "Mario Kart Wii.rvz", "RMCP01", WII_MAGIC, b"RVZ\x01")
    _disc(emu / "dolphin" / "Melee.iso", "GALE01", GC_MAGIC)
    (emu / "dolphin" / "Homebrew.wbfs").write_bytes(b"WBFS" + bytes(60))
    (emu / "dolphin" / ".gitkeep").write_text("")
    for name in ("Red.gb", "Red.sav", "Crystal.gbc", "Emerald.gba", "Emerald.sav"):
        (emu / "mgba" / name).write_bytes(name.encode())
    with zipfile.ZipFile(emu / "mgba" / "Tetris.zip", "w") as z:
        z.writestr("Tetris.gb", b"x")
    (emu / "covers" / "dolphin").mkdir(parents=True)
    (emu / "covers" / "dolphin" / "Wind Waker.webp").write_bytes(b"cover")
    (emu / "metadata" / "mgba").mkdir(parents=True)
    (emu / "metadata" / "mgba" / "Emerald.json").write_text("{}")
    (cfg / "per-game" / "dolphin").mkdir(parents=True)
    (cfg / "per-game" / "dolphin" / "RMCP01.json").write_text("{}")
    (cfg / "systems.json").write_text(json.dumps([
        _tile("azahar", ["*.3ds"]),
        _tile("dolphin", ["*.iso", "*.rvz", "*.wbfs"]),
        _tile("mgba", ["*.gb", "*.gbc", "*.gba", "*.zip"]),
    ], indent=2))
    (cfg / "controller-autoconfig.json").write_text(json.dumps(
        {"enabled": True, "packs": {"dolphin": False}}))
    with sqlite3.connect(cfg / "playtime.db") as con:
        con.executescript("""
            CREATE TABLE playtime (game_key TEXT NOT NULL, system_id TEXT NOT NULL,
                total_secs INTEGER NOT NULL DEFAULT 0, session_count INTEGER NOT NULL DEFAULT 0,
                last_played TEXT, PRIMARY KEY (system_id, game_key));
            CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, game_key TEXT NOT NULL,
                system_id TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, duration INTEGER);
        """)
        con.executemany("INSERT INTO playtime VALUES (?, ?, ?, 1, NULL)", [
            ("Mario Kart Wii.rvz", "dolphin", 13795), ("dolphin:settings", "dolphin", 7),
            ("Red.gb", "mgba", 4044), ("Gone.gba", "mgba", 60)])
        con.execute("INSERT INTO sessions (game_key, system_id, started_at) "
                    "VALUES ('Red.gb', 'mgba', 'x')")

    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    paths.use_roots(ROOT, data)
    try:
        yield data
    finally:
        paths.use_roots(*before)


@pytest.fixture(scope="module")
def packs():
    return load_catalog(ROOT / "catalog", ROOT / "config" / "catalog.d")


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _ids(data: Path) -> list[str]:
    return [t["id"] for t in json.loads((data / "config" / "systems.json").read_text())]


# ── the dry run ─────────────────────────────────────────────────────────────

def test_the_plan_writes_nothing(box, packs):
    before = _fingerprint(box)
    splits = system_split.plan(packs)
    system_split.edit_configs(splits, packs)
    system_split.edit_grid(splits, packs)
    assert _fingerprint(box) == before


def test_every_save_is_listed_with_its_destination(box, packs):
    moves = {(m.src.name, m.dst.parent.name) for s in system_split.plan(packs)
             for m in s.moves if m.what == "save"}
    assert moves == {("Red.sav", "gb"), ("Emerald.sav", "gba")}


def test_each_dump_goes_to_its_real_system(box, packs):
    games = {n: sid for s in system_split.plan(packs) for n, sid in s.games.items()}
    assert games == {"Wind Waker.rvz": "gamecube", "Melee.iso": "gamecube",
                     "Mario Kart Wii.rvz": "wii", "Homebrew.wbfs": "wii",
                     "Red.gb": "gb", "Crystal.gbc": "gbc", "Emerald.gba": "gba",
                     "Tetris.zip": "gb"}


# ── apply ───────────────────────────────────────────────────────────────────

def test_apply_moves_the_games_and_what_is_filed_under_them(box, packs):
    system_split.apply(system_split.plan(packs), packs)
    emu = box / "emu"
    assert (emu / "wii" / "Mario Kart Wii.rvz").is_file()
    assert (emu / "gb" / "Red.sav").read_bytes() == b"Red.sav"      # the save, byte for byte
    assert (emu / "gba" / "Emerald.sav").is_file()
    assert (emu / "covers" / "gamecube" / "Wind Waker.webp").is_file()
    assert (emu / "metadata" / "gba" / "Emerald.json").is_file()
    assert (box / "config" / "per-game" / "wii" / "RMCP01.json").is_file()
    assert [p.name for p in (emu / "dolphin").iterdir()] == [".gitkeep"]


def test_apply_replaces_each_old_tile_in_place(box, packs):
    system_split.apply(system_split.plan(packs), packs)
    assert _ids(box) == ["azahar", "gamecube", "wii", "gb", "gba", "gbc"]


def test_apply_carries_the_hours_and_the_switches(box, packs):
    system_split.apply(system_split.plan(packs), packs)
    with sqlite3.connect(box / "config" / "playtime.db") as con:
        rows = set(con.execute("SELECT system_id, game_key, total_secs FROM playtime"))
        sessions = set(con.execute("SELECT system_id, game_key FROM sessions"))
    assert rows == {("wii", "Mario Kart Wii.rvz", 13795), ("gamecube", "gamecube:settings", 7),
                    ("gb", "Red.gb", 4044), ("gba", "Gone.gba", 60)}
    assert sessions == {("gb", "Red.gb")}
    cfg = box / "config"
    assert json.loads((cfg / "controller-autoconfig.json").read_text())["packs"] == \
        {"gamecube": False}


def test_a_second_run_changes_nothing(box, packs):
    system_split.apply(system_split.plan(packs), packs)
    after = _fingerprint(box)
    assert system_split.plan(packs) == []
    stamp = (box / "config" / "systems.json").stat().st_mtime_ns
    assert system_split.apply([], packs) == []
    assert (box / "config" / "systems.json").stat().st_mtime_ns == stamp
    assert _fingerprint(box) == after


# ── what stays ──────────────────────────────────────────────────────────────

def test_a_dump_it_cannot_read_stays_and_keeps_the_old_tile(box, packs):
    (box / "emu" / "dolphin" / "Unknown.iso").write_bytes(bytes(64))
    (box / "emu" / "dolphin" / "Unknown.sav").write_bytes(b"s")
    system_split.apply(system_split.plan(packs), packs)
    assert (box / "emu" / "dolphin" / "Unknown.iso").is_file()
    assert (box / "emu" / "dolphin" / "Unknown.sav").is_file()
    assert _ids(box) == ["azahar", "gamecube", "wii", "dolphin", "gb", "gba", "gbc"]
    # And a re-run proposes only the leftover, again.
    [split] = [s for s in system_split.plan(packs) if s.old == "dolphin"]
    assert split.moves == [] and split.keeps_tile


def test_an_existing_destination_is_never_overwritten(box, packs):
    (box / "emu" / "gb").mkdir(parents=True)
    (box / "emu" / "gb" / "Red.gb").write_bytes(b"theirs")
    system_split.apply(system_split.plan(packs), packs)
    assert (box / "emu" / "gb" / "Red.gb").read_bytes() == b"theirs"
    assert (box / "emu" / "mgba" / "Red.gb").read_bytes() == b"Red.gb"
    assert (box / "emu" / "mgba" / "Red.sav").is_file(), "a save follows its game or stays with it"
    assert "mgba" in _ids(box)


# ── the command itself ──────────────────────────────────────────────────────

@pytest.mark.linux_until_port(10)
def test_the_command_dry_runs_by_default(box):
    before = _fingerprint(box)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GAMECORE_")}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "split-systems.py"),
                        "--data", str(box)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Red.sav" in r.stdout and "Dry run" in r.stdout
    assert _fingerprint(box) == before


# ── ryujinx → switch: a change of emulator, not of system ───────────────────

@pytest.fixture
def switch_box(tmp_path):
    data = tmp_path / "userdata"
    (data / "emu" / "ryujinx").mkdir(parents=True)
    (data / "config").mkdir()
    for name in ("Zelda.nsp", "Mario Kart.xci", "Old dump.zip"):
        (data / "emu" / "ryujinx" / name).write_bytes(name.encode())
    (data / "config" / "systems.json").write_text(json.dumps(
        [_tile("ryujinx", ["*.xci", "*.nsp", "*.zip"])]))
    (data / "config" / "controller-autoconfig.json").write_text(json.dumps(
        {"enabled": True, "packs": {"ryujinx": False}}))
    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    paths.use_roots(ROOT, data)
    try:
        yield data
    finally:
        paths.use_roots(*before)


def test_switch_games_move_to_the_switch_tile_and_a_zip_keeps_the_ryujinx_tile(switch_box, packs):
    system_split.apply(system_split.plan(packs), packs)
    emu = switch_box / "emu"
    assert sorted(p.name for p in (emu / "switch").iterdir()) == ["Mario Kart.xci", "Zelda.nsp"]
    assert [p.name for p in (emu / "ryujinx").iterdir()] == ["Old dump.zip"]
    assert _ids(switch_box) == ["switch", "ryujinx"]
    # The switch pack owns Ryujinx now: the owner's choice for it follows.
    autoconfig = json.loads((switch_box / "config" / "controller-autoconfig.json").read_text())
    assert autoconfig["packs"] == {"switch": False}
