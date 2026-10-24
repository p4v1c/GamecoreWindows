"""The peripherals that are not SDL gamepads.

Every test here hands over its own inventory (`vid:pid → name`) or PnP rows,
and its own packs. Nothing reads Windows' device list (but the one test that
says so) and nothing reads the shipped catalogue by id: this suite has to give
the same answer on a PC with a GameCube adapter plugged in and on CI.
"""
from __future__ import annotations

import pytest

from backend.services import usb_devices
from backend.services.platform import usb as platform_usb


# ── doubles ──────────────────────────────────────────────────────────────────

class FakePack:
    """Just enough Pack for usb_devices: an id, a data dict, an order."""

    def __init__(self, pack_id: str, usb: list[dict] | None = None, order: int = 0):
        self.id = pack_id
        self.data = {"label": pack_id.upper(), "order": order}
        if usb is not None:
            self.data["usb"] = usb


ADAPTER = {
    "vidPid": "057e:0337",
    "class": "adapter",
    "label": "GameCube adapter",
    "udevRule": 'ATTRS{idVendor}=="057e", ATTRS{idProduct}=="0337", MODE="0666"',
    "note": "Check the switch is on Wii U.",
}


# ── inventory ────────────────────────────────────────────────────────────────

def test_pnp_rows_become_one_entry_per_device():
    """A composite device's interfaces (`&MI_00`) carry the same pair: one entry."""
    rows = [{"DeviceID": "USB\\VID_057E&PID_0337&MI_00\\6&1", "Name": ""},
            {"DeviceID": "USB\\VID_057E&PID_0337\\5&2", "Name": "GC Adapter"},
            {"DeviceID": "USB\\ROOT_HUB30\\4&29", "Name": "Root hub"},
            {"DeviceID": "HID\\VID_046D&PID_C52B", "Name": "not on the USB list"},
            {"DeviceID": "USB\\VID_046D&PID_C52B\\7&3", "Name": "Receiver"}]
    assert platform_usb.parse_devices(rows) == {"057e:0337": "GC Adapter",
                                                "046d:c52b": "Receiver"}


def test_two_identical_adapters_are_one_entry():
    rows = [{"DeviceID": "USB\\VID_057E&PID_0337\\1", "Name": "GC Adapter"},
            {"DeviceID": "USB\\VID_057E&PID_0337\\2", "Name": "GC Adapter"}]
    assert platform_usb.parse_devices(rows) == {"057e:0337": "GC Adapter"}


def test_inventory_survives_a_device_list_windows_refuses(monkeypatch):
    """The settings screen must draw "nothing detected", not raise on the way."""
    def refuse():
        raise OSError("WMI refused")
    monkeypatch.setattr(platform_usb, "inventory", refuse)
    assert usb_devices.inventory() == {}


def test_the_real_device_list_reads_without_changing_anything():
    assert all(len(k) == 9 and k[4] == ":" for k in platform_usb.inventory())


# ── presence, the case this whole module exists for ──────────────────────────

def test_declared_adapter_present_is_reported_present():
    packs = {"gc": FakePack("gc", [ADAPTER])}
    rows = usb_devices.report(packs=packs, present={"057e:0337": "GC Adapter"})
    assert [r["status"] for r in rows] == [usb_devices.PRESENT]
    assert rows[0]["detected_as"] == "GC Adapter"
    assert rows[0]["class"] == "adapter"


def test_declared_adapter_absent_is_reported_absent_with_the_packs_note():
    packs = {"gc": FakePack("gc", [ADAPTER])}
    rows = usb_devices.report(packs=packs, present={"046d:c52b": "Something else"})
    assert [r["status"] for r in rows] == [usb_devices.ABSENT]
    # The pack's own words, not a generic "device not found" — the note is the
    # only part of the row that tells the owner what to actually do.
    assert rows[0]["note"] == ADAPTER["note"]
    assert rows[0]["detected_as"] == ""


def test_a_pack_declaring_nothing_contributes_no_rows():
    """Most packs declare no `usb`, and they must draw no heading at all."""
    packs = {"plain": FakePack("plain")}
    assert usb_devices.report(packs=packs, present={}) == []


