"""Unit tests for the low-battery threshold logic (services.battery._check).

Run under pytest:  pytest backend/tests/test_battery.py
Or directly:       python backend/tests/test_battery.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.services import battery


@pytest.fixture(autouse=True)
def fresh_alert_state():
    """_check remembers which thresholds already fired — start every test clean."""
    battery._fired.clear()
    yield
    battery._fired.clear()


def pad(level, charging=False, name="sony_controller_battery_aa"):
    return {"name": name, "label": "DualShock 4", "level": level, "charging": charging}


def test_each_threshold_fires_once_on_the_way_down():
    assert battery._check([pad(100)]) == [], "100% → rien"
    assert battery._check([pad(26)]) == [], "26% → rien"

    a = battery._check([pad(25)])
    assert len(a) == 1 and a[0]["threshold"] == 25, f"25% → toast seuil 25 ({a})"

    assert battery._check([pad(16)]) == [], "16% → nothing (25 already reported)"

    a = battery._check([pad(15)])
    assert len(a) == 1 and a[0]["threshold"] == 15, f"15% → toast seuil 15 ({a})"

    assert battery._check([pad(14)]) == [], "14% → nothing (already reported)"

    a = battery._check([pad(10)])
    assert len(a) == 1 and a[0]["threshold"] == 10, f"10% → toast seuil 10 ({a})"

    a = battery._check([pad(4)])
    assert len(a) == 1 and a[0]["threshold"] == 5, f"4% → toast seuil 5 ({a})"

    assert battery._check([pad(3)]) == [], "3% → rien"


def test_pad_connecting_at_4_percent_gives_one_toast_not_three():
    a = battery._check([pad(4)])
    assert len(a) == 1 and a[0]["threshold"] == 5, f"connecting at 4% → a single toast ({a})"


def test_charging_rearms_the_thresholds():
    battery._check([pad(12)])
    assert battery._check([pad(30, charging=True)]) == [], "charge → rien"

    a = battery._check([pad(15)])
    assert len(a) == 1, f"after charging, 15% reports again ({a})"


def test_oscillating_around_a_threshold_does_not_spam():
    battery._check([pad(15)])
    assert battery._check([pad(16)]) == [], "16% does not re-arm (hysteresis margin)"
    assert battery._check([pad(15)]) == [], "15% already reported"

    battery._check([pad(25)])  # > 15+5 → re-arms
    a = battery._check([pad(15)])
    assert len(a) == 1, f"25% puis 15% re-signale ({a})"


def test_disconnecting_forgets_the_alert_state():
    battery._check([pad(12)])
    battery._check([])  # manette partie
    a = battery._check([pad(12)])
    assert len(a) == 1 and a[0]["threshold"] == 15, f"reconnecting at 12% reports again ({a})"


def test_two_pads_alert_independently():
    a = battery._check([pad(14, name="pad_a"), pad(80, name="pad_b")])
    assert len(a) == 1, f"2 pads: only the low one alerts ({a})"


if __name__ == "__main__":
    for fn in (
        test_each_threshold_fires_once_on_the_way_down,
        test_pad_connecting_at_4_percent_gives_one_toast_not_three,
        test_charging_rearms_the_thresholds,
        test_oscillating_around_a_threshold_does_not_spam,
        test_disconnecting_forgets_the_alert_state,
        test_two_pads_alert_independently,
    ):
        battery._fired.clear()
        fn()
        print(f"[OK ] {fn.__name__}")
    print("\nAll tests passed.")


def _supply(root, name, capacity, input_keys=None):
    """A power supply dir; `input_keys` = key bitmaps of the device's input nodes."""
    dev = root / "devices" / name
    for i, bitmap in enumerate(input_keys or []):
        node = dev / "input" / f"input{i}" / "capabilities"
        node.mkdir(parents=True)
        (node / "key").write_text(bitmap + "\n")
    dev.mkdir(parents=True, exist_ok=True)
    supply = root / "power_supply" / name
    supply.mkdir(parents=True)
    (supply / "capacity").write_text(f"{capacity}\n")
    (supply / "status").write_text("Discharging\n")
    (supply / "device").symlink_to(dev)


@pytest.mark.linux_until_port(5)
def test_a_mouse_battery_is_not_a_controller(tmp_path):
    """The MX Anywhere 3 showed up as a second "P2" pill in the top bar."""
    gamepad = "7fdb000000000000 0 0 0 0"          # BTN_SOUTH (0x130) and friends
    mouse = "1f0000 0 0 0 0"                       # BTN_LEFT..BTN_EXTRA only
    _supply(tmp_path, "ps-controller-battery-40:1b:5f:b9:ea:8d", 55, [gamepad, "0", "1000"])
    _supply(tmp_path, "hidpp_battery_0", 40, [mouse])
    _supply(tmp_path, "hid-5c:ba:37:63:ee:62-battery", 52)   # no input nodes: kept
    names = [b["name"] for b in battery.read_batteries(tmp_path / "power_supply")]
    assert names == ["hid-5c:ba:37:63:ee:62-battery", "ps-controller-battery-40:1b:5f:b9:ea:8d"]


def test_capability_bitmaps_are_read_as_64_bit_words():
    assert battery._has_bit("7fdb000000000000 0 0 0 0", 0x130)
    assert not battery._has_bit("1f0000 0 0 0 0", 0x130)
    assert battery._has_bit("1f0000 0 0 0 0", 0x110)
    assert not battery._has_bit("", 0x130)
