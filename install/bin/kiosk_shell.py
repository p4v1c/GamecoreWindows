"""The console user's shell: GameCore in place of Explorer, and the trip to the Windows desktop.

Shared by `gamecore-shell` (the shell itself, unelevated) and `gamecore-session`
(the elevated supervisor). Stdlib only: both run before anything else.

Winlogon starts the per-user `Shell` value (HKCU) at sign-in, on every edition.
Explorer decides it is the desktop, not a folder window, only when that value
names it; so Desktop mode points the value at Explorer while it starts, then
back (`open_desktop`). A crash in between would boot the next session on the
desktop: `REARM_VALUE` holds the GameCore command until the value is put back.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Callable, Protocol

log = logging.getLogger("gamecore-shell")

WINLOGON_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
SHELL_VALUE = "Shell"
GAMECORE_KEY = r"Software\GameCore"
REARM_VALUE = "ShellRearm"
EXPLORER = "explorer.exe"

# Created by gamecore-shell, set by gamecore-session (same user, higher label).
DESKTOP_EVENT = "Local\\GameCoreShellDesktop"
RETURN_EVENT = "Local\\GameCoreShellReturn"
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
    """HKCU values, as `gamecore-shell` needs them; tests hand in a dict-backed fake."""

    def get(self, key: str, name: str) -> str | None: ...
    def set(self, key: str, name: str, value: str) -> None: ...
    def delete(self, key: str, name: str) -> None: ...


class CurrentUserRegistry:
    """The real HKCU of the process's user (the console user, elevated or not)."""

    def get(self, key: str, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return str(winreg.QueryValueEx(handle, name)[0])
        except OSError:
            return None

    def set(self, key: str, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete(self, key: str, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except OSError:
            pass


def point_shell_at_explorer(registry: Registry) -> str | None:
    """Make Explorer the shell for the next Explorer start. Returns the GameCore command it replaced."""
    current = registry.get(WINLOGON_KEY, SHELL_VALUE)
    if not current or current.strip().lower() == EXPLORER:
        return None
    registry.set(GAMECORE_KEY, REARM_VALUE, current)
    registry.set(WINLOGON_KEY, SHELL_VALUE, EXPLORER)
    return current


def is_gamecore_shell(registry: Registry) -> bool:
    """True when the console user's shell is gamecore-shell (KIOSK=1), even mid-trip."""
    current = registry.get(WINLOGON_KEY, SHELL_VALUE) or ""
    return "gamecore-shell" in current or bool(registry.get(GAMECORE_KEY, REARM_VALUE))


def rearm(registry: Registry) -> bool:
    """Put the GameCore shell back if a desktop trip left Explorer in its place. True when it did."""
    command = registry.get(GAMECORE_KEY, REARM_VALUE)
    if not command:
        return False
    registry.set(WINLOGON_KEY, SHELL_VALUE, command)
    registry.delete(GAMECORE_KEY, REARM_VALUE)
    log.info("shell put back to %s", command)
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
