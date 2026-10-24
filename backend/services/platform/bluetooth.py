"""Bluetooth devices: paired list, scan, pair, unpair, through WinRT.

Replaces `bluetoothctl`. Devices are association endpoints of the classic
and Low Energy protocols (Xbox Series pads are LE, DualShock 4 is classic).
Addresses are `AA:BB:CC:DD:EE:FF` strings, as the interface already uses.

Windows connects a paired device by itself when the device asks (a pad
switched on, a headset powered up), and offers no call to force a connection
or a disconnection for an arbitrary device: `connected` is read, never set.

Pairing answers Windows' confirmation itself ("just works", as a box with no
keypad must): a device that wants a typed PIN cannot be served and says so.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_ADDRESS = "System.Devices.Aep.DeviceAddress"
_CONNECTED = "System.Devices.Aep.IsConnected"
_PAIRED = "System.Devices.Aep.IsPaired"
_PROPERTIES = [_ADDRESS, _CONNECTED, _PAIRED]

# DevicePairingKinds Windows may ask us to handle: confirm, show a PIN, or
# confirm a matching PIN. PROVIDE_PIN (4) needs a keypad and is not offered.
_PAIRING_KINDS = 1 | 2 | 8
# DevicePairingResultStatus / DeviceUnpairingResultStatus values.
_PAIRED_OK, _ALREADY_PAIRED, _AUTH_FAILURE, _HANDLER_MISSING = 0, 3, 9, 16
_UNPAIRED_OK, _ALREADY_UNPAIRED = 0, 1
_PAIR_FAILURES = {1: "The device is not ready to pair. Put it in pairing mode.",
                  2: "Pairing failed", 4: "The device refused the connection",
                  7: "The device did not answer in time",
                  9: "This device asks for a PIN, which the box cannot type",
                  16: "This device asks for a PIN, which the box cannot type"}

# Where each address was last seen, so pair and unpair can find the endpoint.
_ids: dict[str, str] = {}


def format_address(raw) -> str:
    """`"8c:de:52:ad:d7:e4"` or `0x8cde52add7e4` → `"8C:DE:52:AD:D7:E4"`."""
    if isinstance(raw, int):
        digits = f"{raw:012X}"
    else:
        digits = "".join(c for c in str(raw) if c.isalnum()).upper()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)) if len(digits) == 12 else ""


def _value(info, key: str, unbox):
    props = info.properties
    if not props.has_key(key):
        return None
    try:
        return unbox(props.lookup(key))
    except (TypeError, OSError):
        return None


def describe(info) -> dict:
    """One endpoint as `{"mac", "name", "connected", "paired", "id"}`."""
    from winrt.system import unbox_boolean, unbox_string

    return {"mac": format_address(_value(info, _ADDRESS, unbox_string) or ""),
            "name": info.name or "",
            "connected": bool(_value(info, _CONNECTED, unbox_boolean)),
            "paired": bool(_value(info, _PAIRED, unbox_boolean)),
            "id": info.id}


def _selectors(paired: bool) -> list[str]:
    from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice

    return [BluetoothDevice.get_device_selector_from_pairing_state(paired),
            BluetoothLEDevice.get_device_selector_from_pairing_state(paired)]


async def _known(paired: bool) -> list[dict]:
    """Endpoints Windows already knows in that pairing state, one per address."""
    from winrt.windows.devices.enumeration import DeviceInformation

    seen: dict[str, dict] = {}
    for selector in _selectors(paired):
        for info in await DeviceInformation.find_all_async(selector, _PROPERTIES):
            device = describe(info)
            # The "paired" selector also matches cached unpaired endpoints.
            if device["mac"] and device["paired"] == paired:
                _ids[device["mac"]] = device["id"]
                current = seen.get(device["mac"])
                if current is None or (device["connected"] and not current["connected"]):
                    seen[device["mac"]] = device
    return list(seen.values())


async def paired_devices() -> list[dict]:
    """Every paired device: `[{"mac", "name", "connected", "paired": True, "id"}]`."""
    return await _known(paired=True)


async def scan(seconds: float) -> list[dict]:
    """Look around for `seconds`; returns the unpaired devices that answered."""
    from winrt.windows.devices.enumeration import DeviceInformation

    found: dict[str, dict] = {}

    def on_added(low_energy: bool):
        def added(_watcher, info) -> None:
            device = describe(info)
            # Unnamed LE advertisers are phones and beacons with random
            # addresses (measured: 20 of 28 in one scan); a pad names itself.
            if not device["mac"] or device["paired"] or (low_energy and not device["name"]):
                return
            _ids[device["mac"]] = device["id"]
            found.setdefault(device["mac"], device)
        return added

    classic, low_energy = _selectors(False)
    watchers = []
    for selector, is_le in ((classic, False), (low_energy, True)):
        watcher = DeviceInformation.create_watcher(selector, _PROPERTIES)
        watcher.add_added(on_added(is_le))
        watcher.add_updated(lambda _w, _u: None)   # required, or Added stops firing
        watcher.start()
        watchers.append(watcher)
    try:
        await asyncio.sleep(seconds)
    finally:
        for watcher in watchers:
            watcher.stop()
    return list(found.values())


async def _endpoint(mac: str):
    from winrt.windows.devices.enumeration import DeviceInformation

    if mac not in _ids:
        await _known(paired=True)
        await _known(paired=False)
    device_id = _ids.get(mac)
    return await DeviceInformation.create_from_id_async(device_id) if device_id else None


async def pair(mac: str) -> tuple[bool, str]:
    """Pair the device at `mac`, accepting Windows' confirmation. (ok, message)."""
    info = await _endpoint(mac)
    if info is None:
        return False, "The device is not in range. Scan again with it in pairing mode."
    custom = info.pairing.custom
    token = custom.add_pairing_requested(lambda _sender, args: args.accept())
    try:
        result = await custom.pair_async(_PAIRING_KINDS)
    finally:
        custom.remove_pairing_requested(token)
    status = int(result.status)
    if status in (_PAIRED_OK, _ALREADY_PAIRED):
        return True, "Paired"
    return False, _PAIR_FAILURES.get(status, f"Pairing failed (status {status})")


async def unpair(mac: str) -> tuple[bool, str]:
    """Remove the pairing. (ok, message); already unpaired counts as done."""
    info = await _endpoint(mac)
    if info is None:
        return True, "Not paired"
    status = int((await info.pairing.unpair_async()).status)
    if status in (_UNPAIRED_OK, _ALREADY_UNPAIRED):
        return True, "Unpaired"
    return False, f"Windows refused to unpair it (status {status})"
