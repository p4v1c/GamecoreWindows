"""External disks: what is attached, where it lands, and what happens when it goes.

**Nothing in this file touches a real device.** Every subprocess goes through a
`runner` double, every filesystem is a `tmp_path` tree. No test here has ever run
`mount`, `umount`, `udisksctl` or `mkfs`, and what is asserted about those is the
argv that WOULD have been run — which is the part that can be wrong.

The five states this has to tell apart are the ones the box actually meets:
a disk that arrives, one that arrives already mounted, one that leaves, one that
leaves while a game is running from it, and a link left over from a session that
ended with the disk still plugged in.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import paths, storage                    # noqa: E402


# ── doubles ──────────────────────────────────────────────────────────────────

def lsblk(*partitions: dict, removable: bool = True) -> str:
    """An `lsblk -J` payload with the partitions nested under one disk.

    Nested on purpose: removability is a property of the DISK, and a partition
    on a USB stick reports `rm: false` for itself. A payload that put the flag
    on the partition would test a shape lsblk never emits.
    """
    return json.dumps({"blockdevices": [{
        "name": "sdb", "path": "/dev/sdb", "type": "disk",
        "rm": removable, "hotplug": False, "fstype": None,
        "label": None, "uuid": None, "mountpoint": None, "size": "1T",
        "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part",
                      "rm": False, "hotplug": False, "size": "1T",
                      "label": None, "uuid": None, "fstype": "ext4",
                      "mountpoint": None, **p} for p in partitions],
    }]})


def runner_for(payload: str, mount_rc: int = 0, mount_err: str = ""):
    """A subprocess double that records every argv it was handed."""
    calls: list[list[str]] = []

    def run(argv, _timeout):
        calls.append(list(argv))
        if argv[0] == "lsblk":
            return 0, payload, ""
        return mount_rc, "", mount_err

    run.calls = calls
    return run


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Point <DATA> at a throwaway tree, restored after the test.

    `data_dir()` reads the module global on every call, so patching it here is
    honest — unlike the trap paths.py documents, where a test assigned
    `config.GAMECORE_ROOT` after resolution had moved elsewhere and went on
    passing while asserting on nothing. `monkeypatch` rather than `use_roots()`
    because `use_roots` has no undo and this must not leak into the next test.
    """
    monkeypatch.setattr(paths, "GAMECORE_DATA", tmp_path / "data")
    return tmp_path / "data"


def volume(**over) -> storage.Volume:
    base = dict(name="sdb1", path="/dev/sdb1", label="ROMS", uuid="A4E2-19F7",
                fstype="ext4", mountpoint="/run/media/gc/ROMS",
                removable=True, size="1T")
    return storage.Volume(**{**base, **over})


# ── parsing what is attached ─────────────────────────────────────────────────

def test_a_partition_inherits_its_disks_removability():
    """The bug this shape exists to catch: `rm` is on the DISK, and a partition
    on a USB stick reports false for itself. Reading it off the partition made
    every external disk look internal — so nothing was ever offered."""
    found = storage.parse_lsblk(lsblk({"label": "ROMS", "fstype": "ext4"}))
    assert [v.path for v in found] == ["/dev/sdb1"]
    assert found[0].removable


def test_the_internal_disk_is_never_listed():
    """It has a label and a mount point like any other. Offering it in a screen
    with an Eject button is handing someone their own root filesystem."""
    assert storage.parse_lsblk(lsblk({"label": "system"}, removable=False)) == []


def test_a_whole_disk_filesystem_with_no_partition_table_is_found():
    """Common on a pre-formatted USB stick. A parser that only looked at
    `children` would report the disk as holding nothing."""
    payload = json.dumps({"blockdevices": [{
        "name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True,
        "fstype": "exfat", "label": "ROMS", "uuid": "A4E2",
        "mountpoint": None, "size": "64G",
    }]})
    assert [v.path for v in storage.parse_lsblk(payload)] == ["/dev/sdb"]


def test_a_partition_with_nothing_on_it_is_not_offered():
    """swap, LVM metadata, an empty partition. Not a ROM disk, and listing it
    invites someone to try to mount it."""
    assert storage.parse_lsblk(lsblk({"fstype": "swap"})) == []
    assert storage.parse_lsblk(lsblk({"fstype": None})) == []


def test_output_that_is_not_json_gives_nothing_rather_than_raising():
    """lsblk missing, or a version that printed a warning first. The settings
    screen must draw "no disks", not fail."""
    assert storage.parse_lsblk("not json at all") == []
    assert storage.list_volumes(lambda *a: (1, "", "no lsblk")) == []


def test_nulls_where_strings_were_expected_do_not_crash():
    """lsblk writes `null`, not "", for a disk with no label."""
    found = storage.parse_lsblk(lsblk({"label": None, "uuid": None,
                                       "mountpoint": None, "fstype": "ext4"}))
    assert found[0].label == "" and found[0].mountpoint == ""
    assert not found[0].is_mounted


