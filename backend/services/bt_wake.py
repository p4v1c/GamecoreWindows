"""Pads wake the box: the devices a pad wakes it through are allowed to.

Windows decides per device whether it may wake the PC (Device Manager's
"Allow this device to wake the computer", `platform/device_wake.py`). On the
reference box it was off for the Bluetooth radio, and a radio that may not
wake the PC makes every Bluetooth pad's home button do nothing. Writing it
needs an elevated process, which the backend is (dev-log D8), so this lives
in the backend next to the pad monitor that sees new pads arrive.

Armed, when Windows lists them as able to wake:
- every Bluetooth radio (PnP class `Bluetooth`, on USB or PCI): pads page
  through it;
- a Bluetooth pad's own device, by the address the roster keys it by (D33);
- a wired pad's USB device, by the vendor:product SDL reports.

Nothing is disarmed: keyboards and mice keep what Windows gave them (dev-log
D36). Turning pads off before a sleep has no Windows counterpart (D28: no
public call drops one device's link), so `turn_off_pads()` turns off nothing.
A Modern Standby PC wakes on pad input by itself; these settings matter for S3.
"""
import asyncio
import logging
import re

from . import gamepad_monitor
from .controller_registry import normalize_mac
from .platform import device_wake

log = logging.getLogger(__name__)

#: How often the roster is checked for a pad not seen before.
ROSTER_POLL_SECS = 5.0
#: How soon a pass that could not read the wake settings is retried.
RETRY_SECS = 30.0
BUS_USB = 0x03

_RADIO_BUSES = ("USB\\", "PCI\\")
_BT_ENUMERATORS = ("BTHENUM\\", "BTHLE\\")
# The device address ends a Bluetooth instance id: "...&0&401B5FB9EA8D_C00000000".
_BT_ADDRESS = re.compile(r"&([0-9A-F]{12})(?:_[0-9A-F]+)?$", re.IGNORECASE)
# A whole USB device, not one of its interfaces (&MI_xx).
_USB_DEVICE = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})\\", re.IGNORECASE)

_told_no_turn_off = False


def _bt_address(instance: str) -> str:
    match = _BT_ADDRESS.search(instance)
    if not match:
        return ""
    raw = match[1].lower()
    return normalize_mac(":".join(raw[i:i + 2] for i in range(0, 12, 2)))


def _is_target(device: dict, pad_macs: set[str], wired_ids: set[str]) -> bool:
    instance = device["instance"].upper()
    if device["pnp_class"] == "Bluetooth" and instance.startswith(_RADIO_BUSES):
        return True
    if instance.startswith(_BT_ENUMERATORS):
        return _bt_address(instance) in pad_macs
    usb = _USB_DEVICE.match(instance)
    return bool(usb) and f"{usb[1].lower()}:{usb[2].lower()}" in wired_ids


def devices_to_arm(devices: list[dict], pad_macs: set[str], wired_ids: set[str]) -> list[str]:
    """WMI names of the radios and pads still refused wake. Pure.

    `devices` comes from `device_wake.wake_devices()`; `pad_macs` are the
    Bluetooth pads' addresses, `wired_ids` the USB pads' `vid:pid`.
    """
    return sorted(d["wmi_name"] for d in devices
                  if not d["enabled"] and _is_target(d, pad_macs, wired_ids))


def roster_macs() -> set[str]:
    """Addresses of the pads the monitor sees; other keys are SDL instances."""
    return {m for m in (normalize_mac(k) for k in gamepad_monitor.roster()) if m}


def roster_usb_ids() -> set[str]:
    """`vid:pid` of the wired pads the monitor sees."""
    return {f"{vendor}:{product}".lower()
            for vendor, product, _name, bus in gamepad_monitor.roster().values()
            if bus == BUS_USB}


async def set_wake_permissions() -> bool | None:
    """One pass: allow the radios and the pads to wake the box.

    True when Windows answered, None when the settings could not be read
    (not elevated: WMI then lists nothing, which reads as an empty list).
    """
    try:
        devices = await asyncio.to_thread(device_wake.wake_devices)
    except OSError as e:
        log.debug("bt_wake: wake settings unreadable: %s", e)
        return None
    if not devices:
        log.debug("bt_wake: Windows lists no device able to wake the box (not elevated?)")
        return None
    for name in devices_to_arm(devices, roster_macs(), roster_usb_ids()):
        try:
            await asyncio.to_thread(device_wake.set_wake, name, True)
            log.info("bt_wake: %s may now wake the box", name)
        except OSError as e:
            log.warning("bt_wake: could not allow %s to wake the box: %s", name, e)
    return True


async def turn_off_pads() -> int:
    """Turn the connected pads off before a sleep: nothing on Windows (D28). Returns 0."""
    global _told_no_turn_off
    if not _told_no_turn_off:
        _told_no_turn_off = True
        log.info("bt_wake: Windows cannot drop one device's link; pads stay on into the sleep")
    return 0


async def run() -> None:
    """A pass at start, then one whenever a pad not seen before connects."""
    seen: set[str] | None = None
    retry_at = 0.0
    loop = asyncio.get_running_loop()
    while True:
        pads = roster_macs() | roster_usb_ids()
        new_pad = seen is None or not pads <= seen
        if new_pad or (retry_at and loop.time() >= retry_at):
            answered = await set_wake_permissions()
            retry_at = 0.0 if answered else loop.time() + RETRY_SECS
            seen = (seen or set()) | pads
        await asyncio.sleep(ROSTER_POLL_SECS)
