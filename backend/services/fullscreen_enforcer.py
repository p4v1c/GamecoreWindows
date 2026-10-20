"""Borderless fullscreen for apps that have no fullscreen command-line flag.

A tile in config/apps.json (or a pack's `fullscreen` block) asks for it:

    "fullscreen": { "timeout_s": 60 }

/games/launch fires `enforce()` as a background task: it waits for the first
window of the launched job, strips its frame, stretches it over its monitor
and brings it to the front. Borderless rather than exclusive: the
interface's alerts are a topmost window, which exclusive fullscreen hides.

`wm_class`, the Linux key that named the window, may still be present and is
not needed: the window is the job's own (`window_focus.game_windows`).
"""
import asyncio
import logging
import time

from . import window_focus
from .platform import window_control

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECS = 45.0
# An app that resizes itself once its content loads undoes the first try.
ATTEMPTS = 10
RETRY_SECS = 1.0


def _enforce_sync(system_id: str, job, timeout_s: float) -> bool:
    hwnd = window_focus.wait_for_window(job, timeout_s)
    if not hwnd:
        log.warning("fullscreen_enforcer[%s]: no window after %ss", system_id, timeout_s)
        return False
    for _ in range(ATTEMPTS):
        if window_control.covers_its_monitor(hwnd):
            window_control.bring_to_front(hwnd)
            log.info("fullscreen_enforcer[%s]: window is fullscreen", system_id)
            return True
        window_control.make_borderless_fullscreen(hwnd)
        time.sleep(RETRY_SECS)
    log.warning("fullscreen_enforcer[%s]: window never covered its monitor", system_id)
    return False


async def enforce(system_id: str, cfg: dict, job) -> bool:
    """Fire-and-forget: fullscreen the launched app's window once it appears."""
    if not cfg or job is None:
        return False
    try:
        timeout_s = float(cfg.get("timeout_s", DEFAULT_TIMEOUT_SECS))
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_SECS
    try:
        return await asyncio.to_thread(_enforce_sync, system_id, job, timeout_s)
    except Exception as e:
        log.warning("fullscreen_enforcer[%s]: %s", system_id, e)
        return False
