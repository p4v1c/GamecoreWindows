"""melonDS per-profile saves: `place_saves` writes each instance's save paths.

The primary profile (every entry None) must leave the file byte-identical;
another profile's folder must never survive into the next launch.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load("tested_melonds_generator_saves", HERE.parent / "generator.py")
setup = _load("tested_melonds_mp_setup_saves", HERE.parent / "multiplayer" / "setup.py")
SEED = (HERE.parent / "seed" / "melonDS.toml").read_text()
LEGACY = [None, None, None, None]


@pytest.fixture
def box(tmp_path):
    target = tmp_path / "config" / "melonDS.toml"
    target.parent.mkdir()
    target.write_text(SEED)
    root = tmp_path / "data" / "emu" / "profile-saves"
    return target, root


def _place(target, root, dirs):
    generator.place_saves(dirs=dirs, root=root, opts={"target": target})


def _instance0(text: str, key: str) -> str:
    return generator._save_value(text, "Instance0", key)


def test_primary_profile_leaves_the_config_byte_identical(box):
    target, root = box
    before = target.stat().st_mtime_ns
    _place(target, root, LEGACY)
    assert target.read_text() == SEED
    assert target.stat().st_mtime_ns == before, "no write at all"
    assert not target.with_name("melonDS.toml.bak-ctrlmodel").exists()


@pytest.mark.linux_until_port(7)
def test_another_profile_points_player_one_saves_and_states_at_its_folder(box):
    target, root = box
    sam = root / "b0b0" / "melonds"
    _place(target, root, [sam, None, None, None])
    text = target.read_text()
    assert _instance0(text, "SaveFilePath") == f'"{sam}"'
    assert _instance0(text, "SavestatePath") == f'"{sam}"'
    changed = [line for line in text.splitlines() if line not in SEED.splitlines()]
    assert changed == [f'SaveFilePath = "{sam}"', f'SavestatePath = "{sam}"']


@pytest.mark.linux_until_port(7)
def test_switching_profiles_never_keeps_the_previous_folder(box):
    target, root = box
    ana, sam = root / "a1a1" / "melonds", root / "b0b0" / "melonds"
    _place(target, root, [ana, None, None, None])
    _place(target, root, [sam, None, None, None])
    assert str(ana) not in target.read_text()
    assert _instance0(target.read_text(), "SaveFilePath") == f'"{sam}"'


def test_back_to_the_primary_profile_empties_a_stale_path(box):
    """A launch that crashed, or melonDS writing its config on exit, leaves the
    last profile's folder behind; the primary's next launch must clear it."""
    target, root = box
    _place(target, root, [root / "b0b0" / "melonds", None, None, None])
    _place(target, root, LEGACY)
    assert target.read_text() == SEED


def test_a_path_the_owner_chose_is_kept_for_the_primary_profile(box):
    target, root = box
    mine = SEED.replace('SaveFilePath = ""', 'SaveFilePath = "/userdata/my-saves"', 1)
    target.write_text(mine)
    _place(target, root, LEGACY)
    assert target.read_text() == mine


def test_players_two_to_four_keep_their_paths_and_blank_saves(box, tmp_path):
    """Pads carry no profile yet: P2-4 stay next to the ROM, as before."""
    target, root = box
    text = SEED + '\n[Instance1]\nSaveFilePath = ""\nSavestatePath = ""\n'
    target.write_text(text)
    roms = tmp_path / "emu" / "melonds"
    roms.mkdir(parents=True)
    (roms / "Game.nds").write_bytes(b"rom")
    (roms / "Game.sav").write_bytes(b"\x01" * 8)
    _place(target, root, [root / "b0b0" / "melonds", None, None, None])
    after = target.read_text()
    assert generator._save_value(after, "Instance1", "SaveFilePath") == '""'
    assert generator.section(after, "Instance2") is None
    made = setup.blank_player_saves(str(roms / "Game.nds"), 2, after)
    assert made == [roms / "Game.sav.2"]
    assert (roms / "Game.sav.2").read_bytes() == b"\xff" * 8


@pytest.mark.linux_until_port(7)
def test_a_later_player_folder_creates_its_instance_section(box):
    target, root = box
    p2 = root / "c2c2" / "melonds"
    _place(target, root, [None, p2, None, None])
    text = target.read_text()
    assert generator._save_value(text, "Instance1", "SaveFilePath") == f'"{p2}"'
    assert _instance0(text, "SaveFilePath") == '""'


def test_no_config_refuses_a_folder_and_ignores_the_primary(tmp_path):
    target, root = tmp_path / "melonDS.toml", tmp_path / "saves"
    _place(target, root, LEGACY)
    assert not target.exists()
    with pytest.raises(FileNotFoundError):
        _place(target, root, [root / "b0b0" / "melonds", None, None, None])


def test_a_path_the_owner_chose_refuses_another_profile_and_stays(box):
    target, root = box
    mine = SEED.replace('SaveFilePath = ""', 'SaveFilePath = "/userdata/my-saves"', 1)
    target.write_text(mine)
    with pytest.raises(Exception, match="set by hand"):
        _place(target, root, [root / "sam" / "melonds", None, None, None])
    assert target.read_text() == mine
