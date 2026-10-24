"""/api/sysinfo, with every probe failing.

This endpoint feeds the TopBar, which is on screen the whole time the box is
on. Every value in it comes from something that can be absent: a box with no
controller paired has no battery, a box with no network has no route to
8.8.8.8, and Windows may refuse the adapter list.

None of that is exotic — it is a freshly installed box before the first pad is
paired. The failure it must never produce is a 500, because the TopBar polls
this and a home screen that cannot draw its own header looks like a box that
did not boot.

Nothing here reads the real pads: SDL's snapshot is replaced with a list the
test fills, so the result does not depend on what the machine running the
suite has plugged in.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.main import app                    # noqa: E402
from backend.services.platform import gamepads as platform_gamepads  # noqa: E402
from backend.services.platform import network as platform_network    # noqa: E402

_KEYS = {"ip", "storage_used_gb", "storage_total_gb", "storage_free_gb",
         "version", "controllers", "bios"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_pads(monkeypatch):
    """SDL's snapshot, as a list; `add(name, power)` connects a pad."""
    pads: list = []
    monkeypatch.setattr(platform_gamepads, "pads", lambda: list(pads))

    def add(name: str, power: int = 2, is_pad: bool = True, device: str = ""):
        pads.append(platform_gamepads.PadInfo(
            device=device or f"sdl{len(pads)}", name=name, vendor="054c", product="09cc",
            guid="0" * 32, serial="", bus=5, is_pad=is_pad, has_guide=True, power=power))
    return add


# ── the whole response, when nothing is there ──────────────────────────────

def test_the_header_still_draws_on_a_box_with_no_controller(client, fake_pads):
    """No pad paired: an empty list, a 200, and every key present."""
    body = client.get("/api/sysinfo").json()
    assert set(body) == _KEYS
    assert body["controllers"] == []
    assert isinstance(body["storage_total_gb"], float)


def test_a_box_with_no_network_reports_a_dash_rather_than_failing(client,
                                                                  monkeypatch,
                                                                  fake_pads):
    """`connect()` to 8.8.8.8 needs no packet, but it does need a route.

    A box on a bench with the cable out has none, and that is a dash in the
    corner of the header — not a 500 that blanks the whole bar.
    """
    class _DeadSocket:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, *a):
            raise OSError("network is unreachable")

        def getsockname(self):
            raise AssertionError("not reached")

    # Replaced on the ROUTER's module, not on the stdlib one: asyncio builds
    # its own self-pipe out of socket.socketpair() while TestClient starts, so
    # a global patch takes the event loop down before the request is made.
    class _FakeSocketModule:
        AF_INET = socket.AF_INET
        SOCK_DGRAM = socket.SOCK_DGRAM
        socket = _DeadSocket

    from backend.routers import sysinfo
    monkeypatch.setattr(sysinfo, "socket", _FakeSocketModule)
    monkeypatch.setattr(platform_network, "adapters", lambda: [])   # cable out
    body = client.get("/api/sysinfo").json()
    assert body["ip"] == "—"
    assert set(body) == _KEYS


def test_windows_refusing_the_adapter_list_keeps_the_route_address(client, monkeypatch,
                                                                  fake_pads):
    def refuse():
        raise OSError("WMI refused")
    monkeypatch.setattr(platform_network, "adapters", refuse)
    body = client.get("/api/sysinfo").json()
    assert set(body) == _KEYS and body["ip"]


@pytest.mark.parametrize("route_ip, expected", [
    ("192.168.1.20", "192.168.1.20"),     # the route already leaves by the LAN
    ("10.8.0.2", "192.168.1.20"),         # a VPN owns the default route
    ("172.25.0.1", "192.168.1.20"),       # a Hyper-V switch
])
def test_the_lan_address_is_never_a_vpns_or_a_virtual_switchs(route_ip, expected):
    adapters = [
        {"name": "Wi-Fi", "kind": "wifi", "connected": True, "virtual": False,
         "ip": "192.168.1.40", "gateway": "192.168.1.1"},
        {"name": "Ethernet", "kind": "ethernet", "connected": True, "virtual": False,
         "ip": "192.168.1.20", "gateway": "192.168.1.1"},
        {"name": "vEthernet (WSL)", "kind": "ethernet", "connected": True, "virtual": True,
         "ip": "172.25.0.1", "gateway": ""},
    ]
    assert platform_network.pick_lan_ip(route_ip, adapters) == expected


def test_no_physical_adapter_keeps_the_route_address():
    assert platform_network.pick_lan_ip("10.0.0.5", []) == "10.0.0.5"


# ── one probe at a time ────────────────────────────────────────────────────

def test_a_wheel_is_not_a_controller_pill(client, fake_pads):
    fake_pads("Racing Wheel", is_pad=False)
    fake_pads("PS4 Controller")
    names = [c["label"] for c in client.get("/api/sysinfo").json()["controllers"]]
    assert names == ["PS4 Controller"]


def test_a_pad_with_no_known_level_is_skipped_not_fatal(client, fake_pads):
    """A wired pad, or one SDL cannot read: that pad's pill goes, nothing else."""
    fake_pads("Wired pad", power=4)
    fake_pads("Unknown pad", power=-1)
    fake_pads("Good pad", power=3)
    controllers = client.get("/api/sysinfo").json()["controllers"]
    assert [c["label"] for c in controllers] == ["Good pad"]
    assert controllers[0]["level"] == 100 and controllers[0]["charging"] is False


def test_every_controller_entry_has_the_shape_the_topbar_reads(client, fake_pads):
    """The pill renders these five fields. A missing one is an undefined in the
    UI, which draws as a blank pill rather than as an error."""
    fake_pads("A pad")

    controller = client.get("/api/sysinfo").json()["controllers"][0]
    assert set(controller) == {"name", "label", "player", "level", "charging"}
