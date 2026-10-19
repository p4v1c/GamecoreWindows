"""What the box DOES when a disk arrives or leaves.

`poll_once` is deliberately split out of the sleep loop so all of this is
reachable without a real disk, a real subprocess or a wait: the runner is a
double and the websocket is a list.

The scenario that matters most is the last one — the disk pulled out while a
game is running from it. It is the case a living-room box meets for real, it
cannot be repaired from here, and before this the only thing the player got was
an emulator that froze for no stated reason.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import paths, storage, storage_monitor      # noqa: E402


def poll(was, ws, runner=None):
    """One comparison pass, run to completion from a sync test.

    `asyncio.run` and not a pytest-asyncio marker, for the reason test_gamemedia
    spells out: nothing in this suite depends on pytest-asyncio, and the release
    workflow installs only `requirements.txt` + `pytest`. A `@pytest.mark.asyncio`
    here would pass on a developer's machine and do NOTHING on the runner —
    where a red pytest step is what stops a broken release being published.
    """
    return asyncio.run(storage_monitor.poll_once(was, ws, runner))


class FakeWs:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, event, data):
        self.sent.append((event, data))

    def events(self) -> list[str]:
        return [e for e, _ in self.sent]

    def payload(self, event: str) -> dict:
        return next(d for e, d in self.sent if e == event)


def lsblk(*partitions: dict) -> str:
    return json.dumps({"blockdevices": [{
        "name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True,
        "hotplug": False, "fstype": None, "label": None, "uuid": None,
        "mountpoint": None, "size": "1T",
        "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part",
                      "rm": False, "hotplug": False, "size": "1T",
                      "label": "ROMS", "uuid": "A4E2", "fstype": "ext4",
                      "mountpoint": None, **p} for p in partitions],
    }]})


def scripted(*payloads: str, mount_rc: int = 0):
    """A runner that returns each lsblk payload in turn, then repeats the last.

    Sequenced because mounting CHANGES the answer: `_on_arrival` re-reads the
    volume after udisksctl rather than assuming where it landed, and that
    re-read is the behaviour under test.
    """
    remaining = list(payloads)
    calls: list[list[str]] = []

    def run(argv, _timeout):
        calls.append(list(argv))
        if argv[0] == "lsblk":
            payload = remaining[0] if len(remaining) == 1 else remaining.pop(0)
            return 0, payload, ""
        return mount_rc, "", "mount refused" if mount_rc else ""

    run.calls = calls
    return run


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "GAMECORE_DATA", tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def no_game(monkeypatch):
    monkeypatch.setattr(storage_monitor, "_running_game", lambda: None)


def running(monkeypatch, rom_path: str, game_key: str = "Zelda.iso"):
    monkeypatch.setattr(storage_monitor, "_running_game",
                        lambda: {"game_key": game_key, "system_id": "gc",
                                 "rom_path": rom_path})


# ── a disk arrives ───────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(5)
def test_a_disk_plugged_in_is_mounted_linked_and_announced(data_root, no_game):
    """The headline. Plugging a ROM disk in did literally nothing before."""
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": None}),
                   lsblk({"mountpoint": "/run/media/gc/ROMS"}))

    live = poll({}, ws, run)

    assert ["udisksctl", "mount", "--no-user-interaction", "-b", "/dev/sdb1"] in run.calls
    assert ws.events() == ["storage:mounted"]
    assert (data_root / "volumes" / "roms").is_symlink()
    assert live["/dev/sdb1"].mountpoint == "/run/media/gc/ROMS"


@pytest.mark.linux_until_port(5)
def test_the_mount_point_is_re_read_rather_than_guessed(data_root, no_game):
    """udisks picks the mount point, and guessing it is the exact mistake the
    stable link exists to undo — a second plug lands on `ROMS 1`."""
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": None}),
                   lsblk({"mountpoint": "/run/media/gc/ROMS 1"}))

    poll({}, ws, run)
    assert str((data_root / "volumes" / "roms").readlink()) == "/run/media/gc/ROMS 1"
    assert ws.payload("storage:mounted")["mountpoint"] == "/run/media/gc/ROMS 1"


@pytest.mark.linux_until_port(5)
def test_a_disk_that_arrives_already_mounted_still_gets_its_link(data_root, no_game):
    """Mounted by hand from a file manager, or by a desktop automounter. It
    needs no udisksctl call and it still needs its stable path — treating only
    a NEW device as an arrival is why this case had no link."""
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"}))

    was = {"/dev/sdb1": storage.Volume(
        name="sdb1", path="/dev/sdb1", label="ROMS", uuid="A4E2",
        fstype="ext4", mountpoint="", removable=True, size="1T")}
    poll(was, ws, run)

    assert not any(c[0] == "udisksctl" for c in run.calls)
    assert (data_root / "volumes" / "roms").is_symlink()
    assert ws.events() == ["storage:mounted"]


def test_a_disk_that_cannot_be_mounted_says_so_once(data_root, no_game):
    """A disk with a filesystem the box cannot read. The owner needs to know it
    was seen and refused — silence is indistinguishable from a dead port."""
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": None}), mount_rc=1)

    poll({}, ws, run)
    assert ws.events() == ["storage:failed"]
    assert not (data_root / "volumes").exists()


def test_nothing_happens_twice_for_a_disk_that_has_not_changed(data_root, no_game):
    """The loop runs every three seconds forever. A disk that is simply still
    there must produce no event and no subprocess beyond the listing."""
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"}))

    live = poll({}, ws, run)
    ws.sent.clear()
    poll(live, ws, run)

    assert ws.events() == []
    assert not any(c[0] == "udisksctl" for c in run.calls)


# ── a disk leaves ────────────────────────────────────────────────────────────

def test_a_disk_pulled_out_is_announced_and_its_link_dropped(data_root, no_game):
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"}))
    live = poll({}, ws, run)
    ws.sent.clear()

    gone = scripted(json.dumps({"blockdevices": []}))
    poll(live, ws, gone)

    assert ws.events() == ["storage:removed"]
    assert not (data_root / "volumes" / "roms").exists()


def test_a_disk_unmounted_from_the_ui_counts_as_a_departure(data_root, no_game):
    """Still attached, no longer mounted. The link has to go either way, or the
    next scan reads a link pointing at a mount point that is not there."""
    ws = FakeWs()
    run = scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"}))
    live = poll({}, ws, run)
    ws.sent.clear()

    ejected = scripted(lsblk({"mountpoint": None}))
    poll(live, ws, ejected)

    assert ws.events() == ["storage:removed"]
    assert not (data_root / "volumes" / "roms").exists()


def test_a_disk_that_was_never_mounted_leaving_is_not_news(data_root, no_game):
    """Plugged in, refused, unplugged again. Nothing was ever usable, so there
    is nothing to tell the player they have lost."""
    ws = FakeWs()
    was = {"/dev/sdb1": storage.Volume(
        name="sdb1", path="/dev/sdb1", label="ROMS", uuid="A4E2",
        fstype="ext4", mountpoint="", removable=True, size="1T")}
    poll(was, ws, scripted(json.dumps({"blockdevices": []})))
    assert ws.events() == []


# ── the disk pulled out mid-game ─────────────────────────────────────────────

def test_a_disk_removed_during_a_game_is_called_out_by_name(data_root, monkeypatch):
    """The case a living-room box actually meets, and the one that costs
    something. The emulator holds an open descriptor on a device that is gone:
    it fails on its next read and its next save does not land. None of that can
    be repaired from here — saying it beats a freeze with no explanation."""
    ws = FakeWs()
    monkeypatch.setattr(storage_monitor, "_running_game", lambda: None)
    live = poll(
        {}, ws, scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"})))
    ws.sent.clear()

    running(monkeypatch, "/run/media/gc/ROMS/gamecube/Zelda.iso")
    poll(live, ws, scripted(json.dumps({"blockdevices": []})))

    assert "storage:lost" in ws.events()
    lost = ws.payload("storage:lost")
    assert lost["game_key"] == "Zelda.iso"
    assert "save" in lost["detail"].lower()
    # And the ordinary departure is still reported, so the storage screen
    # updates as well as the toast.
    assert "storage:removed" in ws.events()


def test_a_game_running_from_the_internal_disk_is_not_called_out(data_root, monkeypatch):
    """A second disk being unplugged while an internal-disk game runs is not
    that player's problem. A warning here is the noise that makes the real one
    unreadable."""
    ws = FakeWs()
    monkeypatch.setattr(storage_monitor, "_running_game", lambda: None)
    live = poll(
        {}, ws, scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"})))
    ws.sent.clear()

    running(monkeypatch, "/home/gc/emu/gamecube/Zelda.iso")
    poll(live, ws, scripted(json.dumps({"blockdevices": []})))

    assert ws.events() == ["storage:removed"]


def test_nothing_is_said_when_no_game_is_running(data_root, no_game):
    ws = FakeWs()
    live = poll(
        {}, ws, scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"})))
    ws.sent.clear()
    poll(live, ws, scripted(json.dumps({"blockdevices": []})))
    assert ws.events() == ["storage:removed"]


# ── the loop must survive everything ─────────────────────────────────────────

@pytest.mark.linux_until_port(5)
def test_a_websocket_that_throws_does_not_stop_disks_being_noticed(data_root, no_game):
    """A broadcast failure must not be able to stop the box seeing storage at
    all — the link is what makes the disk usable, the toast is decoration."""
    class BrokenWs:
        async def broadcast(self, event, data):
            raise RuntimeError("no listeners")

    run = scripted(lsblk({"mountpoint": None}),
                   lsblk({"mountpoint": "/run/media/gc/ROMS"}))
    live = poll({}, BrokenWs(), run)

    assert (data_root / "volumes" / "roms").is_symlink()
    assert live["/dev/sdb1"].is_mounted


def test_a_process_manager_that_explodes_still_lets_the_departure_through(
        data_root, monkeypatch):
    """`_running_game` is asked at the exact moment a disk is disappearing, and
    it must not be able to make that worse.

    The real function is exercised, not a double: patching `_running_game`
    itself would replace the very try/except under test and the assertion would
    be about the patch. The process manager is what breaks here.
    """
    import backend.services.process_manager as pm

    ws = FakeWs()
    live = poll({}, ws, scripted(lsblk({"mountpoint": "/run/media/gc/ROMS"})))
    ws.sent.clear()

    class Exploding:
        @property
        def current_game(self):
            raise RuntimeError("process manager is mid-restart")

    monkeypatch.setattr(pm, "process_manager", Exploding())
    assert storage_monitor._running_game() is None

    poll(live, ws, scripted(json.dumps({"blockdevices": []})))
    assert ws.events() == ["storage:removed"]
    assert not (data_root / "volumes" / "roms").exists()
