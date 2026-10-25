"""The kiosk shell's decisions: keep the session task running, open and close the Windows desktop.

Used by `gamecore-shell` (the console user's shell) and, when that shell is
not running (an update ended it), by `gamecore-session` for Desktop mode.
Stdlib only; every effect is injected, so tests drive it.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from typing import Callable

import kiosk_shell as ks

log = logging.getLogger("gamecore-shell")

TASK = r"\GameCore\Session"
# The session's mutex appears ~1 s after the task starts; a task that started
# and died is retried at this pace, never in a loop.
SESSION_RETRY_S = 30.0
EXPLORER_UP_TIMEOUT_S = 20.0
EXPLORER_EXIT_TIMEOUT_S = 8.0
CREATE_NO_WINDOW = 0x08000000
COLOR_DESKTOP = 1
SPI_SETDESKWALLPAPER = 0x0014
DESKTOP_KEY = r"Control Panel\Desktop"

Run = Callable[[list[str]], int]


def run_hidden(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                              timeout=30).returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("%s failed: %s", argv[0], e)
        return -1


def start_explorer() -> None:
    subprocess.Popen([ks.EXPLORER], close_fds=True)


def session_id() -> int:
    session = ctypes.c_ulong()
    ctypes.WinDLL("kernel32").ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    return session.value


def black_desktop() -> None:
    """Black behind the interface for this sign-in only: nothing is saved to the profile."""
    user32 = ctypes.WinDLL("user32")
    colors, values = (ctypes.c_int * 1)(COLOR_DESKTOP), (ctypes.c_ulong * 1)(0)
    user32.SetSysColors(1, colors, values)
    user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, ctypes.c_wchar_p(""), 0)


def restore_wallpaper(registry: ks.Registry) -> None:
    """The player's own wallpaper again, read from the profile black_desktop left alone."""
    path = registry.get(DESKTOP_KEY, "Wallpaper") or ""
    ctypes.WinDLL("user32").SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, ctypes.c_wchar_p(path), 0)


class Shell:
    """What the kiosk shell decides. Every effect is injected, so tests drive it."""

    def __init__(self, registry: ks.Registry, run: Run = run_hidden,
                 session_running: Callable[[], bool] = lambda: ks.mutex_exists(ks.SESSION_MUTEX),
                 taskbar: Callable[[], int] = ks.taskbar_window,
                 explorer: Callable[[], None] = start_explorer,
                 exit_explorer: Callable[[int], bool] = ks.ask_explorer_to_exit,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.registry = registry
        self._run, self._session_running, self._taskbar = run, session_running, taskbar
        self._explorer, self._exit_explorer = explorer, exit_explorer
        self._clock, self._sleep = clock, sleep
        self.next_session_try = 0.0

    def keep_session(self) -> None:
        """Start the elevated session task unless it runs; at most once per SESSION_RETRY_S."""
        now = self._clock()
        if now < self.next_session_try or self._session_running():
            return
        self.next_session_try = now + SESSION_RETRY_S
        code = self._run(["schtasks.exe", "/run", "/tn", TASK])
        log.info("session task started (schtasks exit %s)", code)

    def open_desktop(self) -> bool:
        """Explorer as the desktop, the GameCore shell put back once its taskbar is up."""
        if self._taskbar():
            return True
        replaced = ks.point_shell_at_explorer(self.registry)
        try:
            restore_wallpaper(self.registry)
            self._explorer()
            up = ks.wait_until(lambda: bool(self._taskbar()), EXPLORER_UP_TIMEOUT_S, self._sleep)
        finally:
            if replaced:
                ks.rearm(self.registry)
        log.info("Windows desktop %s", "open" if up else "did not show a taskbar in time")
        return up

    def close_desktop(self) -> bool:
        """Back to GameCore: Explorer leaves, as the Plasma session does on Linux."""
        window = self._taskbar()
        if not window:
            return True
        self._exit_explorer(window)
        gone = ks.wait_until(lambda: not self._taskbar(), EXPLORER_EXIT_TIMEOUT_S, self._sleep)
        if not gone:
            self._run(["taskkill.exe", "/f", "/im", ks.EXPLORER, "/fi", f"SESSION eq {session_id()}"])
        log.info("Windows desktop closed%s", "" if gone else " (forced)")
        black_desktop()
        return gone
