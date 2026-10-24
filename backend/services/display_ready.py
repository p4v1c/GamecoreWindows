"""After a wake: wait until the screen can actually show something.

Windows says an output is back once a monitor on an adapter attached to the
desktop is active again (`platform/display.py` `display_ready`). A 200 ms poll
costs nothing for 10 s.

The link being up is not the picture being up: a TV keeps "no signal" on
screen for a moment after it, which is what the settle delay is for
(`standby.json` `wake_settle_secs`, Settings → System).
"""
import asyncio
import logging
from collections.abc import Callable

from .platform import display as platform_display

log = logging.getLogger(__name__)

POLL_SECS = 0.2
#: Past this the splash starts anyway: a box with no screen must still wake.
WAIT_MAX_SECS = 10.0


def _probe() -> bool:
    try:
        return platform_display.display_ready()
    except OSError:
        return False


async def wait_for_display(settle: float = 0.0, probe: Callable[[], bool] | None = None,
                           max_wait: float = WAIT_MAX_SECS) -> bool:
    """Wait for an active monitor (at most `max_wait`), then `settle` more.

    Returns False when it gave up waiting; the caller starts anyway.
    """
    probe = probe or _probe
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait
    ready = await asyncio.to_thread(probe)
    while not ready and loop.time() < deadline:
        await asyncio.sleep(POLL_SECS)
        ready = await asyncio.to_thread(probe)
    if not ready:
        log.warning("display_ready: no active monitor after %ss; waking the UI anyway",
                    max_wait)
    if settle > 0:
        await asyncio.sleep(settle)
    return ready
