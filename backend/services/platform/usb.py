"""What is on the USB bus: vendor:product → a name, from Windows' device list.

Replaces the `/sys/bus/usb/devices` walk. WMI `Win32_PnPEntity` rows whose
device id starts `USB\\VID_xxxx&PID_xxxx`; a composite device's interfaces
(`&MI_00`) carry the same pair and fold into one entry. Synchronous (COM).
"""
from __future__ import annotations

import re

from .com import wmi_query

_USB_ID = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.IGNORECASE)
_QUERY = "SELECT DeviceID, Name FROM Win32_PnPEntity WHERE DeviceID LIKE 'USB%'"


def parse_devices(rows: list[dict]) -> dict[str, str]:
    """`vid:pid` (lower case) → the first name seen, from PnP rows. Pure.

    The parent device comes after its interfaces in WMI's order, so a name is
    replaced by a later one only when the first was empty.
    """
    found: dict[str, str] = {}
    for row in rows:
        match = _USB_ID.match(str(row.get("DeviceID") or ""))
        if not match:
            continue
        key = f"{match[1].lower()}:{match[2].lower()}"
        name = str(row.get("Name") or "")
        if not found.get(key):
            found[key] = name
    return found


def inventory() -> dict[str, str]:
    """Everything on the USB bus right now (`parse_devices`). Raises OSError."""
    return parse_devices(wmi_query(r"root\cimv2", _QUERY, ("DeviceID", "Name")))
