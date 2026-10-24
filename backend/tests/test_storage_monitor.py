"""Reacting to a disk plugged in or pulled out: links and broadcasts.

`platform/volumes.py` is faked (`disks`); each drive's root is a `tmp_path`
folder, and the stable link is a real junction to it.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.services import paths, storage, storage_monitor
from backend.services.platform import filesystem
from backend.tests.fake_volumes import row


def poll(was, ws):
    """One comparison pass, run to completion from a sync test.

    `asyncio.run` and not a pytest-asyncio marker: CI installs only
    `requirements.txt` + `pytest`, where such a marker would run nothing.
    """
    return asyncio.run(storage_monitor.poll_once(was, ws))


class FakeWs:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, event, data):
        self.sent.append((event, data))

    def events(self) -> list[str]:
        return [e for e, _ in self.sent]

    def payload(self, event: str) -> dict:
        return next(d for e, d in self.sent if e == event)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "GAMECORE_DATA", tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def drive(tmp_path):
    root = tmp_path / "E-drive"
    root.mkdir()
    return root


@pytest.fixture
def no_game(monkeypatch):
    monkeypatch.setattr(storage_monitor, "_running_game", lambda: None)


def running(monkeypatch, rom_path: str, game_key: str = "Zelda.iso"):
    monkeypatch.setattr(storage_monitor, "_running_game",
                        lambda: {"game_key": game_key, "system_id": "gc", "rom_path": rom_path})


# ── a disk arrives ───────────────────────────────────────────────────────────

def test_a_disk_plugged_in_is_linked_and_announced(data_root, drive, disks, no_game):
    ws = FakeWs()
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    assert ws.events() == ["storage:mounted"]
    assert filesystem.link_target(data_root / "volumes" / "roms") == drive
    assert ws.payload("storage:mounted")["stable_path"] == str(data_root / "volumes" / "roms")
    assert live["E:"].is_mounted


def test_the_same_disk_on_another_letter_repoints_its_link(data_root, drive, disks, no_game,
                                                           tmp_path):
    ws = FakeWs()
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    disks.rows = []
    live = poll(live, ws)
    other = tmp_path / "F-drive"
    other.mkdir()
    disks.rows = [row(name="F:", path="F:", mountpoint=str(other))]
    poll(live, ws)
    assert filesystem.link_target(data_root / "volumes" / "roms") == other


def test_a_disk_with_no_drive_letter_says_so_once(data_root, disks, no_game):
    ws = FakeWs()
    disks.rows = [row(mountpoint="")]
    live = poll({}, ws)
    assert ws.events() == ["storage:failed"]
    assert not (data_root / "volumes").exists()
    poll(live, ws)
    assert ws.events() == ["storage:failed"]


def test_nothing_happens_twice_for_a_disk_that_has_not_changed(data_root, drive, disks, no_game):
    ws = FakeWs()
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    ws.sent.clear()
    poll(live, ws)
    assert ws.events() == [] and disks.ejected == []


# ── a disk leaves ────────────────────────────────────────────────────────────

def test_a_disk_pulled_out_is_announced_and_its_link_dropped(data_root, drive, disks, no_game):
    ws = FakeWs()
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    ws.sent.clear()
    disks.rows = []
    poll(live, ws)
    assert ws.events() == ["storage:removed"]
    assert not (data_root / "volumes" / "roms").exists()
    assert drive.is_dir(), "the disk's own folder went with the link"


def test_a_disk_that_was_never_usable_leaving_is_not_news(data_root, disks, no_game):
    ws = FakeWs()
    was = {"E:": storage.Volume(**row(mountpoint=""))}
    poll(was, ws)
    assert ws.events() == []


# ── the disk pulled out mid-game ─────────────────────────────────────────────

def test_a_disk_removed_during_a_game_is_called_out_by_name(data_root, drive, disks,
                                                            monkeypatch):
    ws = FakeWs()
    monkeypatch.setattr(storage_monitor, "_running_game", lambda: None)
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    ws.sent.clear()
    running(monkeypatch, str(drive / "gamecube" / "Zelda.iso"))
    disks.rows = []
    poll(live, ws)
    lost = ws.payload("storage:lost")
    assert lost["game_key"] == "Zelda.iso" and "save" in lost["detail"].lower()
    assert "storage:removed" in ws.events()


def test_a_game_running_from_the_internal_disk_is_not_called_out(data_root, drive, disks,
                                                                 monkeypatch):
    ws = FakeWs()
    monkeypatch.setattr(storage_monitor, "_running_game", lambda: None)
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    ws.sent.clear()
    running(monkeypatch, "C:\\Users\\gc\\Documents\\GameCore\\gamecube\\Zelda.iso")
    disks.rows = []
    poll(live, ws)
    assert ws.events() == ["storage:removed"]


# ── the loop must survive everything ─────────────────────────────────────────

def test_a_websocket_that_throws_does_not_stop_disks_being_noticed(data_root, drive, disks,
                                                                   no_game):
    class BrokenWs:
        async def broadcast(self, event, data):
            raise RuntimeError("no listeners")

    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, BrokenWs())
    assert filesystem.is_link(data_root / "volumes" / "roms")
    assert live["E:"].is_mounted


def test_a_process_manager_that_explodes_still_lets_the_departure_through(
        data_root, drive, disks, monkeypatch):
    import backend.services.process_manager as pm

    ws = FakeWs()
    disks.rows = [row(mountpoint=str(drive))]
    live = poll({}, ws)
    ws.sent.clear()

    class Exploding:
        @property
        def current_game(self):
            raise RuntimeError("process manager is mid-restart")

    monkeypatch.setattr(pm, "process_manager", Exploding())
    assert storage_monitor._running_game() is None
    disks.rows = []
    poll(live, ws)
    assert ws.events() == ["storage:removed"]
    assert not (data_root / "volumes" / "roms").exists()
