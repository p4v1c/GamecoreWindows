"""The Wi-Fi and Bluetooth radio switches, through WinRT `Windows.Devices.Radios`.

Replaces `nmcli radio wifi` (and rfkill underneath). These are the switches
Windows' quick settings flip. A radio in the `DISABLED` state is blocked by a
hardware switch, the firmware or a policy: turning it on from here cannot work.
"""
from __future__ import annotations

# RadioKind values.
WIFI = 1
BLUETOOTH = 3
# RadioState values.
_ON, _OFF, _DISABLED = 1, 2, 3


class RadioBlocked(OSError):
    """The radio is disabled below Windows (hardware switch, firmware, policy)."""


async def _radio(kind: int):
    from winrt.windows.devices.radios import Radio, RadioAccessStatus

    if await Radio.request_access_async() != RadioAccessStatus.ALLOWED:
        raise OSError("Windows refused access to the radios.")
    for radio in await Radio.get_radios_async():
        if int(radio.kind) == kind:
            return radio
    return None


def read_state(state: int) -> bool | None:
    """On, off (a block below Windows reads as off), or None when unknown."""
    if state == _ON:
        return True
    if state in (_OFF, _DISABLED):
        return False
    return None


async def enabled(kind: int) -> bool | None:
    """Whether the radio of `kind` is on. None when there is no such radio."""
    radio = await _radio(kind)
    return None if radio is None else read_state(int(radio.state))


async def set_enabled(kind: int, on: bool) -> bool | None:
    """Switch the radio; returns the state read back. Raises RadioBlocked / OSError."""
    from winrt.windows.devices.radios import RadioAccessStatus, RadioState

    radio = await _radio(kind)
    if radio is None:
        raise OSError("There is no such radio on this PC.")
    if int(radio.state) == _DISABLED:
        raise RadioBlocked("The radio is blocked by a hardware switch or the firmware.")
    status = await radio.set_state_async(RadioState.ON if on else RadioState.OFF)
    if status != RadioAccessStatus.ALLOWED:
        raise OSError("Windows refused to switch the radio.")
    return read_state(int(radio.state))
