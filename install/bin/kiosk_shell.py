r"""The console's shell: GameCore in place of Explorer, and the pieces of the trip to the Windows desktop.

Shared by `gamecore-shell` (the shell itself, unelevated) and `gamecore-session`
(the elevated supervisor). Stdlib only: both run before anything else.

The shell is HKLM `Winlogon\Shell` (install/steps/set-kiosk-shell.ps1). The
per-user values are not read on Windows 11 Home: HKCU `Winlogon\Shell` is
mapped to HKLM only (`IniFileMapping\system.ini\boot\Shell` = `SYS:`), and the
"Custom User Interface" policy (HKCU `Policies\System\Shell`) is ignored there
too, both measured on the owner's PC. Explorer takes the desktop (taskbar)
only when HKLM `Shell` names it at the moment it starts, so Desktop mode
points it at Explorer for the start and back (kiosk_desktop.DesktopTrip).
`KIOSK_VALUE` holds the GameCore command: the installer's record of what the
shell must be, re-armed by every session start.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Callable, Protocol

log = logging.getLogger("gamecore-shell")

WINLOGON_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
SHELL_VALUE = "Shell"
GAMECORE_KEY = r"SOFTWARE\GameCore"
KIOSK_VALUE = "KioskShell"
EXPLORER = "explorer.exe"

# Created by gamecore-shell, set by gamecore-session (same user, higher label).
DESKTOP_EVENT = "Local\\GameCoreShellDesktop"
SHELL_MUTEX = "Local\\GameCoreShell"
# gamecore-session's own mutex: present while the elevated session runs.
SESSION_MUTEX = "Local\\GameCoreSession"

EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
SM_SHUTTINGDOWN = 0x2000
TASKBAR_CLASS = "Shell_TrayWnd"
# The taskbar's own "Exit Explorer" command (Ctrl+Shift+right-click on it).
WM_EXIT_EXPLORER = 0x0400 + 436


class Registry(Protocol):
    """Registry values under one root; tests hand in a dict-backed fake."""

    def get(self, key: str, name: str) -> str | None: ...
    def set(self, key: str, name: str, value: str) -> None: ...


class WinRegistry:
    """A real hive: HKLM for the shell (writes need the elevated session), HKCU for the wallpaper."""

    def __init__(self, machine: bool = True):
        import winreg
        self._root = winreg.HKEY_LOCAL_MACHINE if machine else winreg.HKEY_CURRENT_USER

    def get(self, key: str, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(self._root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
                return str(winreg.QueryValueEx(handle, name)[0])
        except OSError:
            return None

    def set(self, key: str, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(self._root, key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def kiosk_command(registry: Registry) -> str | None:
    """The GameCore shell command when the installer made GameCore the shell (KIOSK=1), else None."""
    return registry.get(GAMECORE_KEY, KIOSK_VALUE) or None


def point_shell(registry: Registry, command: str) -> None:
    registry.set(WINLOGON_KEY, SHELL_VALUE, command)


def rearm(registry: Registry) -> bool:
    """Make HKLM `Shell` GameCore's again if a trip, a crash or anything else changed it. True when it did."""
    command = kiosk_command(registry)
    current = registry.get(WINLOGON_KEY, SHELL_VALUE)
    if not command or current == command:
        return False
    point_shell(registry, command)
    log.warning("shell was %r; put back to %s", current, command)
    return True


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    for name in ("OpenEventW", "OpenMutexW", "CreateEventW", "CreateMutexW"):
        getattr(k, name).restype = ctypes.c_void_p
    return k


def signal_event(name: str) -> bool:
    """Set a named event if someone created it. False when nobody listens."""
    k = _kernel32()
    handle = k.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(k.SetEvent(ctypes.c_void_p(handle)))
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


def mutex_exists(name: str) -> bool:
    """True when a copy holds `name`; access denied also means it exists (an elevated owner)."""
    k = _kernel32()
    handle = k.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        k.CloseHandle(ctypes.c_void_p(handle))
        return True
    return ctypes.get_last_error() == ERROR_ACCESS_DENIED


def session_ending() -> bool:
    """True once Windows signs out or shuts down: the interface exits 0 then too, not for Desktop mode."""
    return bool(ctypes.WinDLL("user32").GetSystemMetrics(SM_SHUTTINGDOWN))


def taskbar_window() -> int:
    """The taskbar's window in this session, or 0: Explorer runs as the desktop when it is there."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = ctypes.c_void_p
    return user32.FindWindowW(TASKBAR_CLASS, None) or 0


def ask_explorer_to_exit(window: int) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    return bool(user32.PostMessageW(ctypes.c_void_p(window), WM_EXIT_EXPLORER, 0, 0))


def wait_until(condition: Callable[[], bool], timeout_s: float, sleep: Callable[[float], None],
               step_s: float = 0.25) -> bool:
    """Poll `condition` until true or `timeout_s` spent. Returns its last answer."""
    waited = 0.0
    while not condition():
        if waited >= timeout_s:
            return False
        sleep(step_s)
        waited += step_s
    return True
