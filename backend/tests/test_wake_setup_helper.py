"""`gamecore-wake-setup`, run for real against a fake sysfs tree.

It decides in bash what the backend decides in Python (which input device is a
pad), so the two are run over the same devices here and must agree.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.services.gamepad_devices import BTN_SOUTH, is_pad_keys


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "install" / "bin" / "gamecore-wake-setup"
USB_ROOT = "devices/pci0000:00/0000:00:14.0/usb3"

BTN_LEFT, BTN_RIGHT, BTN_MODE = 0x110, 0x111, 0x13C
DEVICES = {
    # name: (usb port, key codes)
    "ds4": ("3-1", {BTN_SOUTH, 0x131, 0x133, 0x134, BTN_MODE}),
    "generic-pad": ("3-2", {BTN_SOUTH, 0x131}),
    "keyboard": ("3-3", set(range(1, 120))),
    "mouse": ("3-5", {BTN_LEFT, BTN_RIGHT}),
    # Declares every code, BTN_SOUTH included: Sunshine's virtual keyboard.
    "everything": ("3-6", set(range(1, 0x2ff))),
}


def bitmap(codes: set[int]) -> str:
    """The kernel's capabilities/key format: 64-bit words, most significant first."""
    if not codes:
        return "0"
    words = [0] * (max(codes) // 64 + 1)
    for c in codes:
        words[c // 64] |= 1 << (c % 64)
    return " ".join(f"{w:x}" for w in reversed(words))


def _usb(root: Path, port: str, vid: str, pid: str) -> Path:
    dev = root / USB_ROOT / port
    (dev / "power").mkdir(parents=True)
    (dev / "idVendor").write_text(vid + "\n")
    (dev / "idProduct").write_text(pid + "\n")
    (dev / "power" / "wakeup").write_text("disabled\n")
    return dev


def _input(root: Path, parent: Path, n: int, codes: set[int]) -> Path:
    node = parent / f"{parent.name}:1.0" / f"0003:0000:0000.000{n}" / "input" / f"input{n}"
    (node / "capabilities").mkdir(parents=True)
    (node / "capabilities" / "key").write_text(bitmap(codes) + "\n")
    link = root / "class" / "input" / f"input{n}"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(node, link.parent))
    return node


@pytest.fixture
def sysfs(tmp_path):
    root = tmp_path / "sys"
    bt = _usb(root, "3-4", "8087", "0029")
    hci = bt / "3-4:1.0" / "bluetooth" / "hci0"
    hci.mkdir(parents=True)
    (hci / "hci0:256").mkdir()
    (root / "class" / "bluetooth").mkdir(parents=True)
    (root / "class" / "bluetooth" / "hci0").symlink_to(
        os.path.relpath(hci, root / "class" / "bluetooth"))
    (root / "class" / "bluetooth" / "hci0:256").symlink_to(
        os.path.relpath(hci / "hci0:256", root / "class" / "bluetooth"))
    usb = {"bt": bt}
    for n, (name, (port, codes)) in enumerate(DEVICES.items(), start=1):
        usb[name] = _usb(root, port, "1234", f"000{n}")
        _input(root, usb[name], n, codes)
    # A uinput pad: no USB parent, nothing to arm.
    virtual = root / "devices" / "virtual" / "input" / "input9"
    (virtual / "capabilities").mkdir(parents=True)
    (virtual / "capabilities" / "key").write_text(bitmap({BTN_SOUTH}) + "\n")
    (root / "class" / "input" / "input9").symlink_to(
        os.path.relpath(virtual, root / "class" / "input"))
    return {"root": root, "usb": usb}


def run(sysfs, *args):
    return subprocess.run(["bash", str(HELPER), *args], text=True, capture_output=True,
                          env={**os.environ, "GAMECORE_SYSFS": str(sysfs["root"])}, timeout=30)


def wake(dev: Path) -> str:
    return (dev / "power" / "wakeup").read_text().strip()


@pytest.mark.linux_until_port(6)
def test_the_adapter_and_the_pads_are_armed_and_nothing_else(sysfs):
    r = run(sysfs)
    assert r.returncode == 0, r.stderr
    usb = sysfs["usb"]
    assert wake(usb["bt"]) == "enabled"
    assert wake(usb["ds4"]) == "enabled"
    assert wake(usb["generic-pad"]) == "enabled"
    assert wake(usb["keyboard"]) == "disabled"
    assert wake(usb["mouse"]) == "disabled"
    assert wake(usb["everything"]) == "disabled"


@pytest.mark.linux_until_port(6)
@pytest.mark.parametrize("name", list(DEVICES))
def test_the_helper_and_the_backend_agree_on_what_a_pad_is(sysfs, name):
    run(sysfs)
    port, codes = DEVICES[name]
    assert (wake(sysfs["usb"][name]) == "enabled") == is_pad_keys(codes)


@pytest.mark.linux_until_port(6)
def test_a_second_run_writes_nothing(sysfs):
    run(sysfs)
    second = run(sysfs)
    assert second.returncode == 0
    assert second.stdout == "", "an attribute already enabled was written again"


@pytest.mark.linux_until_port(6)
def test_udev_mode_arms_one_device(sysfs):
    usb = sysfs["usb"]
    node = next((usb["ds4"] / "3-1:1.0").glob("*/input/input*"))
    r = run(sysfs, "--device", str(node))
    assert r.returncode == 0, r.stderr
    assert wake(usb["ds4"]) == "enabled"
    assert wake(usb["generic-pad"]) == "disabled", "--device touched another device"
    hci = sysfs["root"] / "class" / "bluetooth" / "hci0"
    run(sysfs, "--device", str(hci))
    assert wake(usb["bt"]) == "enabled"


def test_the_bitmap_helper_matches_the_kernel_layout():
    # BTN_SOUTH alone: bit 304 = word 4, bit 48.
    assert bitmap({BTN_SOUTH}) == "1000000000000 0 0 0 0"
