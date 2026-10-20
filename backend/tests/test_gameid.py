"""Naming a game, from every container the catalogue declares.

The fixtures below are BUILT, byte by byte, rather than checked in: a real
PARAM.SFO or disc image would be a game dump in the repository, and a recorded
blob nobody can read is a fixture that stops meaning anything the first time it
needs changing. Every one of them is the smallest thing the real parser accepts,
constructed by the same struct layout the parser reads.

The two rows that matter most here are the ones with no fixture to build.
`hash` and `filename` exist because a cartridge dump carries NO identifier —
an N64 or Mega Drive ROM is the game and nothing else — and a design that
assumed "read the id out of the container" would work for today's thirteen
systems and be rewritten by the first retro console anybody adds.
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import gameid  # noqa: E402
from backend.services.catalog import load_schema  # noqa: E402

CATALOG = ROOT / "catalog"


# ── the registry is the schema's enum ────────────────────────────────────────

def test_every_declarable_key_has_a_reader():
    """`perGame.key` and `gameid.READERS` are one list written twice.

    A pack may only declare a key the schema allows; the schema allows exactly
    what this module implements. Let the two drift and the failure is silent in
    the worst direction: `check-catalog` passes, the pack looks configured, and
    `identify()` returns None on every launch — a per-game file that is never
    written and never explains itself.
    """
    schema = load_schema(CATALOG / "_schema" / "pack.schema.json")
    declarable = set(schema["properties"]["perGame"]["properties"]["key"]["enum"])
    implemented = set(gameid.READERS)
    assert declarable == implemented, (
        f"perGame.key allows {sorted(declarable - implemented)} that gameid does "
        f"not implement, and gameid implements {sorted(implemented - declarable)} "
        f"that no pack can ask for")


@pytest.mark.parametrize("pack_dir", sorted(p for p in CATALOG.iterdir()
                                            if (p / "pack.json").is_file()),
                         ids=lambda p: p.name)
def test_no_pack_declares_a_key_nobody_implements(pack_dir):
    meta = json.loads((pack_dir / "pack.json").read_text())
    key = (meta.get("perGame") or {}).get("key")
    assert key is None or key in gameid.READERS, (
        f"{pack_dir.name} declares perGame.key {key!r}, which gameid has no "
        f"reader for — every launch would silently write nothing")


# ── the containers ───────────────────────────────────────────────────────────

def _param_sfo(fields: dict[str, str]) -> bytes:
    """A PARAM.SFO the real reader accepts, laid out by its own struct format.

    Written here rather than recorded so the test states the layout it depends
    on. A recorded blob would pass just as well and teach nobody what a change
    to `sfo.py` broke.
    """
    keys, data = b"", b""
    entries = b""
    for name, value in fields.items():
        raw = value.encode() + b"\x00"
        entries += struct.pack("<HHIII", len(keys), 0x0204, len(raw), len(raw), len(data))
        keys += name.encode() + b"\x00"
        data += raw
    header_len = 20 + 16 * len(fields)
    key_table = header_len
    data_table = key_table + len(keys)
    return (struct.pack("<4sIIII", b"\x00PSF", 0x0101, key_table, data_table,
                        len(fields)) + entries + keys + data)


def test_a_ps3_folder_is_named_by_its_title_id(tmp_path):
    game = tmp_path / "Demons Souls"
    (game / "PS3_GAME").mkdir(parents=True)
    (game / "PS3_GAME" / "PARAM.SFO").write_bytes(
        _param_sfo({"TITLE": "Demon's Souls", "TITLE_ID": "BLES00932"}))
    assert gameid.identify("ps3", game) == "BLES00932"


def test_a_ps4_folder_is_named_by_its_title_id(tmp_path):
    """PS4 has no `disc` reader in local_media — deliberately, the scraper does
    not look games up that way. Reaching the title reader directly is what lets
    the per-game side have an identity without changing what the scraper sees."""
    game = tmp_path / "Bloodborne"
    (game / "sce_sys").mkdir(parents=True)
    (game / "sce_sys" / "param.sfo").write_bytes(
        _param_sfo({"TITLE": "Bloodborne", "TITLE_ID": "CUSA00207"}))
    assert gameid.identify("ps4", game) == "CUSA00207"


def test_a_gamecube_image_is_named_by_its_header(tmp_path):
    iso = tmp_path / "Melee.iso"
    iso.write_bytes(b"GALE01" + b"\x00" * 1024)
    assert gameid.identify("gcwii", iso) == "GALE01"


def test_a_wii_u_dump_is_named_by_its_own_meta_xml(tmp_path):
    game = tmp_path / "Breath of the Wild"
    (game / "meta").mkdir(parents=True)
    (game / "meta" / "meta.xml").write_text(
        '<?xml version="1.0"?><menu>'
        '<title_id type="hexBinary" length="8">00050000101C9500</title_id>'
        '</menu>')
    assert gameid.identify("wiiu", game) == "00050000101c9500"


def test_the_wii_u_id_is_lowercased_because_that_is_what_cemu_opens(tmp_path):
    """Cemu formats the file name with `{:016x}`. The same digits in the other
    case are a file it walks straight past, and nothing anywhere reports a
    miss: the settings simply never arrive, on a box, forever."""
    game = tmp_path / "game"
    (game / "meta").mkdir(parents=True)
    (game / "meta" / "meta.xml").write_text(
        "<menu><title_id>000500001019E600</title_id></menu>")
    assert gameid.identify("wiiu", game) == "000500001019e600"


def test_a_wii_u_rom_file_finds_the_meta_beside_its_code_directory(tmp_path):
    """A Cemu library is scanned down to the `.rpx`, so the entry that reaches
    here is the executable and the metadata is its grandparent's business."""
    game = tmp_path / "Splatoon"
    (game / "code").mkdir(parents=True)
    (game / "meta").mkdir()
    (game / "meta" / "meta.xml").write_text(
        "<menu><title_id>0005000010176900</title_id></menu>")
    rpx = game / "code" / "Splatoon.rpx"
    rpx.write_bytes(b"\x7fELF")
    assert gameid.identify("wiiu", rpx) == "0005000010176900"