def test_an_unknown_class_is_listed_rather_than_crashing():
    """A pack from a newer catalogue naming a sixth class.

    The schema stops a SHIPPED pack from inventing one, but config/catalog.d/
    is data the operator wrote and the OTA tier carries packs this release has
    never seen. Listing it as unknown keeps the device — and its note — on
    screen; a KeyError here would take the whole controllers screen down for
    one unrecognised word.
    """
    packs = {"future": FakePack("future", [{**ADAPTER, "class": "dancemat"}])}
    rows = usb_devices.report(packs=packs, present={})
    assert len(rows) == 1
    assert rows[0]["class"] == usb_devices.UNKNOWN
    assert rows[0]["note"] == ADAPTER["note"]


def test_report_is_in_catalogue_order():
    packs = {
        "late": FakePack("late", [{**ADAPTER, "vidPid": "0001:0001"}], order=9),
        "early": FakePack("early", [{**ADAPTER, "vidPid": "0002:0002"}], order=1),
    }
    rows = usb_devices.report(packs=packs, present={})
    assert [r["system_id"] for r in rows] == ["early", "late"]


# ── vid:pid parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("057E:0337", "057e:0337"),      # a udev rule written in the usual upper case
    (" 057e:0337 ", "057e:0337"),
    ("057e:0337", "057e:0337"),
    ("57e:337", ""),                 # unpadded — not what Windows writes
    ("057e", ""),
    ("", ""),
    ("zzzz:0337", ""),
])
def test_vid_pid_is_normalised_to_one_spelling(raw, expected):
    assert usb_devices.normalize_vid_pid(raw) == expected


def test_case_does_not_decide_whether_a_device_is_found():
    """A pack that spelled the id in upper case must still match Windows' list.

    The schema pins lowercase, so this cannot happen from a shipped pack — but
    an operator's local pack is hand-written, and "your adapter is not detected"
    because of letter case is unfalsifiable from a sofa.
    """
    packs = {"gc": FakePack("gc", [{**ADAPTER, "vidPid": "057E:0337"}])}
    assert usb_devices.report(packs=packs, present={"057e:0337": "GC Adapter"}
                              )[0]["status"] == usb_devices.PRESENT


# ── the launch notice ────────────────────────────────────────────────────────

def test_launch_notice_names_the_device_and_repeats_the_note():
    packs = {"gc": FakePack("gc", [ADAPTER])}
    notice = usb_devices.launch_notice("gc", packs=packs, present={})
    assert "GameCube adapter" in notice
    assert "057e:0337" in notice
    assert ADAPTER["note"] in notice


def test_launch_notice_is_empty_when_the_device_is_there():
    packs = {"gc": FakePack("gc", [ADAPTER])}
    assert usb_devices.launch_notice("gc", packs=packs,
                                     present={"057e:0337": "GC Adapter"}) == ""


def test_launch_notice_is_empty_for_a_system_that_declares_nothing():
    packs = {"plain": FakePack("plain")}
    assert usb_devices.launch_notice("plain", packs=packs,
                                     present={}) == ""


def test_launch_notice_is_empty_for_an_unknown_system():
    assert usb_devices.launch_notice("nope", packs={},
                                     present={}) == ""


def test_a_broken_pack_costs_a_notice_and_never_a_launch():
    """`note` missing — a malformed local pack the schema never saw.

    The rule this module inherits from bios.py: a check that cannot run must
    cost the player a missing sentence, never a game that does not start.
    """
    packs = {"bad": FakePack("bad", [{"vidPid": "057e:0337", "class": "adapter"}])}
    assert usb_devices.launch_notice("bad", packs=packs,
                                     present={}) == ""
    assert usb_devices.report(packs=packs, present={}) == []


# ── what the installer is handed ─────────────────────────────────────────────

def test_udev_rules_carry_the_declared_line_and_name_the_device():
    lines = usb_devices.udev_rules(FakePack("gc", [ADAPTER]))
    assert ADAPTER["udevRule"] in lines
    # A rules file nobody can trace back to a pack is a rules file nobody dares
    # delete. The comment names the pack and the device.
    assert any(line.startswith("# gc:") and "GameCube adapter" in line
               for line in lines)


def test_a_device_needing_no_rule_produces_none():
    """Plenty of peripherals work on the kernel's defaults, and an unnecessary
    rule is a permission widened for nothing."""
    no_rule = {k: v for k, v in ADAPTER.items() if k != "udevRule"}
    assert usb_devices.udev_rules(FakePack("gc", [no_rule])) == []


def test_declares_usb_is_what_asks_for_the_udev_re_fire():
    assert usb_devices.declares_usb(FakePack("gc", [ADAPTER])) is True
    assert usb_devices.declares_usb(FakePack("plain")) is False