# ── the stable name ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,expected", [
    ("ROMS", "roms"),
    ("My ROMs", "my-roms"),
    ("roms-disk", "roms-disk"),
    ("  Games  ", "games"),
])
def test_the_slug_comes_from_the_label_the_owner_chose(label, expected):
    """`roms-disk`, not `A4E2-19F7`. It is the only name they will recognise."""
    assert storage.slug_for(label, "A4E2-19F7") == expected


def test_a_label_with_a_slash_cannot_become_two_directories():
    """`My ROMs/2` naming a path two levels deep is a library that scans the
    wrong place, silently. This slug ends up inside systems.json."""
    assert "/" not in storage.slug_for("My ROMs/2", "A4E2")


def test_a_disk_with_no_usable_label_falls_back_to_its_uuid():
    """An empty slug would collide with every other empty slug and point every
    unlabelled disk at one directory."""
    assert storage.slug_for("", "A4E2-19F7") == "disk-a4e2-19f7"
    assert storage.slug_for("///", "A4E2-19F7") == "disk-a4e2-19f7"
    # Nothing usable anywhere: refused rather than guessed.
    assert storage.slug_for("", "") == ""


# ── the stable link, which is the whole design ───────────────────────────────

@pytest.mark.linux_until_port(5)
def test_the_link_points_at_wherever_the_disk_landed(data_root):
    link = storage.link_volume(volume())
    assert link == data_root / "volumes" / "roms"
    assert link.is_symlink()
    assert str(link.readlink()) == "/run/media/gc/ROMS"


@pytest.mark.linux_until_port(5)
def test_replugging_repoints_the_link_instead_of_failing(data_root):
    """The bug the stable link exists for.

    Plug the same disk in twice without a clean unmount and udisks calls the
    second mount `ROMS 1`. A romsPath recorded against the real mount point
    scans nothing from then on, without a word. Recorded against the link it
    survives — but only if a second arrival REPLACES the link rather than
    raising on a name that already exists.
    """
    storage.link_volume(volume())
    storage.link_volume(volume(mountpoint="/run/media/gc/ROMS 1"))
    link = data_root / "volumes" / "roms"
    assert str(link.readlink()) == "/run/media/gc/ROMS 1"


def test_a_disk_that_is_not_mounted_gets_no_link(data_root):
    assert storage.link_volume(volume(mountpoint="")) is None
    assert not (data_root / "volumes").exists()


def test_a_disk_with_no_name_at_all_gets_no_link(data_root):
    assert storage.link_volume(volume(label="", uuid="")) is None


def test_unlinking_removes_only_our_own_symlink(data_root):
    """The one bug in here that would cost someone their ROMs.

    `<DATA>/volumes/<slug>` is a path an owner may legitimately have replaced
    with a real directory holding real files. Removing a tree this module did
    not create is not a tidy-up, it is data loss.
    """
    real = data_root / "volumes" / "roms"
    real.mkdir(parents=True)
    (real / "a-game.iso").write_text("not a symlink")

    assert storage.unlink_volume(volume()) is False
    assert (real / "a-game.iso").exists()


@pytest.mark.linux_until_port(5)
def test_a_link_left_over_from_a_previous_session_is_found(data_root):
    """The reboot hole. The box is powered off with the disk attached and comes
    up without it: nothing generated a departure, so last session's link is
    still there pointing at an empty /run/media path. It resolves, it is a
    directory, and it scans zero games — which reads exactly like a library
    that has lost its contents."""
    (data_root / "volumes").mkdir(parents=True)
    (data_root / "volumes" / "roms").symlink_to(data_root / "gone")

    assert storage.stale_links([]) == [data_root / "volumes" / "roms"]
    # A disk that IS here keeps its link.
    live = data_root / "here"
    live.mkdir()
    assert storage.stale_links([volume(mountpoint=str(live))]) == []


# ── the P3 seam: a library on an external disk ───────────────────────────────

@pytest.mark.linux_until_port(5)
def test_a_romspath_under_the_stable_link_resolves_onto_the_disk(data_root):
    """Point 2 of the brief, and it needed no new code — which is the P3 split
    paying for itself. `volumes/roms/nintendo` is a relative romsPath like any
    other, so resolve_data_path already lands it on the mounted disk."""
    mount = data_root / "mounted"
    (mount / "nintendo").mkdir(parents=True)
    (mount / "nintendo" / "game.iso").write_text("rom")
    storage.link_volume(volume(mountpoint=str(mount)))

    resolved = paths.resolve_data_path("volumes/roms/nintendo")
    assert (resolved / "game.iso").read_text() == "rom"


