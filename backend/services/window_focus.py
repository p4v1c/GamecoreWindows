"""Who is in front: a game coming forward, the interface over a game put down.

Windows gives the foreground to the process the player last touched, and the
backend is never that process; `platform/window_control.py` takes it anyway.
Best effort by design: a game merely behind the interface is still reachable
with one more press, and a resume or a suspend that raised here after it had
happened would be a lie about what happened.
"""
import asyncio
import logging
import time

from .platform import window_control

log = logging.getLogger(__name__)

#: The interface's process images: the packaged app (`productName` in
#: electron/package.json, pinned by a test) and the development `electron.exe`.
SHELL_EXECUTABLES = ("GameCore.exe", "electron.exe")

#: How long a new game gets to open its first window before nobody waits.
PRESENT_TIMEOUT_SECS = 45.0
POLL_SECS = 0.25


def game_windows(job) -> list[int]:
    """The app windows of every process in the session's job."""
    return window_control.top_level_windows(set(job.pids())) if job is not None else []


def wait_for_window(job, timeout: float) -> int:
    """The game's first window once it appears; 0 at the timeout or when the
    game has exited. Blocking: run it off the event loop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = game_windows(job)
        if windows:
            return windows[0]
        if not job.alive():
            return 0
        time.sleep(POLL_SECS)
    return 0


def _activate_sync(system_id: str, job) -> bool:
    """Raise every window of the resumed game, as it left them.

    Several windows (one per screen and player) keep their layout; the last
    one raised, the topmost before the suspend, gets the keyboard focus.
    """
    windows = game_windows(job)
    if not windows:
        log.info("window_focus[%s]: no window found for the resumed session", system_id)
        return False
    raised = [window_control.bring_to_front(hwnd) for hwnd in reversed(windows)]
    log.info("window_focus[%s]: %d resumed window(s) raised: %s",
             system_id, len(windows), raised[-1])
    return raised[-1]


def _hide_sync(system_id: str) -> bool:
    """Bring the interface over a session about to be frozen.

    Before the freeze, never after: a frozen window in front does not answer
    the activation messages, and Windows then refuses to put it behind
    (measured on the real PC). The game's own window is not touched.
    """
    shells = window_control.windows_of_executables(SHELL_EXECUTABLES)
    if not shells:
        log.info("window_focus[%s]: the interface's own window was not found "
                 "— the suspended game may stay on top", system_id)
        return False
    raised = window_control.bring_to_front(shells[0])
    log.info("window_focus[%s]: interface raised over the frozen session: %s",
             system_id, raised)
    return raised


def _present_sync(system_id: str, job, timeout: float) -> bool:
    hwnd = wait_for_window(job, timeout)
    if not hwnd:
        log.info("window_focus[%s]: no window within %.0f s", system_id, timeout)
        return False
    raised = window_control.bring_to_front(hwnd)
    log.info("window_focus[%s]: new window brought to the front: %s", system_id, raised)
    return raised


async def activate(system_id: str, job) -> bool:
    """Bring a resumed session's window back to the front."""
    try:
        return await asyncio.to_thread(_activate_sync, system_id, job)
    except Exception as e:
        log.warning("window_focus[%s]: %s", system_id, e)
        return False


async def hide(system_id: str) -> bool:
    """Raise the interface over a session about to be suspended."""
    try:
        return await asyncio.to_thread(_hide_sync, system_id)
    except Exception as e:
        log.warning("window_focus[%s]: %s", system_id, e)
        return False


async def present(system_id: str, job, timeout: float = PRESENT_TIMEOUT_SECS) -> bool:
    """Bring a new game's first window to the front once it opens.

    Windows keeps the foreground for the process that received the last
    input (the interface, when the player pressed launch); a window opened by
    a game the backend started may then stay behind it.
    """
    try:
        return await asyncio.to_thread(_present_sync, system_id, job, timeout)
    except Exception as e:
        log.warning("window_focus[%s]: %s", system_id, e)
        return False
