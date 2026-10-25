"""Drop one Bluetooth device's link, as BlueZ's `Device1.Disconnect` does on Linux.

`IOCTL_BTH_DISCONNECT_DEVICE` (`bthioctl.h`) sent to a local radio ends the
link with "remote user terminated" (0x13), the same reason BlueZ sends: a pad
takes it as "turn off". A link left to the sleep itself ends another way, and
an Xbox pad answered that on the Linux box by paging straight back, which woke
it. Radios come from `BluetoothFindFirstRadio` / `BluetoothFindNextRadio`.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# CTL_CODE(FILE_DEVICE_BLUETOOTH 0x41, BTH_IOCTL_BASE + 0x03, METHOD_BUFFERED, FILE_ANY_ACCESS).
IOCTL_BTH_DISCONNECT_DEVICE = 0x41000C
_MAX_RADIOS = 8


class _FindRadioParams(ctypes.Structure):
    """BLUETOOTH_FIND_RADIO_PARAMS."""
    _fields_ = [("dwSize", wintypes.DWORD)]


def address_value(mac: str) -> int:
    """'aa:bb:cc:dd:ee:ff' as the 64-bit BTH_ADDR Windows expects. Pure. Raises ValueError."""
    digits = mac.replace(":", "").replace("-", "")
    if len(digits) != 12:
        raise ValueError(f"not a Bluetooth address: {mac!r}")
    return int(digits, 16)


def _radios() -> list[int]:
    """Handles of the local radios; the caller closes them."""
    bthprops = ctypes.WinDLL("bthprops.cpl", use_last_error=True)
    bthprops.BluetoothFindFirstRadio.restype = wintypes.HANDLE
    params = _FindRadioParams(ctypes.sizeof(_FindRadioParams))
    radio = wintypes.HANDLE()
    find = bthprops.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(radio))
    if not find:
        return []
    radios = [radio.value]
    try:
        while len(radios) < _MAX_RADIOS:
            radio = wintypes.HANDLE()
            if not bthprops.BluetoothFindNextRadio(wintypes.HANDLE(find), ctypes.byref(radio)):
                break
            radios.append(radio.value)
    finally:
        bthprops.BluetoothFindRadioClose(wintypes.HANDLE(find))
    return radios


def disconnect(mac: str) -> bool:
    """End the link to `mac` on whichever radio holds it. True when one radio did.

    Raises ValueError for an address that is not one, OSError when no radio is
    present. Blocking (the radio answers once the link is down): call it in a thread.
    """
    address = ctypes.c_ulonglong(address_value(mac))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    radios = _radios()
    if not radios:
        raise OSError("no Bluetooth radio")
    done = False
    try:
        for radio in radios:
            returned = wintypes.DWORD()
            if kernel32.DeviceIoControl(
                    wintypes.HANDLE(radio), IOCTL_BTH_DISCONNECT_DEVICE,
                    ctypes.byref(address), ctypes.sizeof(address), None, 0,
                    ctypes.byref(returned), None):
                done = True
    finally:
        for radio in radios:
            kernel32.CloseHandle(wintypes.HANDLE(radio))
    return done
