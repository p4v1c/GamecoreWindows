"""/api/sysinfo, with every probe failing.

This endpoint feeds the TopBar, which is on screen the whole time the box is
on. Every value in it comes from something that can be absent: a box with no
controller paired has no battery, a box with no network has no route to
8.8.8.8, and /sys/class/power_supply on a machine with no power supplies at all
is an empty directory rather than a missing one.

None of that is exotic — it is a freshly installed box before the first pad is
paired. The failure it must never produce is a 500, because the TopBar polls
this and a home screen that cannot draw its own header looks like a box that
did not boot.

Nothing here reads the real /sys: the probes are pointed at a temporary tree,
so the result does not depend on whether the machine running the suite happens
to have a laptop battery.
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
from backend.services import battery            # noqa: E402

_KEYS = {"ip", "storage_used_gb", "storage_total_gb", "storage_free_gb",
         "version", "controllers", "bios"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_sysfs(monkeypatch, tmp_path):
    """Point read_batteries at a power-supply tree this test owns.

    The real path is a literal inside read_batteries, so the seam is `glob`
    itself: the pattern is passed through and answered from tmp_path.
    """
    root = tmp_path / "power_supply"
    root.mkdir()

    def supply(name: str, **files: str) -> Path:
        d = root / name
        d.mkdir()
        for filename, content in files.items():
            (d / filename).write_text(content, encoding="utf-8")
        return d

    monkeypatch.setattr(battery.glob, "glob",
                        lambda pattern: sorted(str(p) for p in root.iterdir()))
    return supply


# ── the whole response, when nothing is there ──────────────────────────────

def test_the_header_still_draws_on_a_box_with_no_controller(client, fake_sysfs):
    """No pad paired: an empty list, a 200, and every key present."""
    body = client.get("/api/sysinfo").json()
    assert set(body) == _KEYS
    assert body["controllers"] == []
    assert isinstance(body["storage_total_gb"], float)


def test_a_box_with_no_network_reports_a_dash_rather_than_failing(client,
                                                                  monkeypatch,
                                                                  fake_sysfs):
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
    body = client.get("/api/sysinfo").json()
    assert body["ip"] == "—"
    assert set(body) == _KEYS


def test_no_power_supply_directory_at_all_is_not_an_error(client, monkeypatch):
    """A container, a VM, a board with no power management: the glob answers
    nothing and that is a legitimate reading, not a broken probe."""
    monkeypatch.setattr(battery.glob, "glob", lambda pattern: [])
    body = client.get("/api/sysinfo").json()
    assert body["controllers"] == []
    assert set(body) == _KEYS


# ── one probe at a time ────────────────────────────────────────────────────

@pytest.mark.linux_until_port(5)
def test_a_laptop_battery_is_not_a_controller(client, fake_sysfs):
    """This endpoint is asked what PADS are charged.

    The install target is a mini PC, but the project is developed on laptops
    and the same code runs there — a BAT0 pill labelled as a controller is a
    wrong reading, not a harmless one.
    """
    fake_sysfs("BAT0", capacity="87", status="Discharging")
    fake_sysfs("sony_controller_battery_a4:ae", capacity="40", status="Discharging")

    names = [c["name"] for c in client.get("/api/sysinfo").json()["controllers"]]
    assert names == ["sony_controller_battery_a4:ae"]


def test_a_supply_with_no_capacity_file_is_skipped(client, fake_sysfs):
    """Not every power_supply entry has a level — a mains adapter has none."""
    fake_sysfs("some_supply", status="Full")
    fake_sysfs("wireless_controller", capacity="55", status="Charging")

    controllers = client.get("/api/sysinfo").json()["controllers"]
    assert [c["name"] for c in controllers] == ["wireless_controller"]
    assert controllers[0]["charging"] is True


def test_a_capacity_the_kernel_wrote_badly_is_skipped_not_fatal(client, fake_sysfs):
    """An unplugged pad can leave an empty or non-numeric capacity behind.

    One unreadable pad must cost that pad's pill and nothing else — the others
    are still on the header.
    """
    fake_sysfs("bad_pad", capacity="", status="Unknown")
    fake_sysfs("worse_pad", capacity="not a number", status="Unknown")
    fake_sysfs("good_pad", capacity="72", status="Discharging")

    controllers = client.get("/api/sysinfo").json()["controllers"]
    assert [c["name"] for c in controllers] == ["good_pad"]
    assert controllers[0]["level"] == 72
    assert controllers[0]["charging"] is False


def test_a_missing_status_file_reads_as_not_charging(client, fake_sysfs):
    """Absent is a legitimate reading of "we do not know", and the honest
    answer for an unknown charge state is "not charging" — a pad shown as
    charging when it is not is the reading that lets a battery run flat."""
    fake_sysfs("quiet_pad", capacity="33")

    controllers = client.get("/api/sysinfo").json()["controllers"]
    assert controllers[0]["charging"] is False
    assert controllers[0]["label"] == "quiet_pad"


@pytest.mark.linux_until_port(5)
def test_a_model_name_is_preferred_over_the_sysfs_directory_name(client, fake_sysfs):
    """The directory name embeds the pad's MAC. It is what joins a battery to
    a player slot, so it is kept — but it is not what a person should read."""
    fake_sysfs("sony_controller_battery_a4:ae:11", capacity="90",
               status="Discharging", model_name="DualSense Wireless Controller")

    controller = client.get("/api/sysinfo").json()["controllers"][0]
    assert controller["label"] == "DualSense Wireless Controller"
    assert controller["name"] == "sony_controller_battery_a4:ae:11"


def test_every_controller_entry_has_the_shape_the_topbar_reads(client, fake_sysfs):
    """The pill renders these five fields. A missing one is an undefined in the
    UI, which draws as a blank pill rather than as an error."""
    fake_sysfs("a_pad", capacity="50", status="Discharging")

    controller = client.get("/api/sysinfo").json()["controllers"][0]
    assert set(controller) == {"name", "label", "player", "level", "charging"}
