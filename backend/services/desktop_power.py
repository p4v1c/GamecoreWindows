"""Who decides when the television goes dark: GameCore or Windows.

Windows' active power plan has its own "turn off the display" and "sleep
after" timers, and they cannot see a gamepad. Left alone they cap whatever the
settings page says: "Never" would mean Windows' fifteen minutes. So while
GameCore's standby is on, this module sets those timers to never and writes
down what they were, so `release()` can hand them back.

## It must be handed back

Claiming without releasing is the worse bug, not the safer one: with GameCore's
standby switched off and Windows' timers still at never, nobody turns the
screen off and the PC never sleeps. So the previous values are written down
before they are overwritten, and `release()` runs wherever GameCore stops
managing the screen (standby switched off, the backend stopping).

## The failure this deliberately accepts

If the backend dies while holding the claim, Windows' timers stay at never
until GameCore comes back. A screen that stays on is visible and annoying;
the opposite fault, a screen that goes dark under a player, is not.
"""
import asyncio
import json
import logging

from .paths import config_dir
from .platform import power as platform_power

log = logging.getLogger(__name__)

# Seconds; 0 is "never" for both timers.
_OFF = 0

# What Windows had before GameCore took over, in GameCore's config dir, which
# survives OTA updates: a claim outlives any single run of the backend.
_HANDOFF = config_dir() / "display-timeout-handoff.json"
# The key the Windows values live under; a note from the Linux box (imported
# data) carries other keys, which `release()` drops.
_KEY = "windows_timeouts"


def available() -> bool:
    """Windows always has a power plan whose timers GameCore can take over."""
    return True


async def _read() -> dict[str, int] | None:
    try:
        return await asyncio.to_thread(platform_power.idle_timeouts)
    except OSError as e:
        log.info("desktop_power: Windows' idle timers unreadable: %s", e)
        return None


async def _write(values: dict[str, int]) -> bool:
    try:
        await asyncio.to_thread(platform_power.set_idle_timeouts, values)
    except OSError as e:
        log.warning("desktop_power: could not set Windows' idle timers: %s", e)
        return False
    return True


def _note() -> dict:
    """What is outstanding. "There is a note" is what `release()` reads as "we hold it"."""
    try:
        note = json.loads(_HANDOFF.read_text(encoding="utf-8"))
        return note if isinstance(note, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save(note: dict) -> None:
    """Persist the note, or remove the file once nothing is outstanding.

    An empty note DELETES the file: a file that exists is the claim.
    """
    if not note:
        _forget()
        return
    try:
        _HANDOFF.parent.mkdir(parents=True, exist_ok=True)
        _HANDOFF.write_text(json.dumps(note, indent=2), encoding="utf-8")
    except OSError:
        log.warning("desktop_power: could not write %s: the timers taken over "
                    "here will not be restorable", _HANDOFF)


def _forget() -> None:
    try:
        _HANDOFF.unlink()
    except OSError:
        pass


async def claim() -> bool:
    """Set Windows' screen-off and sleep timers to never, noting what they were.

    Idempotent: timers already at never are left alone and the note is NOT
    rewritten, since overwriting it would erase the owner's real setting.
    False when Windows' timers cannot be read or written.
    """
    current = await _read()
    if current is None:
        return False
    wanted = {key: _OFF for key in current}
    if current == wanted:
        return True                     # already ours
    note = _note()
    # Written BEFORE the attempt: a half-done write must not lose the record.
    note.setdefault(_KEY, current)
    _save(note)
    if not await _write(wanted):
        # Withdrawn when nothing changed: the note is what says we hold it.
        if note.get(_KEY) == current:
            note.pop(_KEY)
            _save(note)
        return False
    log.info("desktop_power: Windows' screen-off and sleep timers set to never "
             "(were %s): GameCore's standby timings now decide", current)
    return True


async def release() -> bool:
    """Give Windows its timers back. True when something was restored.

    A write that fails keeps the note, so the next release still has the values.
    """
    note = _note()
    if not note:
        return False
    previous = note.get(_KEY)
    released = False
    if isinstance(previous, dict) and previous:
        if not await _write({k: int(v) for k, v in previous.items()
                             if k in platform_power.TIMERS}):
            return False
        log.info("desktop_power: Windows' idle timers restored to %s", previous)
        released = True
    _save({})
    return released
