"""Notice the box waking up, and put back what a sleep can break.

The signal is logind's `PrepareForSleep`, read from `gdbus monitor` on
the system bus. logind sends it for every suspend, Rest mode or not (power
button, desktop menu, lid, idle), and the backend is the process that has to
act on it. `true` closes every game while sleep_guard's delay lock holds the
sleep back; `false` puts the screen, pads and launch gate back. The interface learns
it from the `power:resumed` event, and from `GET /api/power/state` after a
socket that dropped across the sleep.
"""
import asyncio
import logging
import re
import shutil

from .. import ws
from . import (bt_wake, display, display_ready, gamepad_monitor, rest_mode, sleep_guard,
               standby, wake_source)

log = logging.getLogger(__name__)

#: Signals only, from logind only: no eavesdropping, so it runs unprivileged.
LOGIND_MONITOR = ("gdbus", "monitor", "--system", "--dest", "org.freedesktop.login1",
                  "--object-path", "/org/freedesktop/login1")
_SIGNAL_RE = re.compile(r"\.PrepareForSleep \((true|false),?\)")
#: A monitor that died (bus restart) is started again after this long.
RESTART_SECS = 30.0

_resumes = 0
# From the sleep announcement to the UI being told the screen is back.
_sleeping = False


def resume_count() -> int:
    """How many wakes this backend has seen; the UI replays the splash on a change.

    Counted when the screen is back, not at the wake, so a client that reads it
    after a reconnect never starts the splash on a TV still finding the signal.
    """
    return _resumes


def is_sleeping() -> bool:
    return _sleeping


async def announce_sleep() -> None:
    """`power:sleeping`: the UI covers everything in black until `power:resumed`."""
    global _sleeping
    _sleeping = True
    await ws.broadcast("power:sleeping", {})


def parse_sleep_signal(line: str) -> bool | None:
    """True for "going to sleep", False for "awake again", None for anything else."""
    m = _SIGNAL_RE.search(line)
    return None if m is None else m.group(1) == "true"


async def _step(name: str, coro) -> None:
    # One failed step must not cost the others: a box with no display tool
    # still needs its pads and its launch gate back.
    try:
        await coro
    except Exception:
        log.exception("resume: %s failed", name)


async def on_resume() -> None:
    """Everything a wake owes the player. The UI hears of it last of the
    visible steps: once the TV shows a picture, so the splash plays whole."""
    global _resumes, _sleeping
    log.info("resume: the box is awake")
    # First: anything that runs before it may itself bump a counter.
    wake_source.report()
    await _step("close what survived the sleep", sleep_guard.after_wake())
    rest_mode.woke()
    await _step("screen", standby.exit_standby())
    await _step("wait for the link", display_ready.wait_for_display())
    # After the link: Windows lists no mode for an output it has not re-sensed.
    await _step("display mode", display.restore_preferred())
    # The link is up before the picture: the TV still says "no signal" a moment.
    await asyncio.sleep(max(0.0, float(standby.load_config().get("wake_settle_secs", 0))))
    _resumes += 1
    _sleeping = False
    await _step("announce", ws.broadcast("power:resumed", {"resumes": _resumes}))
    gamepad_monitor.request_reprofile()
    await _step("bluetooth wake", bt_wake.set_wake_permissions())


async def _read(proc) -> None:
    async for raw in proc.stdout:
        state = parse_sleep_signal(raw.decode(errors="replace"))
        if state is True:
            log.info("resume: logind says the box is going to sleep")
            wake_source.remember()
            await _step("announce the sleep", announce_sleep())
            await sleep_guard.before_sleep()
        elif state is False:
            await on_resume()


async def run() -> None:
    """Follow logind for the life of the backend."""
    if not shutil.which(LOGIND_MONITOR[0]):
        log.warning("resume: gdbus not found; a wake from sleep will not be noticed "
                    "(install glib2)")
        return
    while True:
        proc = await asyncio.create_subprocess_exec(
            *LOGIND_MONITOR, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        # The lock only while someone listens: a lock with no monitor behind it
        # would delay every sleep for nothing.
        await sleep_guard.take()
        try:
            await _read(proc)
        finally:
            await sleep_guard.release()
            if proc.returncode is None:
                await sleep_guard.kill_bounded(proc)
        # Python 3.11's wait_for can swallow a cancel that lands while it
        # finishes (release() above); the task then slept on and the backend's
        # shutdown waited for it. Measured: a hung lifespan in the test suite.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        log.warning("resume: the logind monitor exited (%s); restarting in %ss",
                    proc.returncode, RESTART_SECS)
        await asyncio.sleep(RESTART_SECS)
