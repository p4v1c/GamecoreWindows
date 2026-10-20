"""Which Windows session is on the screen, who is logged on to it, which display.

Replaces the Linux probing of `DISPLAY` and `XAUTHORITY`. A window opens on
the desktop of the session its process belongs to; only the active console
session is the one on the TV (session 0 has no visible desktop, a Remote
Desktop session draws on another machine).
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

# WTSGetActiveConsoleSessionId's answer while no session is attached to the
# console (switching users, the console being reconnected).
NO_CONSOLE_SESSION = 0xFFFFFFFF
_WTS_USER_NAME = 5
_DISPLAY_ATTACHED_TO_DESKTOP = 0x1
_DISPLAY_PRIMARY_DEVICE = 0x4

_kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
_kernel32.ProcessIdToSessionId.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
_kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
_wtsapi32.WTSQuerySessionInformationW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
    ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD))
_wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
_wtsapi32.WTSFreeMemory.argtypes = (ctypes.c_void_p,)


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


_user32.EnumDisplayDevicesW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.POINTER(_DisplayDevice), wintypes.DWORD)
_user32.EnumDisplayDevicesW.restype = wintypes.BOOL


def own_session_id() -> int:
    """The session this process runs in."""
    session = wintypes.DWORD()
    if not _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        return NO_CONSOLE_SESSION
    return session.value


def console_session_id() -> int:
    """The session attached to the physical console, or NO_CONSOLE_SESSION."""
    return _kernel32.WTSGetActiveConsoleSessionId()


def session_user(session_id: int) -> str:
    """The user logged on to `session_id`, "" when nobody is."""
    buffer = wintypes.LPWSTR()
    size = wintypes.DWORD()
    if not _wtsapi32.WTSQuerySessionInformationW(
            None, session_id, _WTS_USER_NAME, ctypes.byref(buffer), ctypes.byref(size)):
        return ""
    try:
        return buffer.value or ""
    finally:
        _wtsapi32.WTSFreeMemory(buffer)


def primary_display() -> str:
    """The primary display attached to the desktop (`\\\\.\\DISPLAY1`), "" when none is."""
    device = _DisplayDevice()
    device.cb = ctypes.sizeof(device)
    index = 0
    while _user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
        wanted = _DISPLAY_ATTACHED_TO_DESKTOP | _DISPLAY_PRIMARY_DEVICE
        if device.StateFlags & wanted == wanted:
            return device.DeviceName
        index += 1
    return ""
