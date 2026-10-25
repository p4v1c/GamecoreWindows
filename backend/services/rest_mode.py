"""Rest mode: close everything that runs, then put the box to sleep.

The order is the feature. A box that sleeps with a game alive wakes into it,
and a frozen emulator holding the GPU across a suspend is the case most likely
to come back broken. So nothing sleeps until every session, suspended ones
included, is gone: killed, waited for, and its job terminated again if it
lingers.

Launches are refused from the first step to the wake (`launch.close_gate`),
so a launch racing the menu cannot start a game after the sweep.

The interface is covered and the pads turned off before the sleep is asked
for (`resume.going_to_sleep()`), as logind's delay lock made happen on Linux:
a Modern Standby PC freezes the backend within a second of its display going
off, and announces nothing first (dev-log D62).

On Modern Standby the display goes off only once keyboard, mouse, touchpad and
pads have been still for a moment: any input in the first second ends the
standby (measured: the touchpad click that chose Rest, Kernel-Power 507 after
0.97 s). A display that still comes back within seconds is put out again
(dev-log D65).
"""
import asyncio
import logging
import time

from . import launch
from .errors import ServiceError
from .game_session import Session
from .platform import power, sleep
from .process_manager import process_manager

log = logging.getLogger(__name__)

#: A launch already past its gate gets this long to spawn, so the sweep sees it.
#: Covers the pad-profile wait (8 s) and the pack prepare hook (3 s).
LAUNCH_SETTLE_SECS = 15.0
#: How long killed sessions may take to disappear before they are forced.
EXIT_WAIT_SECS = 8.0
#: The grace after the forced kill, before sleeping anyway.
FORCE_WAIT_SECS = 2.0
POLL_SECS = 0.1
#: Reopens launches if no wake is ever reported (S3 sleep accepted but skipped).
REOPEN_AFTER_SECS = 60.0

#: Keyboard, mouse, touchpad and pads still this long before the display goes off.
INPUT_QUIET_SECS = 2.0
#: A hand left on the touchpad does not hold the box awake past this.
INPUT_QUIET_MAX_SECS = 15.0
#: A display back on this soon after Rest put it out is input settling, not a wake.
EARLY_WAKE_SECS = 5.0
#: Times the display is put out again before such a wake counts as real.
MAX_RESLEEPS = 3

GATE_REASON = "The box is going to sleep."
#: Shown in the menu row: Windows refused (no sleep state, sleep disabled by policy).
REFUSED = "This PC can’t sleep right now. Turn it off instead."

_resting = False
_modern_standby = False
_reopen_task: asyncio.Task | None = None
_display_off_at = 0.0
_resleeps = 0


def is_resting() -> bool:
    """True from the menu press until the box is awake again."""
    return _resting


def _resident() -> list[Session]:
    """Every live session: the one on screen and the frozen ones."""
    fg = process_manager.foreground_session
    return ([fg] if fg else []) + process_manager.background_sessions


async def _wait_gone(sessions: list[Session], budget: float) -> list[Session]:
    """Poll until no session's job is alive or `budget` runs out. Returns survivors."""
    deadline = time.monotonic() + budget
    alive = [s for s in sessions if s.alive()]
    while alive and time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECS)
        alive = [s for s in alive if s.alive()]
    return alive


async def _settle_launch() -> None:
    """Let a launch that passed its gate before ours finish spawning."""
    deadline = time.monotonic() + LAUNCH_SETTLE_SECS
    while launch.is_launching() and time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECS)
    if launch.is_launching():
        log.warning("rest: a launch is still preparing after %ss; sweeping anyway",
                    LAUNCH_SETTLE_SECS)


def _force_kill(sessions: list[Session]) -> None:
    for s in sessions:
        try:
            s.job.terminate()
        except OSError:
            pass


async def close_everything(exit_wait: float | None = None,
                           force_wait: float | None = None) -> list[int]:
    """Kill every session and wait for each job to be empty.

    The waits default to Rest mode's; a sleep from elsewhere passes shorter
    ones (services/sleep_guard.py). Returns the first pids of the sessions
    that outlived the forced kill (normally none).
    """
    exit_wait = EXIT_WAIT_SECS if exit_wait is None else exit_wait
    force_wait = FORCE_WAIT_SECS if force_wait is None else force_wait
    sessions = [s for s in _resident() if s.job is not None]
    for s in sessions:
        log.info("rest: closing %s (%s)", s.game_key or "?", s.system_id)
        try:
            await process_manager.kill(s.session_id)
        except Exception:
            log.exception("rest: could not close %s", s.game_key or "?")
    survivors = await _wait_gone(sessions, exit_wait)
    if not survivors:
        return []
    log.warning("rest: %d session(s) still alive after %ss, forcing: %s",
                len(survivors), exit_wait, [s.pid for s in survivors])
    _force_kill(survivors)
    survivors = await _wait_gone(survivors, force_wait)
    if survivors:
        log.error("rest: sessions %s survived the forced kill; sleeping anyway",
                  [s.pid for s in survivors])
    return [s.pid for s in survivors]


