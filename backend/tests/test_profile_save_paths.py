"""Save options and folders pointed at a profile and back, on real seed files.

The owner's values are remembered before the first write and put back for the
primary profile; folders are swapped for a directory link (a junction on
Windows) and renamed back, never copied.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.services import profile_save_paths as psp
from backend.services.errors import ServiceError
from backend.services.platform import filesystem

CATALOG = Path(__file__).resolve().parents[2] / "catalog"


def _keys(file: Path, pack: str) -> list[tuple[Path, dict]]:
    import json
    spec = json.loads((CATALOG / pack / "pack.json").read_text())["profileSaves"]
    return [(file, e) for e in spec["keys"]]


@pytest.fixture
def root(tmp_path):
    return tmp_path / "profile-saves"


def test_retroarch_points_at_the_profile_before_its_include_and_back(tmp_path, root):
    cfg = tmp_path / "nes.cfg"
    shutil.copy(CATALOG / "nes" / "seed" / "nes.cfg", cfg)
    seed = cfg.read_text()
    sam = root / "sam" / "nes"
    psp.apply_keys(_keys(cfg, "nes"), sam, root)
    text = cfg.read_text()
    assert f'savefile_directory = "{sam}/saves"' in text
    assert text.index("savestate_directory") < text.index("#include"), "the include must not win"
    psp.apply_keys(_keys(cfg, "nes"), None, root)
    assert cfg.read_text() == seed, "absent before, absent again"


def test_an_ini_option_the_owner_set_comes_back(tmp_path, root):
    ini = tmp_path / "PCSX2.ini"
    shutil.copy(CATALOG / "pcsx2" / "seed" / "PCSX2.ini", ini)
    ini.write_text(ini.read_text().replace("MemoryCards = memcards", "MemoryCards = /mnt/cards"))
    before = ini.read_text()
    psp.apply_keys(_keys(ini, "pcsx2"), root / "sam" / "pcsx2", root)
    assert f"MemoryCards = {root / 'sam' / 'pcsx2'}/memcards" in ini.read_text()
    # Sam to Ana: still the owner's value that is remembered, not Sam's.
    psp.apply_keys(_keys(ini, "pcsx2"), root / "ana" / "pcsx2", root)
    psp.apply_keys(_keys(ini, "pcsx2"), None, root)
    assert ini.read_text() == before


def test_a_missing_section_is_added_and_removed_again(tmp_path, root):
    ini = tmp_path / "settings.ini"
    ini.write_text("[Main]\nSettingsVersion = 3\n")
    psp.apply_keys(_keys(ini, "duckstation"), root / "sam" / "duckstation", root)
    assert "[MemoryCards]\nDirectory = " in ini.read_text()
    psp.apply_keys(_keys(ini, "duckstation"), None, root)
    assert "Directory" not in ini.read_text() and "SaveStates" not in ini.read_text()


def test_qt_keys_with_backslashes(tmp_path, root):
    ini = tmp_path / "qt-config.ini"
    shutil.copy(CATALOG / "azahar" / "seed" / "qt-config.ini", ini)
    before = ini.read_text()
    psp.apply_keys(_keys(ini, "azahar"), root / "sam" / "azahar", root)
    text = ini.read_text()
    assert "use_custom_storage=true" in text and "use_custom_storage\\default=false" in text
    assert f"sdmc_directory={root / 'sam' / 'azahar'}/sdmc/" in text
    assert "nand_directory=" in text, "the NAND stays shared"
    psp.apply_keys(_keys(ini, "azahar"), None, root)
    assert ini.read_text() == before


def test_no_config_file_yet_still_gives_a_profile_its_folder(tmp_path, root):
    """RetroArch writes its .cfg on exit: a box that never ran a system has none,
    and refusing would lock every profile but the owner out of it."""
    missing = tmp_path / "cfg" / "nes.cfg"
    psp.apply_keys(_keys(missing, "nes"), None, root)
    assert not missing.exists(), "the primary writes nothing"
    psp.apply_keys(_keys(missing, "nes"), root / "sam" / "nes", root)
    assert str(root / "sam" / "nes") in missing.read_text()
    psp.apply_keys(_keys(missing, "nes"), None, root)
    assert "savefile_directory" not in missing.read_text(), "the owner's 'absent' comes back"


def test_a_folder_is_swapped_for_a_link_and_renamed_back(tmp_path):
    saves = tmp_path / "PSP" / "SAVEDATA"
    (saves / "ULUS10041").mkdir(parents=True)
    (saves / "ULUS10041" / "DATA.BIN").write_text("owner")
    sam = tmp_path / "profile-saves" / "sam" / "ppsspp" / "SAVEDATA"
    psp.apply_dir(saves, sam, sam.parents[2])
    assert filesystem.is_link(saves) and saves.resolve() == sam.resolve()
    assert (tmp_path / "PSP" / "SAVEDATA.gamecore-primary" / "ULUS10041" / "DATA.BIN").read_text() == "owner"
    (saves / "NEW").mkdir()                       # Sam's game writes through the link
    psp.apply_dir(saves, None, sam.parents[2])
    assert not filesystem.is_link(saves) and (saves / "ULUS10041" / "DATA.BIN").read_text() == "owner"
    assert (sam / "NEW").is_dir(), "Sam's save stays in Sam's folder"
    psp.apply_dir(saves, None, sam.parents[2])    # nothing to do twice


def test_a_folder_the_emulator_never_made_still_gets_a_link(tmp_path):
    saves = tmp_path / "nand" / "user" / "save"
    saves.parent.mkdir(parents=True)
    psp.apply_dir(saves, tmp_path / "sam", tmp_path)
    assert filesystem.is_link(saves)
    psp.apply_dir(saves, None, tmp_path)
    assert not saves.exists()


def test_a_folder_whose_parent_is_missing_refuses_the_profile(tmp_path):
    with pytest.raises(ServiceError):
        psp.apply_dir(tmp_path / "dev_hdd0" / "home" / "00000001" / "savedata", tmp_path / "sam", tmp_path)


def test_two_real_folders_are_never_merged(tmp_path):
    saves = tmp_path / "SAVEDATA"
    saves.mkdir()
    (tmp_path / "SAVEDATA.gamecore-primary").mkdir()
    with pytest.raises(ServiceError):
        psp.apply_dir(saves, None, tmp_path)
    with pytest.raises(ServiceError):
        psp.apply_dir(saves, tmp_path / "sam", tmp_path)


def test_the_owners_own_link_is_kept_for_every_profile(tmp_path):
    elsewhere = tmp_path / "usb" / "switch-saves"
    (elsewhere / "0100").mkdir(parents=True)
    saves = tmp_path / "nand" / "user" / "save"
    saves.parent.mkdir(parents=True)
    filesystem.link_dir(saves, elsewhere)         # the owner keeps his saves on another disk
    root = tmp_path / "profile-saves"
    psp.apply_dir(saves, None, root)              # the primary profile launches
    assert filesystem.is_link(saves) and saves.resolve() == elsewhere.resolve()
    psp.apply_dir(saves, root / "sam" / "switch" / "save", root)
    assert saves.resolve() == (root / "sam" / "switch" / "save").resolve()
    psp.apply_dir(saves, None, root)
    assert filesystem.is_link(saves) and saves.resolve() == elsewhere.resolve()
    assert (elsewhere / "0100").is_dir()
