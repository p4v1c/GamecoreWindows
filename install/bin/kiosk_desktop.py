"""The kiosk shell's decisions: keep the session task running, and the Windows desktop round trip.

`Shell` is gamecore-shell's side (unelevated): start the session task, start
Explorer when asked. `DesktopTrip` is gamecore-session's side (elevated): it
alone may write HKLM `Shell`, so it points the shell at Explorer for the start
and back, and closes Explorer on the way back. Stdlib only; every effect is
injected, so tests drive both.
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
# A first Explorer start after sign-in builds the Start menu cache: seconds.
EXPLORER_UP_TIMEOUT_S = 45.0
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


def start_program(argv: list[str]) -> None:
    subprocess.Popen(argv, close_fds=True)


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


def restore_wallpaper(user_registry: ks.Registry) -> None:
    """The player's own wallpaper again, read from the profile black_desktop left alone."""
    path = user_registry.get(DESKTOP_KEY, "Wallpaper") or ""
    ctypes.WinDLL("user32").SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, ctypes.c_wchar_p(path), 0)


class Shell:
    """gamecore-shell's decisions. Every effect is injected, so tests drive it."""

    def __init__(self, user_registry: ks.Registry, run: Run = run_hidden,
                 session_running: Callable[[], bool] = lambda: ks.mutex_exists(ks.SESSION_MUTEX),
                 start: Callable[[list[str]], None] = start_program,
                 ending: Callable[[], bool] = ks.session_ending,
                 clock: Callable[[], float] = time.monotonic):
        self.user_registry = user_registry
        self._run, self._session_running, self._start = run, session_running, start
        self._ending, self._clock = ending, clock
        self.next_session_try = 0.0

    def keep_session(self) -> None:
        """Start the elevated session task unless it runs; at most once per SESSION_RETRY_S."""
        now = self._clock()
        if now < self.next_session_try or self._ending() or self._session_running():
            return
        self.next_session_try = now + SESSION_RETRY_S
        code = self._run(["schtasks.exe", "/run", "/tn", TASK])
        log.info("session task started (schtasks exit %s)", code)

    def start_explorer(self) -> None:
        """Explorer unelevated, as the player's desktop: gamecore-session has pointed HKLM `Shell` at it."""
        restore_wallpaper(self.user_registry)
        self._start([ks.EXPLORER])
        log.info("Explorer started for Desktop mode")


class DesktopTrip:
    """gamecore-session's half of Desktop mode, elevated. Every effect is injected, so tests drive it."""

    def __init__(self, registry: ks.Registry, run: Run = run_hidden,
                 ask_shell: Callable[[], bool] = lambda: ks.signal_event(ks.DESKTOP_EVENT),
                 start: Callable[[list[str]], None] = start_program,
                 taskbar: Callable[[], int] = ks.taskbar_window,
                 exit_explorer: Callable[[int], bool] = ks.ask_explorer_to_exit,
                 ending: Callable[[], bool] = ks.session_ending,
                 sleep: Callable[[float], None] = time.sleep):
        self.registry = registry
        self._run, self._ask_shell, self._start = run, ask_shell, start
        self._taskbar, self._exit_explorer = taskbar, exit_explorer
        self._ending, self._sleep = ending, sleep

    def open(self) -> bool:
        """Explorer as the desktop with its taskbar; HKLM `Shell` is GameCore's again once it is up."""
        if self._taskbar():
            return True
        if self._ending():
            # The interface exits 0 on a sign-out too: that is not Desktop mode.
            log.info("Windows is signing out: no desktop to open")
            return False
        command = ks.kiosk_command(self.registry)
        if command:
            ks.point_shell(self.registry, ks.EXPLORER)
        try:
            if not self._ask_shell():
                self._start([ks.EXPLORER])    # no gamecore-shell (an update ended it, or KIOSK=0)
            up = ks.wait_until(lambda: bool(self._taskbar()), EXPLORER_UP_TIMEOUT_S, self._sleep)
        finally:
            if command:
                ks.point_shell(self.registry, command)
        if up:
            log.info("Windows desktop open")
            return True
        # Never leave the player with nothing: Task Manager can run anything (File, Run new task).
        log.error("Explorer showed no taskbar in %.0f s: opening Task Manager", EXPLORER_UP_TIMEOUT_S)
        self._start(["taskmgr.exe"])
        return False

    def close(self) -> bool:
        """Back to GameCore: Explorer leaves, as the Plasma session does on Linux. KIOSK=0 keeps it."""
        if not ks.kiosk_command(self.registry):
            return True
        window = self._taskbar()
        if window:
            self._exit_explorer(window)
        gone = ks.wait_until(lambda: not self._taskbar(), EXPLORER_EXIT_TIMEOUT_S, self._sleep)
        # Folder windows and an Explorer that would not exit: every explorer.exe of this session.
        self._run(["taskkill.exe", "/f", "/im", ks.EXPLORER, "/fi", f"SESSION eq {session_id()}"])
        log.info("Windows desktop closed%s", "" if gone else " (forced)")
        return gone
