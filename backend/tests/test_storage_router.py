"""The storage endpoints, and the one that matters: Eject.

Listing is the easy half. Pulling a disk with unwritten data is how a save is
lost, and "has it finished writing" is not a question anyone can answer by
looking at it — which is why there is a button rather than a paragraph asking
players to be careful.

Nothing here runs udisksctl: `storage.mount`/`storage.unmount` are replaced, and
what is asserted is the status code the UI has to act on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import storage                              # noqa: E402


def volume(**over) -> storage.Volume:
    base = dict(name="sdb1", path="/dev/sdb1", label="ROMS", uuid="A4E2",
                fstype="ext4", mountpoint="/run/media/gc/ROMS",
                removable=True, size="1T")
    return storage.Volume(**{**base, **over})


@pytest.fixture
def client():
    from backend import main
    with TestClient(main.app) as c:
        yield c


@pytest.mark.linux_until_port(5)
def test_the_listing_describes_what_a_romspath_should_point_at(client, monkeypatch):
    """`stable_path`, never `mountpoint`. A library recorded against the real
    mount point scans nothing the day the disk is replugged."""
    monkeypatch.setattr(storage, "list_volumes", lambda *a, **k: [volume()])
    body = client.get("/api/storage/volumes").json()
    assert body["ok"]
    row = body["volumes"][0]
    assert row["stable_path"].endswith("/volumes/roms")
    assert row["mounted"] is True


def test_a_disk_pulled_between_the_screen_and_the_button_is_a_404(client, monkeypatch):
    """Genuinely routine on a box someone is plugging things into. A 500 would
    read as a bug in the box rather than a cable that moved."""
    monkeypatch.setattr(storage, "list_volumes", lambda *a, **k: [])
    r = client.post("/api/storage/unmount", json={"device": "/dev/sdb1"})
    assert r.status_code == 404


def test_ejecting_a_busy_disk_passes_udisks_own_words_through(client, monkeypatch):
    """"target is busy" is actionable — a game is still reading it. Replacing it
    with a generic failure leaves the player nothing to act on. 409, not 500:
    the box is fine, the disk is in use."""
    monkeypatch.setattr(storage, "list_volumes", lambda *a, **k: [volume()])
    monkeypatch.setattr(storage, "unmount",
                        lambda *a, **k: (False, "Error unmounting: target is busy"))
    r = client.post("/api/storage/unmount", json={"device": "/dev/sdb1"})
    assert r.status_code == 409
    assert "busy" in r.json()["detail"]


def test_a_clean_eject_reports_ok(client, monkeypatch):
    monkeypatch.setattr(storage, "list_volumes", lambda *a, **k: [volume()])
    monkeypatch.setattr(storage, "unmount", lambda *a, **k: (True, "Unmounted /dev/sdb1"))
    r = client.post("/api/storage/unmount", json={"device": "/dev/sdb1"})
    assert r.status_code == 200 and r.json()["ok"]


def test_the_device_is_addressed_by_path_and_not_by_row_number(client, monkeypatch):
    """A disk arriving while the screen is open renumbers the list. An Eject
    that acted on "the third row" would then detach the wrong disk."""
    ejected = []
    monkeypatch.setattr(storage, "list_volumes", lambda *a, **k: [
        volume(name="sda1", path="/dev/sda1", label="A"),
        volume(name="sdb1", path="/dev/sdb1", label="B"),
    ])
    monkeypatch.setattr(storage, "unmount",
                        lambda v, *a, **k: (ejected.append(v.label), (True, ""))[1])
    client.post("/api/storage/unmount", json={"device": "/dev/sdb1"})
    assert ejected == ["B"]
