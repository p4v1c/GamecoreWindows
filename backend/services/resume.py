"""Notice the box sleeping and waking, and put back what a sleep can break.

Windows reports both through `platform/sleep.py`'s callbacks, for every sleep,
Rest mode or not (power button, Start menu, lid, idle timer):

- S3 and hibernation: `SUSPEND` (Windows waits about 2 s for the handler, so
  the games are closed under it: services/sleep_guard.py), then `RESUME`;
- Modern Standby: Rest mode announces the sleep itself (`going_to_sleep()`)
  before switching the display off, since Windows freezes the backend within
  a second and says nothing first. The console display coming back on is the
  wake; so is a press the backend sees while asleep (a display that never went
  off). A display back on within seconds of Rest putting it out is input
  settling: Rest puts it out again (`rest_mode.is_early_wake()`). A display
  going off outside Rest mode is standby's own screen-off and means nothing
  here.

A wake puts the screen, pads and launch gate back. The interface learns it
from the `power:resumed` event, and from `GET /api/power/state` after a
socket that dropped across the sleep.
"""
import asyncio
import logging
import time

from .. import ws
from . import (bt_wake, display, display_ready, gamepad_monitor, rest_mode, sleep_guard,
               standby, wake_source, window_focus)
from .platform import sleep

log = logging.getLogger(__name__)

SLEEP, WAKE = "sleep", "wake"
#: How long Windows' power thread is held on a suspend event: just under the
#: ~2 s Windows grants, so the sweep finishes before the sleep.
SUSPEND_HOLD_SECS = 1.8
#: A press this soon after the sleep began is the one that chose Rest mode
#: (its release, a stick at rest), not a wake.
PRESS_WAKE_GUARD_SECS = 3.0

_resumes = 0
# From the sleep announcement to the UI being told the screen is back.
_sleeping = False
_slept_at = 0.0


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
    global _sleeping, _slept_at
    _sleeping = True
    _slept_at = time.monotonic()
    await ws.broadcast("power:sleeping", {})


def sleep_called_off() -> None:
    """Windows refused the sleep Rest mode announced: nothing to wake from.

    The interface lifts its black layer on the refusal, and `GET
    /api/power/state` no longer reports a sleep.
    """
    global _sleeping
    _sleeping = False


def classify(event: str, *, model: str, resting: bool, sleeping: bool) -> str | None:
    """What a power event means: `SLEEP`, `WAKE`, or None (nothing to do). Pure.

    A sleep is acted on once and a wake only after a sleep, so a Modern
    Standby PC that also sends the suspend and resume callbacks is not swept
    or woken twice.
    """
    if event == sleep.SUSPEND:
        return None if sleeping else SLEEP
    if event == sleep.RESUME:
        return WAKE if sleeping else None
    if model != sleep.MODERN_STANDBY:
        return None
    if event == sleep.DISPLAY_OFF and resting and not sleeping:
        return SLEEP
    if event == sleep.DISPLAY_ON and sleeping:
        return WAKE
    return None


async def _step(name: str, coro) -> None:
    # One failed step must not cost the others: a box whose display call fails
    # still needs its pads and its launch gate back.
    try:
        await coro
    except Exception:
        log.exception("resume: %s failed", name)


async def going_to_sleep() -> None:
    """The sleep is announced: cover the interface, then close every game and
    turn the pads off. Rest mode calls it before asking for the sleep."""
    log.info("resume: the box is going to sleep")
    await _step("announce the sleep", announce_sleep())
    await sleep_guard.before_sleep()


async def on_resume() -> None:
    """Everything a wake owes the player. The UI hears of it last of the
    visible steps: once the TV shows a picture, so the splash plays whole."""
    global _resumes, _sleeping
    log.info("resume: the box is awake")
    # First: anything that runs before it may itself bump a counter.
    await _step("wake source", asyncio.to_thread(wake_source.report))
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
    await _step("interface in front", window_focus.raise_interface())
    gamepad_monitor.request_reprofile()
    await _step("bluetooth wake", bt_wake.set_wake_permissions())
    # The record the next wake is compared with.
    await _step("wake record", asyncio.to_thread(wake_source.remember))


_lock: asyncio.Lock | None = None


def _event_lock() -> asyncio.Lock:
    """One lock for Windows' events and the dark rest's own, created in the loop."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def press_guard_over() -> bool:
    """The press that chose Rest mode is behind us, and the display is not
    still settling into the standby: a new press is a wake."""
    return time.monotonic() - _slept_at >= PRESS_WAKE_GUARD_SECS and not rest_mode.settling()


def is_press_a_wake() -> bool:
    """A pad press now ends a Modern Standby rest: the backend runs, so the PC is awake."""
    return _sleeping and rest_mode.in_modern_standby() and press_guard_over()


async def woken_by_press() -> None:
    """The wake for a press seen while asleep, when Windows lit no display to say so."""
    await handle(sleep.DISPLAY_ON, sleep.MODERN_STANDBY, _event_lock())


async def handle(event: str, model: str, lock: asyncio.Lock) -> None:
    """Act on one power event, one at a time: a wake waits for its sleep's sweep."""
    async with lock:
        action = classify(event, model=model, resting=rest_mode.is_resting(),
                          sleeping=_sleeping)
        if action == SLEEP:
            await going_to_sleep()
        elif action == WAKE and event == sleep.DISPLAY_ON and rest_mode.is_early_wake():
            await rest_mode.sleep_again()
        elif action == WAKE:
            await on_resume()


async def run() -> None:
    """Follow Windows' sleeps and wakes for the life of the backend."""
    loop = asyncio.get_running_loop()
    lock = _event_lock()
    try:
        model = await asyncio.to_thread(sleep.power_model)
    except OSError as e:
        log.warning("resume: could not read this PC's sleep model: %s", e)
        model = sleep.NO_SLEEP
    log.info("resume: sleep model %s", model)

    def on_event(event: str) -> None:
        # Windows' power thread. Held on a suspend so the games are closed first.
        future = asyncio.run_coroutine_threadsafe(handle(event, model, lock), loop)
        if event == sleep.SUSPEND:
            try:
                future.result(SUSPEND_HOLD_SECS)
            except Exception as e:
                log.warning("resume: the sleep went on before the sweep ended (%s)",
                            type(e).__name__)

    try:
        stop = sleep.watch(on_event)
    except OSError as e:
        log.warning("resume: a sleep or a wake will not be noticed: %s", e)
        return
    try:
        await _step("wake record", asyncio.to_thread(wake_source.remember))
        await asyncio.Event().wait()
    finally:
        stop()
