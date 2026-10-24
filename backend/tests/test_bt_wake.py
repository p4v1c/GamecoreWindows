"""Which devices may wake the box: the Bluetooth radio and the pads, nothing disarmed.

`platform/device_wake.py` is faked; the instance ids are the shapes Windows
gives (the DS4 and the mouse are this PC's own, read on 24/10/2026).
"""
from __future__ import annotations

import asyncio

from backend.services import bt_wake

DS4 = "40:1b:5f:b9:ea:8d"


def _dev(instance, pnp_class, enabled=False):
    return {"instance": instance, "wmi_name": instance + "_0", "enabled": enabled,
            "pnp_class": pnp_class}


RADIO = _dev("USB\\VID_8087&PID_0029\\5&2B4F1E1&0&10", "Bluetooth")
PCI_RADIO = _dev("PCI\\VEN_8086&DEV_51F1&SUBSYS_00748086&REV_01\\3&11583659&0&A3", "Bluetooth")
DS4_HID = _dev("BTHENUM\\{00001124-0000-1000-8000-00805F9B34FB}_VID&0002054C_PID&09CC"
               "\\7&253C164E&0&401B5FB9EA8D_C00000000", "HIDClass")
MOUSE_HID = _dev("BTHENUM\\{00001124-0000-1000-8000-00805F9B34FB}_VID&0002046D_PID&B016"
                 "\\7&253C164E&0&000013080504_C00000000", "HIDClass", enabled=True)
WIRED_PAD = _dev("USB\\VID_045E&PID_028E\\6&1A2B3C4D&0&2", "XnaComposite")
WIRED_PAD_INTERFACE = _dev("USB\\VID_045E&PID_028E&MI_00\\7&1&0&0000", "HIDClass")
USB_KEYBOARD = _dev("USB\\VID_046D&PID_C31C\\6&2&0&1", "USB")
ENUMERATOR = _dev("BTH\\MS_BTHBRB\\6&2928FA2F&0&1", "Bluetooth")
DEVICES = [RADIO, PCI_RADIO, DS4_HID, MOUSE_HID, WIRED_PAD, WIRED_PAD_INTERFACE,
           USB_KEYBOARD, ENUMERATOR]


def test_the_radios_are_armed_even_with_no_pad_on_the_roster():
    assert bt_wake.devices_to_arm(DEVICES, set(), set()) == sorted(
        [RADIO["wmi_name"], PCI_RADIO["wmi_name"]])


def test_a_bluetooth_pad_is_armed_by_the_address_the_roster_keys_it_by():
    armed = bt_wake.devices_to_arm(DEVICES, {DS4}, set())
    assert DS4_HID["wmi_name"] in armed


def test_a_wired_pad_is_armed_as_a_device_not_by_its_interfaces():
    armed = bt_wake.devices_to_arm(DEVICES, set(), {"045e:028e"})
    assert WIRED_PAD["wmi_name"] in armed
    assert WIRED_PAD_INTERFACE["wmi_name"] not in armed


def test_nothing_else_is_touched_and_nothing_is_disarmed():
    armed = bt_wake.devices_to_arm(DEVICES, {DS4}, {"045e:028e"})
    for other in (MOUSE_HID, USB_KEYBOARD, ENUMERATOR):
        assert other["wmi_name"] not in armed
    already = [dict(RADIO, enabled=True)]
    assert bt_wake.devices_to_arm(already, set(), set()) == []


def test_roster_keys_split_into_addresses_and_wired_ids(monkeypatch):
    monkeypatch.setattr(bt_wake.gamepad_monitor, "roster", lambda: {
        "84:30:95:07:c8:1c": ("054c", "09cc", "DS4", 5),
        "sdl3": ("045E", "028E", "Xbox 360", 3)})
    assert bt_wake.roster_macs() == {"84:30:95:07:c8:1c"}
    assert bt_wake.roster_usb_ids() == {"045e:028e"}


def test_a_pass_allows_what_it_found(monkeypatch):
    calls = []
    monkeypatch.setattr(bt_wake.device_wake, "wake_devices", lambda: DEVICES)
    monkeypatch.setattr(bt_wake.device_wake, "set_wake",
                        lambda name, allowed: calls.append((name, allowed)))
    monkeypatch.setattr(bt_wake.gamepad_monitor, "roster",
                        lambda: {DS4: ("054c", "09cc", "DS4", 5)})
    assert asyncio.run(bt_wake.set_wake_permissions()) is True
    assert sorted(calls) == sorted((d["wmi_name"], True) for d in (RADIO, PCI_RADIO, DS4_HID))


def test_one_refused_device_does_not_stop_the_others(monkeypatch, caplog):
    calls = []

    def set_wake(name, allowed):
        calls.append(name)
        if name == PCI_RADIO["wmi_name"]:
            raise OSError("access denied")

    monkeypatch.setattr(bt_wake.device_wake, "wake_devices", lambda: [PCI_RADIO, RADIO])
    monkeypatch.setattr(bt_wake.device_wake, "set_wake", set_wake)
    monkeypatch.setattr(bt_wake.gamepad_monitor, "roster", lambda: {})
    assert asyncio.run(bt_wake.set_wake_permissions()) is True
    assert len(calls) == 2 and "could not allow" in caplog.text


def test_unreadable_or_empty_settings_are_reported_as_unknown(monkeypatch):
    """Unelevated, WMI lists nothing: the loop retries later instead of believing it."""
    def refused():
        raise OSError("WMI refused")
    monkeypatch.setattr(bt_wake.device_wake, "wake_devices", refused)
    assert asyncio.run(bt_wake.set_wake_permissions()) is None
    monkeypatch.setattr(bt_wake.device_wake, "wake_devices", lambda: [])
    assert asyncio.run(bt_wake.set_wake_permissions()) is None


def test_pads_cannot_be_turned_off_on_windows_and_say_so_once(monkeypatch, caplog):
    monkeypatch.setattr(bt_wake, "_told_no_turn_off", False)
    caplog.set_level("INFO")
    assert asyncio.run(bt_wake.turn_off_pads()) == 0
    assert asyncio.run(bt_wake.turn_off_pads()) == 0
    assert caplog.text.count("cannot drop one device's link") == 1


def test_the_loop_passes_again_when_a_new_pad_connects(monkeypatch):
    passes = []
    roster = {}

    async def one_pass():
        passes.append(set(roster))
        return True

    monkeypatch.setattr(bt_wake, "set_wake_permissions", one_pass)
    monkeypatch.setattr(bt_wake.gamepad_monitor, "roster", lambda: dict(roster))
    monkeypatch.setattr(bt_wake, "ROSTER_POLL_SECS", 0.01)

    async def scenario():
        task = asyncio.create_task(bt_wake.run())
        await asyncio.sleep(0.05)
        roster["sdl4"] = ("045e", "028e", "Xbox 360", 3)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert len(passes) == 2, "one pass at start, one for the new pad, none in between"
