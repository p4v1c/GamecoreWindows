"""What a launch does about a peripheral that is not there.

The rule, and it is the opposite of the BIOS gate's: a declared USB accessory
that is absent is SAID and the game starts anyway. Dolphin plays perfectly with
a DualShock 4 and no GameCube adapter, so refusing would invent a fault — while
saying nothing leaves the owner unable to tell an unplugged adapter from one the
sandbox cannot see.

The harness is the BIOS suite's: a system whose binary does not exist, so the
launch always ends at the exec step with 503. That is precisely what makes it
useful here — reaching 503 means nothing upstream refused, which is the whole
assertion.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import usb_devices                          # noqa: E402

_GHOST = {"id": "testpack", "label": "Test System", "kind": "emulator",
          "path": r"C:\definitely\not-installed.exe", "args": "", "romsPath": ""}

ADAPTER = {"vidPid": "057e:0337", "class": "adapter",
           "label": "GameCube adapter", "note": "Check the switch is on Wii U."}


class FakePack:
    def __init__(self, usb):
        self.id = "testpack"
        self.data = {"label": "Test System", "order": 0, "usb": usb}


@pytest.fixture
def launcher(monkeypatch):
    from backend import main
    from backend.services import systems as systems_service

    monkeypatch.setattr(systems_service, "list_all", lambda: [_GHOST])
    with TestClient(main.app) as client:
        yield client


def _declaring(monkeypatch, usb):
    monkeypatch.setattr(usb_devices, "load_catalog",
                        lambda *a, **k: {"testpack": FakePack(usb)})


def _no_devices(monkeypatch):
    """An empty USB bus, whatever is really plugged into the machine running
    this. A suite that read the real /sys would pass or fail by what the
    developer happened to have in a port."""
    monkeypatch.setattr(usb_devices, "inventory", lambda *a, **k: {})


def _adapter_present(monkeypatch):
    monkeypatch.setattr(usb_devices, "inventory",
                        lambda *a, **k: {"057e:0337": "GC Adapter"})


def test_a_missing_accessory_does_not_refuse_the_launch(launcher, monkeypatch):
    """503 is the exec step, i.e. nothing upstream said no.

    A 424 here would mean the box had started refusing to play Dolphin because
    an optional adapter is not plugged in.
    """
    _declaring(monkeypatch, [ADAPTER])
    _no_devices(monkeypatch)
    r = launcher.post("/api/games/launch", json={"system_id": "testpack"})
    assert r.status_code == 503, r.text


def test_a_missing_accessory_is_broadcast_so_the_player_reads_it(launcher, monkeypatch):
    """Not refusing must not become not mentioning. This is the sentence that
    replaces the phone call."""
    sent = []

    from backend import ws
    async def capture(event, data):
        sent.append((event, data))
    monkeypatch.setattr(ws, "broadcast", capture)

    _declaring(monkeypatch, [ADAPTER])
    _no_devices(monkeypatch)
    launcher.post("/api/games/launch", json={"system_id": "testpack"})

    notices = [d for e, d in sent if e == "game:notice"]
    assert notices, f"no game:notice was broadcast — events were {[e for e, _ in sent]}"
    assert "GameCube adapter" in notices[0]["detail"]
    assert ADAPTER["note"] in notices[0]["detail"]


def test_nothing_is_said_when_the_accessory_is_plugged_in(launcher, monkeypatch):
    """A working box must stay silent. A notice on every launch is noise the
    owner learns to ignore, which costs the one that mattered."""
    sent = []

    from backend import ws
    async def capture(event, data):
        sent.append((event, data))
    monkeypatch.setattr(ws, "broadcast", capture)

    _declaring(monkeypatch, [ADAPTER])
    _adapter_present(monkeypatch)
    launcher.post("/api/games/launch", json={"system_id": "testpack"})

    assert [e for e, _ in sent if e == "game:notice"] == []


def test_a_system_declaring_nothing_says_nothing(launcher, monkeypatch):
    sent = []

    from backend import ws
    async def capture(event, data):
        sent.append((event, data))
    monkeypatch.setattr(ws, "broadcast", capture)

    monkeypatch.setattr(usb_devices, "load_catalog", lambda *a, **k: {})
    _no_devices(monkeypatch)
    launcher.post("/api/games/launch", json={"system_id": "testpack"})

    assert [e for e, _ in sent if e == "game:notice"] == []


def test_a_check_that_explodes_does_not_stop_the_launch(launcher, monkeypatch):
    """Same guarantee the BIOS gate documents. A check that cannot run must
    cost a sentence, never a game."""
    def boom(*_a, **_k):
        raise OSError("sysfs went away")

    monkeypatch.setattr(usb_devices, "load_catalog", boom)
    r = launcher.post("/api/games/launch", json={"system_id": "testpack"})
    assert r.status_code == 503, r.text
