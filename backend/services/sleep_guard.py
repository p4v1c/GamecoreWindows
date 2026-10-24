"""Close every game before ANY sleep, not only Rest mode's.

The power button, the Start menu, a lid or an idle timer all put Windows to
sleep without asking GameCore. Windows tells the backend first
(`platform/sleep.py`, the suspend event) and waits about 2 s for the answer
before sleeping anyway; there is no lock that buys more, as logind's delay
inhibitor did on Linux. Ending a game is one `TerminateJobObject`, so the
sweep fits: it refuses launches, ends every session, and the event handler
returns.

The Rest mode button goes through here too: its own sweep has already
emptied the box, so this one finds nothing.
"""
import asyncio
import logging

from . import bt_wake, launch, rest_mode

log = logging.getLogger(__name__)

#: Kept under the ~2 s Windows waits for the suspend event handler.
SLEEP_EXIT_WAIT_SECS = 0.8
SLEEP_FORCE_WAIT_SECS = 0.3
SLEEP_BUDGET_SECS = 1.5

_swept_for_sleep = False


async def _sweep() -> None:
    survivors = await rest_mode.close_everything(SLEEP_EXIT_WAIT_SECS, SLEEP_FORCE_WAIT_SECS)
    if survivors:
        log.error("sleep_guard: %s still alive as the box goes to sleep", survivors)


async def before_sleep() -> None:
    """The sleep is announced: refuse launches, close everything and turn the
    pads off (together, inside the budget)."""
    global _swept_for_sleep
    _swept_for_sleep = True
    launch.close_gate(rest_mode.GATE_REASON)
    try:
        # One failing never cuts the other short.
        results = await asyncio.wait_for(
            asyncio.gather(_sweep(), bt_wake.turn_off_pads(), return_exceptions=True),
            SLEEP_BUDGET_SECS)
        for step, result in zip(("sweep", "pads off"), results):
            if isinstance(result, Exception):
                log.error("sleep_guard: %s failed", step, exc_info=result)
    except asyncio.TimeoutError:
        log.error("sleep_guard: sweep not done in %ss; the box sleeps anyway, "
                  "finishing after the wake", SLEEP_BUDGET_SECS)


async def after_wake() -> None:
    """Sweep again after a sleep: one that outran the sweep, a launch that slipped in."""
    global _swept_for_sleep
    if _swept_for_sleep:
        _swept_for_sleep = False
        await rest_mode.close_everything(SLEEP_EXIT_WAIT_SECS, SLEEP_FORCE_WAIT_SECS)
