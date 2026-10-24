"""Display mode (resolution + refresh) and UI scale, with a revert timer.

The primary output's modes are read and set through `platform/display.py`
(Win32 `ChangeDisplaySettingsEx`).

Applying a mode arms a revert timer HERE, in the backend: a mode the TV refuses
is a black screen, and the settings screen is exactly what disappears behind
it. Only an explicit player action (`confirm`) keeps the mode, and only then is
it written to the registry and to `display.json`.

Refused while a game is resident (running or suspended): changing the mode
under an emulator's swapchain kills it.
"""
import asyncio
import json
import logging

from .errors import ServiceError
from .paths import config_dir
from .platform import display as platform_display
from .process_manager import process_manager

log = logging.getLogger(__name__)


class DisplayError(ServiceError):
    """A refused display request."""

# Long enough to read a sentence and find a button on a pad, short enough that
# a black screen is an inconvenience rather than a reinstall.
REVERT_SECS = 12
BACKEND = "windows"


# What to go back to, and the task that will do it. Module state rather than a
# request-scoped value on purpose: the timer has to outlive the request that
# armed it, and a second request has to be able to find it.
_pending: dict | None = None
_revert_task: asyncio.Task | None = None
# One transaction at a time. Two clients — or one client and the revert timer —
# could otherwise be inside `_apply` at the same moment, and which mode the
# screen ends up in would be decided by whichever tool returned last.
_mode_lock = asyncio.Lock()
# Which transaction a timer belongs to. A task cancelled while it waits for the
# lock never reaches its body, but the check costs nothing and says out loud
# that a timer may not act for a transaction that is over.
_pending_seq = 0


def _cancel_pending() -> None:
    global _pending, _revert_task
    if _revert_task and not _revert_task.done():
        _revert_task.cancel()
    _revert_task = None
    _pending = None


def _read_state_sync() -> dict:
    output = platform_display.primary_output()
    if not output:
        return {"output": "", "modes": [], "current": None, "backend": ""}
    return {"output": output, "modes": platform_display.modes(output),
            "current": platform_display.current_mode(output), "backend": BACKEND}


async def read_state() -> dict:
    """The primary output, its modes and the one on screen."""
    try:
        return await asyncio.to_thread(_read_state_sync)
    except OSError as e:
        log.warning("display: modes unreadable: %s", e)
        return {"output": "", "modes": [], "current": None, "backend": ""}


async def _apply(state_backend: str, output: str, mode: dict,
                 persist: bool = False) -> tuple[bool, str]:
    """Put one mode on screen; `persist` writes the one already there to the registry."""
    return await asyncio.to_thread(platform_display.apply_mode, output, mode["width"],
                                   mode["height"], mode["rate"], persist)


async def _revert_after(delay: float, previous: dict, seq: int = 0) -> None:
    """Put the old mode back unless someone confirms first.

    Cancellation is the success path: `/confirm` cancels this task, so arriving
    at the `_apply` below means nobody was able to answer — which is what a
    screen showing nothing looks like from here.
    """
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    async with _mode_lock:
        if _pending is not None and _pending.get("seq") != seq:
            # A transaction of its own now owns the screen. Cancellation
            # normally gets here first — a task waiting for the lock is
            # cancelled where it waits — and this is what says so out loud.
            return
        ok, detail = await _apply(previous["backend"], previous["output"], previous)
        log.warning("display: no confirmation in %ss — reverted to %sx%s@%s (%s)",
                    delay, previous["width"], previous["height"], previous["rate"],
                    "ok" if ok else detail)
        _cancel_pending()


async def state() -> dict:
    """Modes, current mode, backend, and whether a change awaits confirmation."""
    # No display at all → empty mode list, which the screen shows as such.
    data = await read_state()
    data["pending"] = _pending is not None
    data["revert_secs"] = REVERT_SECS
    return data


async def set_mode(width: int, height: int, rate: float) -> dict:
    """Put a mode on screen, keeping a way back to the last CONFIRMED mode.

    Nothing is given up until the new mode is actually on: a refused second
    change keeps the first one's timer.
    """
    # is_running, not is_foreground: a suspended emulator still holds its GPU
    # context and dies on a mode change.
    if process_manager.is_running:
        raise DisplayError(409, "A game is running — close it before changing the display mode.")

    async with _mode_lock:
        return await _set_mode_locked(width, height, rate)


async def _set_mode_locked(width: int, height: int, rate: float) -> dict:
    global _pending, _revert_task, _pending_seq
    data = await read_state()
    if not data["output"] or not data["current"]:
        raise DisplayError(503, "No connected output reports a mode.")

    # Refused rather than passed through: these tools take any geometry, and a
    # mode the monitor never advertised is the black screen this endpoint
    # exists to make survivable — no reason to walk into it deliberately.
    wanted = next((m for m in data["modes"]
                   if m["width"] == width and m["height"] == height
                   and abs(m["rate"] - rate) < 0.01), None)
    if wanted is None:
        raise DisplayError(400, "That mode is not one this output advertises.")

    previous = dict(data["current"], output=data["output"], backend=data["backend"])
    if (previous["width"], previous["height"]) == (width, height) \
            and abs(previous["rate"] - rate) < 0.01:
        return {"ok": True, "changed": False, "revert_secs": REVERT_SECS}

    # The mode to come back to, which is not necessarily the one on screen.
    fallback = _pending["previous"] if _pending else previous

    ok, detail = await _apply(data["backend"], data["output"], wanted)
    if not ok:
        # The transaction already waiting keeps its timer: nothing about this
        # refusal makes the screen it left any safer to be stuck on.
        raise DisplayError(500, detail or "The compositor refused that mode.")

    _cancel_pending()
    _pending_seq += 1
    _pending = {"previous": fallback, "seq": _pending_seq,
                "wanted": dict(wanted, output=data["output"], backend=data["backend"])}
    _revert_task = asyncio.create_task(_revert_after(REVERT_SECS, fallback, _pending_seq))
    return {"ok": True, "changed": True, "revert_secs": REVERT_SECS}