@pytest.mark.linux_until_port(5)
def test_the_scanner_reads_nothing_from_a_disk_that_has_gone(data_root):
    """Hot removal must not take the scanner down — it must return an empty
    system. `iter_rom_files` already returns early for a path that is not
    there; this pins that the combination still holds through the link."""
    from backend.services.rom_scanner import iter_rom_files

    mount = data_root / "mounted"
    (mount / "nintendo").mkdir(parents=True)
    (mount / "nintendo" / "game.iso").write_text("rom")
    storage.link_volume(volume(mountpoint=str(mount)))
    resolved = paths.resolve_data_path("volumes/roms/nintendo")
    assert len(list(iter_rom_files(resolved, ["*.iso"]))) == 1

    # The cable comes out: the mount point is gone, the link still exists.
    (mount / "nintendo" / "game.iso").unlink()
    (mount / "nintendo").rmdir()
    mount.rmdir()
    assert list(iter_rom_files(resolved, ["*.iso"])) == []


# ── filesystems that keep no permissions ─────────────────────────────────────

@pytest.mark.parametrize("fstype", ["exfat", "ntfs", "vfat", "fuseblk"])
def test_a_disk_with_no_posix_permissions_says_so(fstype):
    """Not a detail. Every file on exFAT takes the mount's own uid and mode, so
    a Flatpak emulator cannot preserve permissions, cannot trust a lock file,
    and has no atomic rename if a write is interrupted. ROMs are read-only and
    perfectly happy; saves are not."""
    row = storage.describe(volume(fstype=fstype))
    assert row["keeps_permissions"] is False
    assert "saves" in row["saves_warning"].lower()


@pytest.mark.parametrize("fstype", ["ext4", "btrfs", "xfs"])
def test_a_normal_filesystem_carries_no_warning(fstype):
    """A warning on a disk with nothing wrong with it is how the owner learns
    to ignore warnings."""
    row = storage.describe(volume(fstype=fstype))
    assert row["keeps_permissions"] is True
    assert row["saves_warning"] == ""


# ── acting on a device, without ever acting on a device ──────────────────────

def test_mounting_asks_udisks_and_never_needs_root(data_root):
    run = runner_for(lsblk())
    ok, _ = storage.mount(volume(mountpoint=""), run)
    assert ok
    # udisksctl does the privileged part over D-Bus as the logged-in user. A
    # `sudo mount` here would be a root path the box does not otherwise need.
    assert run.calls == [["udisksctl", "mount", "--no-user-interaction",
                          "-b", "/dev/sdb1"]]
    assert not any(c[0] in ("sudo", "mount") for c in run.calls)


def test_unmounting_flushes_before_the_cable_comes_out(data_root):
    storage.link_volume(volume())
    run = runner_for(lsblk())
    ok, _ = storage.unmount(volume(), run)
    assert ok
    assert run.calls == [["udisksctl", "unmount", "--no-user-interaction",
                          "-b", "/dev/sdb1"]]
    # A clean eject takes its stable link with it, or the next scan reads a
    # link pointing at a mount point that no longer exists.
    assert not (data_root / "volumes" / "roms").exists()


@pytest.mark.linux_until_port(5)
def test_a_busy_disk_reports_why_rather_than_a_generic_failure(data_root):
    """"target is busy" is actionable — a game is still reading it. Replacing
    udisks's own words with a generic failure leaves nothing to act on."""
    run = runner_for(lsblk(), mount_rc=1, mount_err="Error unmounting: target is busy")
    ok, detail = storage.unmount(volume(), run)
    assert not ok
    assert "busy" in detail
    # It must NOT drop the link for an unmount that did not happen.
    storage.link_volume(volume())
    storage.unmount(volume(), run)
    assert (data_root / "volumes" / "roms").is_symlink()


def test_mounting_something_already_mounted_runs_nothing():
    run = runner_for(lsblk())
    ok, detail = storage.mount(volume(), run)
    assert ok and detail == "/run/media/gc/ROMS"
    assert run.calls == []


def test_find_matches_on_the_device_path_not_a_row_number():
    """A row position is not a handle: a disk arriving while the screen is open
    renumbers the list, and Eject would then detach the wrong disk."""
    run = runner_for(lsblk({"label": "A", "mountpoint": "/run/a"}))
    assert storage.find("/dev/sdb1", run).label == "A"
    assert storage.find("/dev/sdc9", run) is None


# ── is the running game on this disk ─────────────────────────────────────────

def test_a_rom_under_a_mount_is_recognised_without_touching_the_disk():
    """Purely lexical, and it has to be: the question is asked exactly when the
    mount has just vanished, so anything that stats the path answers about a
    directory that is no longer there."""
    assert storage.path_is_under("/run/media/gc/ROMS/nintendo/g.iso", "/run/media/gc/ROMS")
    assert not storage.path_is_under("/home/gc/emu/g.iso", "/run/media/gc/ROMS")
    # A prefix that is not a path boundary is not "under".
    assert not storage.path_is_under("/run/media/gc/ROMS2/g.iso", "/run/media/gc/ROMS")
    assert not storage.path_is_under("", "/run/media/gc/ROMS")
    assert not storage.path_is_under("/run/media/gc/ROMS/g.iso", "")
