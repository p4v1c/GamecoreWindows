"""Display modes of the primary output: list, read, set, and whether it is lit.

Replaces `xrandr`, `kscreen-doctor` and the `/sys/class/drm` connector reads.
Win32 `EnumDisplaySettingsW` / `ChangeDisplaySettingsExW` on the primary
display (`\\\\.\\DISPLAY1` usually). Rates are whole hertz, as Windows reports
them (59.94 Hz reads as 59).

A mode is applied for this session only (`persist=False`) until the player
confirms it: an unconfirmed mode must not survive a restart, or a black screen
outlives the revert timer. Confirming writes it to the registry.
"""
from __future__ import annotations

# EnumDisplayDevices state flags.
_ATTACHED_TO_DESKTOP = 0x1
_PRIMARY_DEVICE = 0x4
_MONITOR_ACTIVE = 0x1
_ENUM_CURRENT_SETTINGS = -1
_BITS_PER_PIXEL = 32
# DEVMODE fields and ChangeDisplaySettingsEx flags.
_DM_PELSWIDTH, _DM_PELSHEIGHT, _DM_DISPLAYFREQUENCY = 0x80000, 0x100000, 0x400000
_CDS_UPDATEREGISTRY, _CDS_NORESET = 0x1, 0x10000000
_DISP_CHANGE = {0: "", 1: "Windows needs a restart for this mode", -1: "The driver refused the mode",
                -2: "The display does not support that mode", -3: "Could not save the mode",
                -4: "Invalid flags", -5: "Invalid parameter", -6: "The display adapter is busy"}


def _devices(parent: str | None = None):
    import win32api

    i = 0
    while True:
        try:
            yield win32api.EnumDisplayDevices(parent, i)
        except Exception:      # pywintypes.error: past the last device
            return
        i += 1


def primary_output() -> str:
    """Device name of the primary display, e.g. `\\\\.\\DISPLAY1`; "" when none."""
    for device in _devices():
        if device.StateFlags & _ATTACHED_TO_DESKTOP and device.StateFlags & _PRIMARY_DEVICE:
            return device.DeviceName
    return ""


def unique_modes(raw: list[tuple[int, int, int, int]]) -> list[dict]:
    """`(width, height, rate, bits)` rows → one dict per 32-bit mode, largest first.

    Windows lists each mode once per scaling option (stretch, centre, none);
    they are one mode to the player.
    """
    seen = {(w, h, r) for w, h, r, bits in raw if bits == _BITS_PER_PIXEL and r > 1}
    return [{"width": w, "height": h, "rate": float(r)}
            for w, h, r in sorted(seen, reverse=True)]


def modes(output: str) -> list[dict]:
    """Every mode `output` advertises (`unique_modes`)."""
    import win32api

    raw, i = [], 0
    while True:
        try:
            m = win32api.EnumDisplaySettings(output, i)
        except Exception:      # past the last mode
            break
        raw.append((m.PelsWidth, m.PelsHeight, m.DisplayFrequency, m.BitsPerPel))
        i += 1
    return unique_modes(raw)


def current_mode(output: str) -> dict | None:
    """The mode on screen, or None when the output reports none."""
    import win32api

    try:
        m = win32api.EnumDisplaySettings(output, _ENUM_CURRENT_SETTINGS)
    except Exception:
        return None
    return {"width": m.PelsWidth, "height": m.PelsHeight, "rate": float(m.DisplayFrequency)}


def apply_mode(output: str, width: int, height: int, rate: float,
               persist: bool = False) -> tuple[bool, str]:
    """Put a mode on `output`. (ok, reason). `persist` writes it to the registry
    without changing the screen again (the mode is already on it)."""
    import win32api

    try:
        devmode = win32api.EnumDisplaySettings(output, _ENUM_CURRENT_SETTINGS)
    except Exception as e:
        return False, f"The display reports no mode: {e}"
    devmode.PelsWidth, devmode.PelsHeight = width, height
    devmode.DisplayFrequency = int(round(rate))
    devmode.Fields = _DM_PELSWIDTH | _DM_PELSHEIGHT | _DM_DISPLAYFREQUENCY
    flags = (_CDS_UPDATEREGISTRY | _CDS_NORESET) if persist else 0
    code = win32api.ChangeDisplaySettingsEx(output, devmode, flags)
    return code == 0, _DISP_CHANGE.get(code, f"Windows refused the mode (code {code})")


def display_ready() -> bool:
    """True when an output attached to the desktop has an active monitor."""
    for adapter in _devices():
        if not adapter.StateFlags & _ATTACHED_TO_DESKTOP:
            continue
        if any(m.StateFlags & _MONITOR_ACTIVE for m in _devices(adapter.DeviceName)):
            return True
    return False
