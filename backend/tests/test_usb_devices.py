"""The peripherals that are not SDL gamepads.

Every test here builds its own sysfs tree under tmp_path and its own packs.
Nothing reads /sys and nothing reads the shipped catalogue by id: this suite
has to give the same answer on a box with a GameCube adapter plugged in and on
CI, and a test that named `packs["dolphin"]` would be asserting on catalogue
data rather than on the code that reads it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import usb_devices


# ── doubles ──────────────────────────────────────────────────────────────────

class FakePack:
    """Just enough Pack for usb_devices: an id, a data dict, an order."""

    def __init__(self, pack_id: str, usb: list[dict] | None = None, order: int = 0):
        self.id = pack_id
        self.data = {"label": pack_id.upper(), "order": order}
        if usb is not None:
            self.data["usb"] = usb


def sysfs_with(tmp_path: Path, devices: dict[str, str]) -> Path:
    """A /sys/bus/usb/devices lookalike holding `vid:pid → product name`.

    Also writes the interface nodes a real bus carries (`3-3:1.0`), because
    those are the ones that must NOT be counted — they have no idVendor, and an
    inventory that walked them would report every device several times.
    """
    # A fresh directory per call: a test that builds two buses (before and
    # after plugging something in) must not have the second one collide with
    # the first, and must not silently inherit its devices either.
    n_existing = len(list(tmp_path.glob("usb-devices*")))
    root = tmp_path / f"usb-devices{n_existing or ''}"
    root.mkdir()
    for n, (vid_pid, name) in enumerate(devices.items(), start=1):
        vendor, product = vid_pid.split(":")
        node = root / f"3-{n}"
        node.mkdir()
        (node / "idVendor").write_text(vendor + "\n")
        (node / "idProduct").write_text(product + "\n")
        if name:
            (node / "product").write_text(name + "\n")
        # The device's own interface — no idVendor, must be skipped.
        iface = root / f"3-{n}:1.0"
        iface.mkdir()
        (iface / "bInterfaceClass").write_text("03\n")
    return root


ADAPTER = {
    "vidPid": "057e:0337",
    "class": "adapter",
    "label": "GameCube adapter",
    "udevRule": 'ATTRS{idVendor}=="057e", ATTRS{idProduct}=="0337", MODE="0666"',
    "note": "Check the switch is on Wii U.",
}


# ── inventory ────────────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(5)
def test_inventory_reads_devices_and_skips_their_interfaces(tmp_path):
    sysfs = sysfs_with(tmp_path, {"057e:0337": "GC Adapter", "046d:c52b": "Receiver"})
    found = usb_devices.inventory(sysfs)
    assert found == {"057e:0337": "GC Adapter", "046d:c52b": "Receiver"}


def test_inventory_survives_a_sysfs_that_is_not_there(tmp_path):
    """A container, or a box whose /sys is not mounted. The settings screen
    must draw "nothing detected", not raise on the way to the grid."""
    assert usb_devices.inventory(tmp_path / "nope") == {}


@pytest.mark.linux_until_port(5)
def test_inventory_ignores_a_node_whose_ids_are_unreadable(tmp_path):
    """A device unplugged between the listdir and the read. Routine on a box
    someone is plugging things into, which is exactly when this runs."""
    sysfs = sysfs_with(tmp_path, {"057e:0337": "GC Adapter"})
    (sysfs / "3-9").mkdir()                      # no idVendor at all
    assert usb_devices.inventory(sysfs) == {"057e:0337": "GC Adapter"}


def test_two_identical_adapters_are_one_entry(tmp_path):
    """The roster question is "is this model on the bus". A second copy of the
    same adapter does not change the answer, and reporting it twice would put
    two identical lines on the controllers screen."""
    root = tmp_path / "usb"
    root.mkdir()
    for n in (1, 2):
        node = root / f"3-{n}"
        node.mkdir()
        (node / "idVendor").write_text("057e")
        (node / "idProduct").write_text("0337")
    assert usb_devices.inventory(root) == {"057e:0337": ""}


# ── presence, the case this whole module exists for ──────────────────────────

@pytest.mark.linux_until_port(5)
def test_declared_adapter_present_is_reported_present(tmp_path):
    packs = {"gc": FakePack("gc", [ADAPTER])}
    sysfs = sysfs_with(tmp_path, {"057e:0337": "GC Adapter"})
    rows = usb_devices.report(packs=packs, sysfs=sysfs)
    assert [r["status"] for r in rows] == [usb_devices.PRESENT]
    assert rows[0]["detected_as"] == "GC Adapter"
    assert rows[0]["class"] == "adapter"


@pytest.mark.linux_until_port(5)
def test_declared_adapter_absent_is_reported_absent_with_the_packs_note(tmp_path):
    packs = {"gc": FakePack("gc", [ADAPTER])}
    sysfs = sysfs_with(tmp_path, {"046d:c52b": "Something else"})
    rows = usb_devices.report(packs=packs, sysfs=sysfs)
    assert [r["status"] for r in rows] == [usb_devices.ABSENT]
    # The pack's own words, not a generic "device not found" — the note is the
    # only part of the row that tells the owner what to actually do.
    assert rows[0]["note"] == ADAPTER["note"]
    assert rows[0]["detected_as"] == ""


def test_a_pack_declaring_nothing_contributes_no_rows(tmp_path):
    """Most packs declare no `usb`, and they must draw no heading at all."""
    packs = {"plain": FakePack("plain")}
    assert usb_devices.report(packs=packs, sysfs=sysfs_with(tmp_path, {})) == []


def test_an_unknown_class_is_listed_rather_than_crashing(tmp_path):
    """A pack from a newer catalogue naming a sixth class.

    The schema stops a SHIPPED pack from inventing one, but config/catalog.d/
    is data the operator wrote and the OTA tier carries packs this release has
    never seen. Listing it as unknown keeps the device — and its note — on
    screen; a KeyError here would take the whole controllers screen down for
    one unrecognised word.
    """
    packs = {"future": FakePack("future", [{**ADAPTER, "class": "dancemat"}])}
    rows = usb_devices.report(packs=packs, sysfs=sysfs_with(tmp_path, {}))
    assert len(rows) == 1
    assert rows[0]["class"] == usb_devices.UNKNOWN
    assert rows[0]["note"] == ADAPTER["note"]


def test_report_is_in_catalogue_order(tmp_path):
    packs = {
        "late": FakePack("late", [{**ADAPTER, "vidPid": "0001:0001"}], order=9),
        "early": FakePack("early", [{**ADAPTER, "vidPid": "0002:0002"}], order=1),
    }
    rows = usb_devices.report(packs=packs, sysfs=sysfs_with(tmp_path, {}))
    assert [r["system_id"] for r in rows] == ["early", "late"]


# ── vid:pid parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("057E:0337", "057e:0337"),      # a udev rule written in the usual upper case
    (" 057e:0337 ", "057e:0337"),
    ("057e:0337", "057e:0337"),
    ("57e:337", ""),                 # unpadded — not what sysfs writes
    ("057e", ""),
    ("", ""),
    ("zzzz:0337", ""),
])
def test_vid_pid_is_normalised_to_one_spelling(raw, expected):
    assert usb_devices.normalize_vid_pid(raw) == expected


@pytest.mark.linux_until_port(5)
def test_case_does_not_decide_whether_a_device_is_found(tmp_path):
    """A pack that spelled the id in upper case must still match sysfs.

    The schema pins lowercase, so this cannot happen from a shipped pack — but
    an operator's local pack is hand-written, and "your adapter is not detected"
    because of letter case is unfalsifiable from a sofa.
    """
    packs = {"gc": FakePack("gc", [{**ADAPTER, "vidPid": "057E:0337"}])}
    sysfs = sysfs_with(tmp_path, {"057e:0337": "GC Adapter"})
    assert usb_devices.report(packs=packs, sysfs=sysfs)[0]["status"] == usb_devices.PRESENT


# ── the launch notice ────────────────────────────────────────────────────────

def test_launch_notice_names_the_device_and_repeats_the_note(tmp_path):
    packs = {"gc": FakePack("gc", [ADAPTER])}
    notice = usb_devices.launch_notice("gc", packs=packs, sysfs=sysfs_with(tmp_path, {}))
    assert "GameCube adapter" in notice
    assert "057e:0337" in notice
    assert ADAPTER["note"] in notice


@pytest.mark.linux_until_port(5)
def test_launch_notice_is_empty_when_the_device_is_there(tmp_path):
    packs = {"gc": FakePack("gc", [ADAPTER])}
    sysfs = sysfs_with(tmp_path, {"057e:0337": "GC Adapter"})
    assert usb_devices.launch_notice("gc", packs=packs, sysfs=sysfs) == ""


def test_launch_notice_is_empty_for_a_system_that_declares_nothing(tmp_path):
    packs = {"plain": FakePack("plain")}
    assert usb_devices.launch_notice("plain", packs=packs,
                                     sysfs=sysfs_with(tmp_path, {})) == ""


def test_launch_notice_is_empty_for_an_unknown_system(tmp_path):
    assert usb_devices.launch_notice("nope", packs={},
                                     sysfs=sysfs_with(tmp_path, {})) == ""


def test_a_broken_pack_costs_a_notice_and_never_a_launch(tmp_path):
    """`note` missing — a malformed local pack the schema never saw.

    The rule this module inherits from bios.py: a check that cannot run must
    cost the player a missing sentence, never a game that does not start.
    """
    packs = {"bad": FakePack("bad", [{"vidPid": "057e:0337", "class": "adapter"}])}
    assert usb_devices.launch_notice("bad", packs=packs,
                                     sysfs=sysfs_with(tmp_path, {})) == ""
    assert usb_devices.report(packs=packs, sysfs=sysfs_with(tmp_path, {})) == []


# ── what the installer is handed ─────────────────────────────────────────────

def test_udev_rules_carry_the_declared_line_and_name_the_device(tmp_path):
    lines = usb_devices.udev_rules(FakePack("gc", [ADAPTER]))
    assert ADAPTER["udevRule"] in lines
    # A rules file nobody can trace back to a pack is a rules file nobody dares
    # delete. The comment names the pack and the device.
    assert any(line.startswith("# gc:") and "GameCube adapter" in line
               for line in lines)


def test_a_device_needing_no_rule_produces_none(tmp_path):
    """Plenty of peripherals work on the kernel's defaults, and an unnecessary
    rule is a permission widened for nothing."""
    no_rule = {k: v for k, v in ADAPTER.items() if k != "udevRule"}
    assert usb_devices.udev_rules(FakePack("gc", [no_rule])) == []


def test_declares_usb_is_what_asks_for_the_udev_re_fire():
    assert usb_devices.declares_usb(FakePack("gc", [ADAPTER])) is True
    assert usb_devices.declares_usb(FakePack("plain")) is False