def test_an_encrypted_wii_u_image_is_simply_not_named(tmp_path):
    """A `.wux` needs the keys to read anything out of. A title nobody can name
    gets no per-game settings, which is exactly the state it is in today —
    reading it would put key handling on the launch path to pick a filename."""
    wux = tmp_path / "Game.wux"
    wux.write_bytes(b"WUX0" + b"\x00" * 256)
    assert gameid.identify("wiiu", wux) is None


# ── the containers that carry nothing ────────────────────────────────────────

def test_a_cartridge_is_named_by_its_own_bytes(tmp_path):
    rom = tmp_path / "Super Mario 64 (USA).z64"
    rom.write_bytes(b"\x80\x37\x12\x40" + b"cartridge data" * 64)
    expected = f"{zlib.crc32(rom.read_bytes()) & 0xFFFFFFFF:08X}"
    assert gameid.identify("hash", rom) == expected


def test_two_dumps_that_differ_by_one_byte_are_two_games(tmp_path):
    """The whole file, not a prefix. Cartridge dumps of one series share long
    identical stretches of header and engine, so a partial digest is not a
    weaker identity — it is one game's settings landing on another."""
    a = tmp_path / "a.z64"
    b = tmp_path / "b.z64"
    a.write_bytes(b"\x80\x37\x12\x40" + b"\x00" * 4096)
    b.write_bytes(b"\x80\x37\x12\x40" + b"\x00" * 4095 + b"\x01")
    assert gameid.identify("hash", a) != gameid.identify("hash", b)


def test_a_disc_image_is_refused_by_the_hash_reader_rather_than_stalling(tmp_path):
    """A pack that picked `hash` for a system with disc images would otherwise
    read tens of gigabytes in front of a player waiting on a black screen."""
    big = tmp_path / "Disc.iso"
    big.write_bytes(b"\x00")
    original = gameid._MAX_HASH_BYTES
    gameid._MAX_HASH_BYTES = 0
    try:
        assert gameid.identify("hash", big) is None
    finally:
        gameid._MAX_HASH_BYTES = original


def test_a_replaced_rom_is_re_read_rather_than_remembered(tmp_path):
    """The memo is keyed on the stat, not the path. A box that cached by path
    alone would keep applying the old game's settings after the owner swapped
    the file — and the file name, which is all they can see, would not change."""
    rom = tmp_path / "cart.gba"
    rom.write_bytes(b"first" * 100)
    first = gameid.identify("hash", rom)
    rom.write_bytes(b"second" * 100)
    assert gameid.identify("hash", rom) != first


def test_the_last_resort_strips_what_two_dumps_of_one_game_disagree_about(tmp_path):
    """Region tags, revisions and scene brackets are exactly what differs
    between two copies of the same game. If they survived into the identity,
    a profile would apply to one player's dump and not the next one's."""
    usa = gameid.identify("filename", tmp_path / "Chrono Trigger (USA) [!].sfc")
    eur = gameid.identify("filename", tmp_path / "Chrono Trigger (Europe) (Rev 1).sfc")
    assert usa == eur == "chronotrigger"


def test_a_name_that_normalises_away_is_no_identity_at_all(tmp_path):
    """`.ini` as a file name would collect every unnameable game on the system
    into one bucket — the leak this whole phase exists to close, arriving
    through the back door."""
    assert gameid.identify("filename", tmp_path / "(((...))).bin") is None


# ── failing without taking the launch with it ────────────────────────────────

def test_an_unknown_strategy_is_a_log_line_and_not_a_crash(tmp_path, caplog):
    """The only way to reach one is a pack from a newer release than this
    backend — mid-OTA, or a local pack written against a later schema."""
    rom = tmp_path / "game.bin"
    rom.write_bytes(b"x")
    assert gameid.identify("quantum-entanglement", rom) is None
    assert "quantum-entanglement" in caplog.text


@pytest.mark.parametrize("strategy", sorted(gameid.READERS))
def test_no_reader_raises_on_a_file_that_is_not_what_it_expects(strategy, tmp_path):
    """Every reader is handed garbage, and a missing path, and must answer
    rather than throw. A per-game config is an improvement on a working system,
    never a precondition of it: an unreadable dump has to launch anyway.
    """
    junk = tmp_path / "not-a-game.bin"
    junk.write_bytes(b"\xff\xfe\x00\x01" * 8)
    gameid.identify(strategy, junk)
    gameid.identify(strategy, tmp_path / "does-not-exist.bin")
