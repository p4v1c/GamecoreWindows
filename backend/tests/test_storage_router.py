"""The storage endpoints, and the one that matters: Eject.

Pulling a disk with unwritten data is how a save is lost, which is why there
is a button rather than a paragraph asking players to be careful. Nothing here
ejects a real disk: `platform/volumes.py` is faked (`disks`).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import storage as storage_router
from backend.services import paths
from backend.tests.fake_volumes import row


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "GAMECORE_DATA", tmp_path / "data")
    app = FastAPI()
    app.include_router(storage_router.router, prefix="/api")
    return TestClient(app)


def test_the_listing_describes_what_a_romspath_should_point_at(client, disks, tmp_path):
    """`stable_path`, never the drive letter: the letter changes when another
    device is plugged first."""
    disks.rows = [row()]
    body = client.get("/api/storage/volumes").json()
    assert body["ok"]
    listed = body["volumes"][0]
    assert listed["stable_path"] == str(tmp_path / "data" / "volumes" / "roms")
    assert listed["device"] == "E:" and listed["mounted"] is True


def test_a_disk_pulled_between_the_screen_and_the_button_is_a_404(client, disks):
    assert client.post("/api/storage/unmount", json={"device": "E:"}).status_code == 404


def test_ejecting_a_busy_disk_passes_windows_reason_through(client, disks):
    """409, not 500: the PC is fine, the disk is in use."""
    disks.rows = [row()]
    disks.eject_result = (False, "E: is busy: a program is using it. Close what is "
                                 "reading it, then eject again.")
    r = client.post("/api/storage/unmount", json={"device": "E:"})
    assert r.status_code == 409 and "busy" in r.json()["detail"]


def test_a_clean_eject_reports_ok(client, disks):
    disks.rows = [row()]
    r = client.post("/api/storage/unmount", json={"device": "E:"})
    assert r.status_code == 200 and r.json() == {"ok": True, "detail": "Safe to unplug"}


def test_the_device_is_addressed_by_drive_and_not_by_row_number(client, disks):
    disks.rows = [row(label="A"), row(name="F:", path="F:", label="B", mountpoint="F:\\")]
    client.post("/api/storage/unmount", json={"device": "F:"})
    assert disks.ejected == ["F:"]


def test_mounting_a_disk_windows_already_mounted_answers_its_drive(client, disks):
    disks.rows = [row()]
    assert client.post("/api/storage/mount", json={"device": "E:"}).json() == \
        {"ok": True, "detail": "E:\\"}
