"""The screen's power, Windows' idle timers and the power plan.

Replaces `xset dpms`, `kscreen-doctor --dpms`, the session's
`SimulateUserActivity`, PowerDevil's and the X server's idle timeouts, and
`cpupower`:

| Here | Win32 |
|---|---|
| `screen_off()` / `screen_on()` | `SC_MONITORPOWER` posted to every top-level window, the PC held awake (`awake.py`); on a Modern Standby PC the screen stays on, black, held on, since switching it off sleeps the PC; on: plus a zero-length mouse move, which is what lights a panel Windows turned off, and the hold released |
| `signal_activity()` | `SetThreadExecutionState` once, no `ES_CONTINUOUS`: resets the display and sleep idle timers, holds nothing |
| `idle_timeouts()` / `set_idle_timeouts()` | the active scheme's "turn off the display" and "sleep after", on AC and battery (PowrProf) |
| `active_scheme()` / `set_active_scheme()` | the power plan (Power saver while the screen is off) |

The backend runs in the console user's session (dev-log D8), so the messages
and the input reach the desktop the TV shows.
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

from . import awake

_HWND_BROADCAST = 0xFFFF
_WM_SYSCOMMAND = 0x0112
_SC_MONITORPOWER = 0xF170
_MONITOR_ON, _MONITOR_OFF = -1, 2
_INPUT_MOUSE, _MOUSEEVENTF_MOVE = 0, 0x0001
_ES_SYSTEM_REQUIRED, _ES_DISPLAY_REQUIRED = 0x1, 0x2

VIDEO_SUBGROUP = "7516b95f-f776-4464-8c53-06167f40cc99"
VIDEO_IDLE = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"
SLEEP_SUBGROUP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
SLEEP_IDLE = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
# The timers, by the key `idle_timeouts()` names them: (subgroup, setting, on AC).
TIMERS = {"display_ac": (VIDEO_SUBGROUP, VIDEO_IDLE, True),
          "display_dc": (VIDEO_SUBGROUP, VIDEO_IDLE, False),
          "sleep_ac": (SLEEP_SUBGROUP, SLEEP_IDLE, True),
          "sleep_dc": (SLEEP_SUBGROUP, SLEEP_IDLE, False)}
POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"


class _GUID(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_byte * 16)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        return cls((ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le))

    def text(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self.bytes)))


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    # MOUSEINPUT is the union's largest member: sizeof(INPUT) is 40 on x64,
    # and SendInput refuses any other size.
    _fields_ = [("type", wintypes.DWORD), ("mi", _MOUSEINPUT)]


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _powrprof():
    return ctypes.WinDLL("powrprof")


def _monitor(state: int) -> None:
    _user32().PostMessageW(_HWND_BROADCAST, _WM_SYSCOMMAND, _SC_MONITORPOWER, state)


def _modern_standby() -> bool:
    from . import sleep        # sleep imports this module
    try:
        return sleep.power_model() == sleep.MODERN_STANDBY
    except OSError:
        return False


def screen_off() -> None:
    """Dark screen, PC still running, as DPMS on the Linux box. Any input, or `screen_on()`, ends it.

    Modern Standby: the display stays on (the interface is black) and held on,
    since a display that goes off takes the PC into standby (dev-log D56).
    """
    if _modern_standby():
        awake.hold({awake.DISPLAY_REQUIRED, awake.SYSTEM_REQUIRED})
        return
    awake.hold({awake.SYSTEM_REQUIRED})
    _monitor(_MONITOR_OFF)


def screen_on() -> None:
    """Light the screen: the hold released, monitor-on message, a zero-length mouse move, display required."""
    awake.release()
    _monitor(_MONITOR_ON)
    move = _INPUT(type=_INPUT_MOUSE, mi=_MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_MOVE, 0, 0))
    _user32().SendInput(1, ctypes.byref(move), ctypes.sizeof(_INPUT))
    signal_activity()


def signal_activity() -> None:
    """Tell Windows somebody is there: both idle timers restart, nothing is held."""
    ctypes.windll.kernel32.SetThreadExecutionState(_ES_DISPLAY_REQUIRED | _ES_SYSTEM_REQUIRED)


def active_scheme() -> str:
    """The active power plan's GUID, lower case. Raises OSError."""
    return _active_scheme()


def _active_scheme() -> str:
    pointer = ctypes.POINTER(_GUID)()
    code = _powrprof().PowerGetActiveScheme(None, ctypes.byref(pointer))
    if code:
        raise OSError(code, "PowerGetActiveScheme failed")
    try:
        return pointer.contents.text()
    finally:
        ctypes.windll.kernel32.LocalFree(pointer)


def set_active_scheme(scheme: str) -> None:
    """Make `scheme` the active plan. Raises OSError (no such plan on this PC)."""
    guid = _GUID.of(scheme)
    code = _powrprof().PowerSetActiveScheme(None, ctypes.byref(guid))
    if code:
        raise OSError(code, f"PowerSetActiveScheme refused {scheme}")


def idle_timeouts() -> dict[str, int]:
    """The active plan's timers in seconds (0 = never), keyed as in `TIMERS`."""
    scheme = _GUID.of(_active_scheme())
    prof = _powrprof()
    out = {}
    for key, (subgroup, setting, on_ac) in TIMERS.items():
        value = wintypes.DWORD()
        read = prof.PowerReadACValueIndex if on_ac else prof.PowerReadDCValueIndex
        code = read(None, ctypes.byref(scheme), ctypes.byref(_GUID.of(subgroup)),
                    ctypes.byref(_GUID.of(setting)), ctypes.byref(value))
        if code:
            raise OSError(code, f"could not read the {key} timer")
        out[key] = value.value
    return out


def set_idle_timeouts(values: dict[str, int]) -> None:
    """Write the timers named in `values` to the active plan, and apply it."""
    scheme_text = _active_scheme()
    scheme = _GUID.of(scheme_text)
    prof = _powrprof()
    for key, seconds in values.items():
        subgroup, setting, on_ac = TIMERS[key]
        write = prof.PowerWriteACValueIndex if on_ac else prof.PowerWriteDCValueIndex
        code = write(None, ctypes.byref(scheme), ctypes.byref(_GUID.of(subgroup)),
                     ctypes.byref(_GUID.of(setting)), wintypes.DWORD(int(seconds)))
        if code:
            raise OSError(code, f"could not write the {key} timer")
    # Written values take effect when the scheme is (re)applied.
    set_active_scheme(scheme_text)
