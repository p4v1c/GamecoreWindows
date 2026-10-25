"""Keep the PC running while standby has the screen dark.

On Linux, standby cuts the screen (DPMS) and the box keeps running, so the
next pad press wakes it. (Rest mode really sleeps, on both: dev-log D62.)
A Modern Standby PC enters standby
as soon as its display goes off, and paused the backend for minutes when
`SC_MONITORPOWER` turned it off (measured, dev-log D56), even with a
system-required power request held. So there the screen is not switched off:
the interface already draws black, and a display-required request keeps
Windows from blanking it and sleeping.

One power request for the process (`PowerCreateRequest`): not tied to a
thread like `SetThreadExecutionState`, and Windows drops it if the backend dies.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

DISPLAY_REQUIRED, SYSTEM_REQUIRED = 0, 1
_POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
_REASON = "GameCore: the screen is dark, the console keeps running"


class _ReasonContext(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("Flags", wintypes.DWORD),
                ("SimpleReasonString", wintypes.LPWSTR)]


class _Request:
    """The Win32 power request, created on first use."""

    def __init__(self):
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.PowerCreateRequest.restype = wintypes.HANDLE
        self._reason = _ReasonContext(0, _POWER_REQUEST_CONTEXT_SIMPLE_STRING, _REASON)
        self._handle = self._kernel32.PowerCreateRequest(ctypes.byref(self._reason))
        if not self._handle or self._handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "PowerCreateRequest failed")

    def set(self, kind: int) -> None:
        if not self._kernel32.PowerSetRequest(wintypes.HANDLE(self._handle), kind):
            raise OSError(ctypes.get_last_error(), f"PowerSetRequest({kind}) failed")

    def clear(self, kind: int) -> None:
        self._kernel32.PowerClearRequest(wintypes.HANDLE(self._handle), kind)


_lock = threading.Lock()
_request = None
_held: set[int] = set()


def _get_request():
    global _request
    if _request is None:
        _request = _Request()
    return _request


def hold(kinds: set[int]) -> None:
    """Hold exactly `kinds` (DISPLAY_REQUIRED, SYSTEM_REQUIRED). Raises OSError."""
    with _lock:
        request = _get_request()
        for kind in sorted(kinds - _held):
            request.set(kind)
            _held.add(kind)
        for kind in sorted(_held - kinds):
            request.clear(kind)
            _held.discard(kind)


def release() -> None:
    """Hold nothing: Windows' own timers apply again."""
    with _lock:
        if _request is None:
            return
        for kind in sorted(_held):
            _request.clear(kind)
        _held.clear()


def held() -> set[int]:
    with _lock:
        return set(_held)
