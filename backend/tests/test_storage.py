"""External disks: listing, the stable link, and safe removal.

`platform/volumes.py` is faked (`disks`), every filesystem is a `tmp_path`
tree: no test here lists or ejects a real disk. The stable link is a real
junction, made and removed on NTFS.
"""
from __future__ import annotations

import pytest

from backend.services import paths, storage
from backend.services.platform import filesystem
from backend.services.platform import volumes as platform_volumes
from backend.tests.fake_volumes import row

# Read at import, before conftest's session guard empties the listing.
REAL_LIST_VOLUMES = platform_volumes.list_volumes


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Point <DATA> at a throwaway tree, restored after the test."""
    monkeypatch.setattr(paths, "GAMECORE_DATA", tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def drive(tmp_path):
    """A folder standing for the drive's root (`E:\\`)."""
    root = tmp_path / "E-drive"
    root.mkdir()
    return root


def volume(**over) -> storage.Volume:
    return storage.Volume(**row(**over))


# ── listing ─────────────────────────────────────────────────────────────────

def test_the_drives_windows_reports_are_listed(disks):
    disks.rows = [row(), row(name="F:", path="F:", label="SD", fstype="exfat", mountpoint="F:\\")]
    assert [v.path for v in storage.list_volumes()] == ["E:", "F:"]


def test_a_listing_windows_refuses_is_empty_not_an_error(disks, monkeypatch):
    def refuse():
        raise OSError("GetLogicalDrives failed")
    monkeypatch.setattr(platform_volumes, "list_volumes", refuse)
    assert storage.list_volumes() == []


@pytest.mark.parametrize("drive_type, bus, external", [
    (2, 0, True),       # a USB stick or an SD card: Windows says removable
    (3, 7, True),       # a USB hard drive reports itself as fixed
    (3, 12, True),      # SD
    (3, 17, False),     # the internal NVMe
    (3, 11, False),     # SATA
])
def test_external_means_removable_or_on_an_external_bus(drive_type, bus, external):
    assert platform_volumes.is_external(drive_type, bus) is external


@pytest.mark.parametrize("size, text", [(500107862016, "465.8G"), (512, "512B"),
                                        (2 * 1024 ** 4, "2.0T")])
def test_sizes_read_the_way_the_screen_shows_them(size, text):
    assert platform_volumes.human_size(size) == text


def test_the_real_listing_never_offers_the_system_drive():
    import os
    system = os.environ.get("SystemDrive", "C:").upper()
    assert all(v["path"] != system for v in REAL_LIST_VOLUMES())


# ── the slug ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,expected", [
    ("ROMS", "roms"), ("My Games", "my-games"), ("Games_2024!", "games-2024"),
    ("  padded  ", "padded"),
])
def test_the_slug_comes_from_the_label_the_owner_chose(label, expected):
    assert storage.slug_for(label) == expected


def test_a_label_with_a_slash_cannot_become_two_directories():
    assert "/" not in storage.slug_for("My ROMs/2") and "\\" not in storage.slug_for("a\\b")


def test_a_disk_with_no_usable_label_falls_back_to_its_serial():
    assert storage.slug_for("", "A4E2-19F7") == "disk-a4e2-19f7"
    assert storage.slug_for("///", "A4E2-19F7") == "disk-a4e2-19f7"
    assert storage.slug_for("", "") == ""


# ── the stable link ──────────────────────────────────────────────────────────

def test_the_link_points_at_wherever_the_disk_landed(data_root, drive):
    link = storage.link_volume(volume(mountpoint=str(drive)))
    assert link == data_root / "volumes" / "roms"
    assert filesystem.is_link(link)
    assert filesystem.link_target(link) == drive


def test_replugging_repoints_the_link_instead_of_failing(data_root, drive, tmp_path):
    storage.link_volume(volume(mountpoint=str(drive)))
    other = tmp_path / "F-drive"
    other.mkdir()
    link = storage.link_volume(volume(mountpoint=str(other)))
    assert filesystem.link_target(link) == other


def test_a_real_folder_at_the_link_is_the_owners_and_left_alone(data_root, drive):
    mine = data_root / "volumes" / "roms"
    mine.mkdir(parents=True)
    (mine / "keep.txt").write_text("mine")
    assert storage.link_volume(volume(mountpoint=str(drive))) is None
    assert (mine / "keep.txt").read_text() == "mine"


def test_a_disk_that_is_not_mounted_gets_no_link(data_root):
    assert storage.link_volume(volume(mountpoint="")) is None


def test_a_disk_with_no_name_at_all_gets_no_link(data_root, drive):
    assert storage.link_volume(volume(label="", uuid="", mountpoint=str(drive))) is None


