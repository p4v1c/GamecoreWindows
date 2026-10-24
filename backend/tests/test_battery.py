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


def _sdl_pad(device="sdl0", power=1, serial="40:1b:5f:b9:ea:8d", is_pad=True,
             name="PS4 Controller"):
    from backend.services.platform.gamepads import PadInfo
    return PadInfo(device=device, name=name, vendor="054c", product="09cc", guid="0" * 32,
                   serial=serial, bus=5, is_pad=is_pad, has_guide=True, power=power)


@pytest.fixture
def connected(monkeypatch):
    from backend.services import controller_registry
    from backend.services.platform import gamepads as platform_gamepads
    pads: list = []
    monkeypatch.setattr(platform_gamepads, "pads", lambda: list(pads))
    controller_registry._slots.clear()
    yield pads
    controller_registry._slots.clear()


@pytest.mark.parametrize("power, level", [(0, 5), (1, 20), (2, 60), (3, 100)])
def test_sdls_power_level_becomes_the_top_of_its_band(connected, power, level):
    connected.append(_sdl_pad(power=power))
    assert [b["level"] for b in battery.read_batteries()] == [level]


@pytest.mark.parametrize("power", [-1, 4])
def test_an_unknown_or_wired_level_lists_no_battery(connected, power):
    """Wired: SDL2 says "plugged in", not how full. A guessed number would lie."""
    connected.append(_sdl_pad(power=power))
    assert battery.read_batteries() == []


def test_a_wheel_battery_is_not_a_controller_pill(connected):
    connected.append(_sdl_pad(is_pad=False, name="Racing Wheel"))
    assert battery.read_batteries() == []


def test_a_battery_is_joined_to_its_player_slot_by_address(connected):
    from backend.services import controller_registry
    controller_registry.connect("40:1b:5f:b9:ea:8d", "PS4 Controller")
    controller_registry.connect("aa:bb:cc:dd:ee:ff", "Other")
    connected.append(_sdl_pad(serial="aa:bb:cc:dd:ee:ff"))
    assert battery.read_batteries()[0]["player"] == 2


def test_low_enters_the_25_alert_and_empty_the_5_alert():
    """SDL's bands land on the thresholds the toasts were written for."""
    assert battery._check([pad(20)])[0]["threshold"] == 25
    assert battery._check([pad(5)])[0]["threshold"] == 5
