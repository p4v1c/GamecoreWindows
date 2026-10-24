"""Name what woke the box: the wake Windows logged after the sleep.

Windows writes a `Power-Troubleshooter` event to the System log at each wake
from S3 or hibernation, with the source it saw (a device such as the
Bluetooth radio, the power button, a wake timer). The record number at the
sleep, another read first thing after the wake, and a newer record is this
wake's. Readable without privilege (`platform/sleep.py`). A Modern Standby
wake logs no such event: the report then says so.
"""
import logging

from .platform import sleep

log = logging.getLogger(__name__)

_before: int | None = None
_remembered = False


def remember() -> None:
    """The sleep is announced: keep the newest record number from before it."""
    global _before, _remembered
    event = sleep.last_wake()
    _before, _remembered = (event or {}).get("record"), True


def describe(event: dict) -> str:
    """A log line's worth of what woke the box. Pure."""
    if event.get("source"):
        return event["source"]
    kind = event.get("type")
    return f"an unnamed source (type {kind})" if kind is not None else "an unnamed source"


def is_new(event: dict | None, before: int | None) -> bool:
    """True when `event` was logged after the record kept at the sleep. Pure."""
    return bool(event) and (before is None or event["record"] > before)


def report() -> str | None:
    """After the wake: log what woke the box and return it, None when unknown."""
    global _before, _remembered
    before, was_remembered = _before, _remembered
    _before, _remembered = None, False
    if not was_remembered:
        log.info("wake_source: no record from before the sleep")
        return None
    event = sleep.last_wake()
    if not is_new(event, before):
        log.info("wake_source: Windows logged no wake for this sleep (Modern Standby?)")
        return None
    source = describe(event)
    log.info("wake_source: woken by %s", source)
    return source