def test_unlinking_removes_only_our_own_link(data_root, drive):
    link = storage.link_volume(volume(mountpoint=str(drive)))
    (drive / "game.iso").write_text("rom")
    assert storage.unlink_volume(volume(mountpoint=str(drive))) is True
    assert not link.exists() and (drive / "game.iso").exists(), "the disk's files went with it"
    real = data_root / "volumes" / "roms"
    real.mkdir()
    assert storage.unlink_volume(volume(mountpoint=str(drive))) is False
    assert real.is_dir()


def test_a_link_left_over_from_a_previous_session_is_found(data_root, drive):
    link = storage.link_volume(volume(mountpoint=str(drive)))
    drive.rmdir()                                  # the disk is not here this boot
    assert storage.stale_links([]) == [link]


def test_a_live_disks_link_is_not_stale(data_root, drive):
    storage.link_volume(volume(mountpoint=str(drive)))
    assert storage.stale_links([volume(mountpoint=str(drive))]) == []


def test_a_romspath_under_the_stable_link_resolves_onto_the_disk(data_root, drive):
    (drive / "nintendo").mkdir()
    (drive / "nintendo" / "game.iso").write_text("rom")
    storage.link_volume(volume(mountpoint=str(drive)))
    resolved = paths.resolve_data_path("volumes/roms/nintendo")
    assert (resolved / "game.iso").read_text() == "rom"


def test_the_scanner_reads_nothing_from_a_disk_that_has_gone(data_root, drive):
    from backend.services.rom_scanner import iter_rom_files

    (drive / "nintendo").mkdir()
    (drive / "nintendo" / "game.iso").write_text("rom")
    storage.link_volume(volume(mountpoint=str(drive)))
    resolved = paths.resolve_data_path("volumes/roms/nintendo")
    assert len(list(iter_rom_files(resolved, ["*.iso"]))) == 1
    (drive / "nintendo" / "game.iso").unlink()
    (drive / "nintendo").rmdir()
    drive.rmdir()
    assert list(iter_rom_files(resolved, ["*.iso"])) == []


# ── filesystems that keep no journal ─────────────────────────────────────────

@pytest.mark.parametrize("fstype", ["exfat", "fat32"])
def test_a_disk_with_no_journal_says_so(fstype):
    described = storage.describe(volume(fstype=fstype))
    assert described["keeps_permissions"] is False
    assert "NTFS" in described["saves_warning"]


@pytest.mark.parametrize("fstype", ["ntfs", "refs"])
def test_a_journaled_filesystem_carries_no_warning(fstype):
    assert storage.describe(volume(fstype=fstype))["saves_warning"] == ""


# ── acting on a device ───────────────────────────────────────────────────────

def test_mounting_a_drive_windows_mounted_changes_nothing(disks):
    assert storage.mount(volume()) == (True, "E:\\")
    assert disks.ejected == []


def test_a_disk_with_no_letter_says_what_to_do():
    ok, detail = storage.mount(volume(mountpoint=""))
    assert ok is False and "Disk Management" in detail


def test_ejecting_asks_windows_and_drops_the_link(disks, data_root, drive):
    v = volume(mountpoint=str(drive))
    storage.link_volume(v)
    assert storage.unmount(v) == (True, "Safe to unplug")
    assert disks.ejected == ["E:"]
    assert not (data_root / "volumes" / "roms").exists()


def test_a_busy_disk_reports_why_and_keeps_its_link(disks, data_root, drive):
    disks.eject_result = (False, "E: is busy: a program is using it. Close what is "
                                 "reading it, then eject again.")
    v = volume(mountpoint=str(drive))
    storage.link_volume(v)
    ok, detail = storage.unmount(v)
    assert ok is False and "busy" in detail
    assert filesystem.is_link(data_root / "volumes" / "roms")


def test_find_matches_on_the_drive_whatever_its_case(disks):
    disks.rows = [row(), row(name="F:", path="F:", label="SD", mountpoint="F:\\")]
    assert storage.find("f:").label == "SD"
    assert storage.find("G:") is None


# ── is the running game on this disk ─────────────────────────────────────────

def test_a_rom_under_a_drive_is_recognised_without_touching_the_disk():
    assert storage.path_is_under("E:\\ROMS\\nintendo\\g.iso", "E:\\")
    assert storage.path_is_under("e:\\roms\\g.iso", "E:\\"), "drive letters ignore case"
    assert not storage.path_is_under("C:\\Users\\gc\\g.iso", "E:\\")
    assert not storage.path_is_under("", "E:\\")
    assert not storage.path_is_under("E:\\g.iso", "")
