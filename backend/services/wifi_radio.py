"""The Wi-Fi radio switch, read and set. The WinRT calls: platform/radios.py.

The radio is Windows' own Wi-Fi switch (quick settings). A radio blocked below
Windows (hardware switch, firmware) reads as off and cannot be turned on here.
"""
from __future__ import annotations

import asyncio
import logging

from .errors import ServiceError
from .platform import radios

log = logging.getLogger(__name__)

# The radio manager answers from memory; anything slower is a stuck service.
RADIO_SECS = 10.0


async def radio_enabled() -> bool | None:
    """Whether the Wi-Fi radio is on. None when there is none, or it cannot be told."""
    try:
        return await asyncio.wait_for(radios.enabled(radios.WIFI), RADIO_SECS)
    except asyncio.TimeoutError:
        log.warning("wifi: radio state did not answer in %ss", RADIO_SECS)
    except OSError as e:
        log.warning("wifi: radio state unreadable: %s", e)
    return None


async def set_radio(enabled: bool) -> dict:
    """Turn the radio on or off. Returns `{"enabled": <state read back>}`."""
    word = "on" if enabled else "off"
    try:
        state = await asyncio.wait_for(radios.set_enabled(radios.WIFI, enabled), RADIO_SECS)
    except asyncio.TimeoutError:
        log.warning("wifi: radio %s timed out after %ss", word, RADIO_SECS)
        raise ServiceError(504, "Windows did not answer. Try again.") from None
    except radios.RadioBlocked:
        raise ServiceError(409, "Wi-Fi is blocked by a hardware switch or the BIOS.") from None
    except OSError as e:
        log.warning("wifi: radio %s failed: %s", word, e)
        raise ServiceError(502, str(e) or f"Could not turn Wi-Fi {word}.") from None
    if enabled and state is False:
        raise ServiceError(409, "Wi-Fi is blocked by a hardware switch or the BIOS.")
    log.info("wifi: radio turned %s", word)
    return {"enabled": state}