#: Where a confirmed mode is written down, on the data side with the rest of
#: the player's choices.
PREFERENCE_FILE = "display.json"


def _preference_path():
    return config_dir() / PREFERENCE_FILE


def preferred_mode() -> dict | None:
    """The mode the player last confirmed, or None.

    Windows keeps the confirmed mode in its own registry too (`confirm`); this
    copy is what a wake checks the output against (`restore_preferred`).
    """
    try:
        data = json.loads(_preference_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return {"width": int(data["width"]), "height": int(data["height"]),
                "rate": float(data["rate"]), "output": str(data.get("output", ""))}
    except (KeyError, TypeError, ValueError):
        log.warning("display: %s is not a mode I can read — ignoring it",
                    _preference_path())
        return None


def _remember(mode: dict) -> None:
    """Only ever called from /confirm.

    Confirmation is the whole difference between a mode that works and a mode
    that was merely accepted by the compositor: somebody read the screen and
    pressed a button on it. Writing an UNconfirmed mode down would persist
    exactly the black screens the revert exists to undo.
    """
    try:
        path = _preference_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "width": mode["width"], "height": mode["height"],
            "rate": mode["rate"], "output": mode.get("output", ""),
        }, indent=2) + "\n", encoding="utf-8")
    except (OSError, KeyError, TypeError) as e:
        log.warning("display: could not remember the confirmed mode — %s", e)


#: Interface size on this screen, as a zoom factor of the whole front end.
#: A fixed list rather than a free value: each one is a size someone checked
#: the themes at, and a pad cycles through four choices, not a slider.
SCALES = (0.9, 1.0, 1.25, 1.5)
SCALE_FILE = "ui-scale.json"


def ui_scale() -> float:
    """The scale the player chose, 1.0 when nothing (valid) was written."""
    try:
        value = float(json.loads((config_dir() / SCALE_FILE).read_text(encoding="utf-8"))["scale"])
    except (OSError, ValueError, KeyError, TypeError):
        return 1.0
    return value if value in SCALES else 1.0


def set_scale(scale: float) -> dict:
    """Remember the interface size. The front end applies it itself: it is a
    zoom of the page, nothing on the output changes, so there is no revert."""
    if scale not in SCALES:
        raise DisplayError(400, f"Scale must be one of {', '.join(f'{int(s * 100)} %' for s in SCALES)}.")
    path = config_dir() / SCALE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"scale": scale}) + "\n", encoding="utf-8")
    except OSError as e:
        raise DisplayError(500, f"Could not save the scale — {e}")
    return {"ok": True, "scale": scale}


async def confirm() -> dict:
    """Keep the mode that is on screen.

    Only reachable by someone who can read the screen — which is the entire
    point. Calling it with nothing pending is not an error: a second press, or
    a reload after the timer already fired, means the same thing.
    """
    async with _mode_lock:
        pending = _pending is not None
        wanted = _pending["wanted"] if pending else None
        _cancel_pending()
    if wanted:
        _remember(wanted)
        ok, detail = await _apply(wanted["backend"], wanted["output"], wanted, persist=True)
        if not ok:
            log.warning("display: confirmed mode kept for this session only: %s", detail)
    return {"ok": True, "confirmed": pending}


async def revert_now() -> dict:
    """Go back immediately, without waiting out the timer.

    For the player who can see the screen and simply does not want the mode.
    """
    async with _mode_lock:
        if _pending is None:
            return {"ok": True, "reverted": False}
        previous = _pending["previous"]
        _cancel_pending()
        ok, detail = await _apply(previous["backend"], previous["output"], previous)
        if not ok:
            raise DisplayError(500, detail or "Could not restore the previous mode.")
    return {"ok": True, "reverted": True}


def _same_mode(a: dict, b: dict) -> bool:
    return (a["width"], a["height"]) == (b["width"], b["height"]) \
        and abs(a["rate"] - b["rate"]) < 0.01


async def restore_preferred() -> bool:
    """Put the confirmed mode back if a wake left the output on another one.

    A TV that was off while the box slept can come back on another mode. No revert timer: the player confirmed this mode already. Returns
    True when a mode was applied.
    """
    wanted = preferred_mode()
    if wanted is None or _pending is not None or process_manager.is_running:
        return False
    async with _mode_lock:
        data = await read_state()
        current = data.get("current")
        if not data["output"] or not current or _same_mode(current, wanted):
            return False
        if wanted["output"] and wanted["output"] != data["output"]:
            return False
        mode = next((m for m in data["modes"] if _same_mode(m, wanted)), None)
        if mode is None:
            return False
        ok, detail = await _apply(data["backend"], data["output"], mode)
    log.info("display: restored %sx%s@%s after a wake (%s)", wanted["width"],
             wanted["height"], wanted["rate"], "ok" if ok else detail)
    return ok