def _seconds_since_input() -> float:
    """The quieter of Windows' input (keyboard, mouse, touchpad) and the pads'."""
    from . import standby       # standby imports this module
    try:
        windows = power.seconds_since_input()
    except OSError:
        windows = INPUT_QUIET_SECS
    return min(windows, standby.seconds_since_pad_input())


async def _wait_input_quiet() -> None:
    """Return once nobody has touched an input for INPUT_QUIET_SECS (or the cap passed)."""
    deadline = time.monotonic() + INPUT_QUIET_MAX_SECS
    while True:
        idle = await asyncio.to_thread(_seconds_since_input)
        if idle >= INPUT_QUIET_SECS:
            return
        if time.monotonic() >= deadline:
            log.warning("rest: input still moving after %ss; sleeping anyway",
                        INPUT_QUIET_MAX_SECS)
            return
        await asyncio.sleep(max(POLL_SECS, INPUT_QUIET_SECS - idle))


async def _suspend() -> tuple[bool, str]:
    await _wait_input_quiet()
    # In a thread: an S3 sleep returns only after the wake, and the loop must
    # stay free for the suspend event (services/resume.py).
    return await asyncio.to_thread(sleep.suspend)


def settling() -> bool:
    """The display went off for a Modern Standby rest less than EARLY_WAKE_SECS ago."""
    return _modern_standby and time.monotonic() - _display_off_at < EARLY_WAKE_SECS


def is_early_wake() -> bool:
    """A display back on now is input settling: put it out again instead of waking."""
    return settling() and _resleeps < MAX_RESLEEPS


async def sleep_again() -> None:
    """Put the display out again after an early wake (`is_early_wake`)."""
    global _resleeps, _display_off_at
    _resleeps += 1
    log.info("rest: display back on %.1fs after going off; off again (%d/%d)",
             time.monotonic() - _display_off_at, _resleeps, MAX_RESLEEPS)
    # Still settling while input quietens: a press then is not a wake either.
    _display_off_at = time.monotonic()
    ok, detail = await _suspend()
    _display_off_at = time.monotonic()
    if not ok:
        log.warning("rest: could not put the display out again: %s", detail)


def in_modern_standby() -> bool:
    """Resting through Modern Standby: the next wake is the display coming back, or a press."""
    return _modern_standby


def woke() -> None:
    """The box is awake: launches are allowed again."""
    global _resting, _reopen_task, _modern_standby
    if _reopen_task is not None and not _reopen_task.done():
        _reopen_task.cancel()
    _reopen_task = None
    _resting = False
    _modern_standby = False
    launch.open_gate()


async def _reopen_later() -> None:
    await asyncio.sleep(REOPEN_AFTER_SECS)
    if _resting:
        log.warning("rest: no wake reported %ss after suspending; reopening launches",
                    REOPEN_AFTER_SECS)
        woke()


async def enter() -> dict:
    """Close everything, then suspend. Raises ServiceError when sleep is refused."""
    global _resting, _reopen_task, _modern_standby, _display_off_at, _resleeps
    from . import resume        # resume imports this module
    if _resting:
        raise ServiceError(409, "The box is already going to sleep.")
    _resting = True
    launch.close_gate(GATE_REASON)
    try:
        await _settle_launch()
        survivors = await close_everything()
        await resume.going_to_sleep()
        ok, detail = await _suspend()
    except BaseException:
        resume.sleep_called_off()
        woke()
        raise
    if not ok:
        resume.sleep_called_off()
        woke()
        log.warning("rest: suspend refused: %s", detail)
        raise ServiceError(503, REFUSED)
    log.info("rest: suspend requested (%s)", detail)
    if detail == sleep.MODERN_STANDBY:
        # The wake cannot be lost (the display, or a press): a timer here would
        # reopen launches while the PC sleeps, its clock still running.
        _modern_standby = True
        _display_off_at = time.monotonic()
        _resleeps = 0
    else:
        _reopen_task = asyncio.create_task(_reopen_later())
    return {"ok": True, "forced": len(survivors)}
