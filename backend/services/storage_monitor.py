"""Reacting to a disk being plugged in, and to one being pulled out.

`storage.py` answers "what is attached and where did it land". This decides
what to DO about a change: re-point an arrival's stable link, and tell
the frontend — so a disk full of ROMs appears on the grid without anyone
restarting anything.

Nothing is invalidated on the way, and that is not an omission. A system's
games are scanned per request from its `romsPath`, and `romsPath` points at the
stable link rather than at a mount point — so the moment the link is re-pointed
the next scan already reads the new disk. There is no cached library to clear,
and adding a hook for one that does not exist would be a line nobody could ever
prove still works.

Polled, at STORAGE_POLL seconds. `WM_DEVICECHANGE` would be event-driven, but
it is delivered to a window, and the backend has none: a hidden window and a
message pump to save a few seconds on an event that happens when a human walks
across a room. Listing the drive letters costs microseconds.

**Departure is the normal case, not the error case.** A living-room box gets
its disk pulled mid-game sooner or later, and pulled without warning is the
default rather than the exception. So every branch here assumes the path may
already be gone: nothing stats a mount to decide whether it left, nothing
raises when it has, and the scanner is told to forget rather than asked to
re-read.
"""
from __future__ import annotations

import asyncio
import logging

from . import storage

log = logging.getLogger(__name__)

# A human plugging a disk in will wait a couple of seconds without noticing;
# longer reads as "it did not work" and gets the cable pulled and re-inserted.
STORAGE_POLL = 3.0


def _index(volumes: list[storage.Volume]) -> dict[str, storage.Volume]:
    """Device (`E:`) → volume."""
    return {v.path: v for v in volumes}


async def _announce(ws, event: str, payload: dict) -> None:
    """Broadcasting must never take the loop down: a websocket layer that is
    momentarily unhappy would otherwise stop the box noticing disks at all."""
    try:
        await ws.broadcast(event, payload)
    except Exception:
        log.exception("storage_monitor: could not broadcast %s", event)


async def _on_arrival(volume: storage.Volume, ws) -> storage.Volume:
    """Give an arrived disk its stable path and say so.

    A disk Windows gave no drive letter cannot be linked: it is announced as
    `storage:failed` with the reason, so the screen can say what to do.
    """
    if not volume.is_mounted:
        _ok, detail = storage.mount(volume)
        log.warning("storage_monitor: %s (%s) has no drive letter", volume.path,
                    volume.label or "no label")
        await _announce(ws, "storage:failed",
                        {"device": volume.path, "label": volume.label, "detail": detail})
        return volume
    link = await asyncio.to_thread(storage.link_volume, volume)
    log.info("storage_monitor: %s (%s) mounted at %s",
             volume.path, volume.label or "no label", volume.mountpoint)
    payload = storage.describe(volume)
    payload["stable_path"] = str(link) if link else ""
    await _announce(ws, "storage:mounted", payload)
    return volume


async def _on_departure(volume: storage.Volume, ws) -> None:
    """A disk that is no longer attached, or no longer mounted.

    The order matters: the running game is checked BEFORE the link is dropped,
    because a player whose disk has just gone mid-session needs to be told what
    happened more than the box needs its link tidy.
    """
    for running in _resident_games():
        if not storage.path_is_under(running.get("rom_path", ""), volume.mountpoint):
            continue
        # The one case that is genuinely bad and cannot be repaired from here.
        # The emulator holds an open file descriptor on a device that is gone:
        # it will fail on its next read, and — the part that costs something —
        # its next save will not land. Saying so beats a freeze with no
        # explanation, which is what this looked like before.
        log.error("storage_monitor: %s was removed while %s was running from it",
                  volume.mountpoint, running.get("game_key") or "a game")
        await _announce(ws, "storage:lost", {
            "device": volume.path,
            "label": volume.label,
            "mountpoint": volume.mountpoint,
            "game_key": running.get("game_key", ""),
            "system_id": running.get("system_id", ""),
            "detail": (f"{volume.label or volume.path} was removed while a game "
                       "was running from it. Progress since the last save is "
                       "very likely lost — plug the disk back in before saving "
                       "again."),
        })

    await asyncio.to_thread(storage.unlink_volume, volume)
    log.info("storage_monitor: %s (%s) is gone", volume.path, volume.label or "no label")
    await _announce(ws, "storage:removed",
                    {"device": volume.path, "label": volume.label,
                     "mountpoint": volume.mountpoint})


def _running_game() -> dict | None:
    """The game on the screen, or None. Never raises — this is a question asked
    while a disk is disappearing, and it must not be able to make that worse."""
    try:
        from . import process_manager
        return process_manager.process_manager.current_game
    except Exception:
        return None


def _resident_games() -> list[dict]:
    """Every session holding files on that disk — suspended ones included.

    `current_game` answers about the SCREEN, and a suspended emulator is not on
    it. But it is still a process with the disc image open: pulling the drive
    breaks its next read and loses its next save exactly as it would for a game
    being played, and the player is *more* likely to pull a disk when nothing
    seems to be running. Asking only about the foreground made this warning
    blind to the case where it matters most.

    Never raises, for the same reason `_running_game` does not.
    """
    resident = []
    front = _running_game()
    if front:
        resident.append(front)
    try:
        from . import process_manager
        resident.extend(
            {**s.describe(), "rom_path": s.rom_path}
            for s in process_manager.process_manager.background_sessions)
    except Exception:
        log.warning("storage_monitor: could not inspect suspended sessions",
                    exc_info=True)
    return resident


async def poll_once(was: dict[str, storage.Volume], ws) -> dict[str, storage.Volume]:
    """One comparison pass. Returns the new state.

    Split out of `run()` so the whole behaviour is reachable from a test
    without a sleep loop or a real disk: `platform/volumes.py` is a double,
    and what is asserted is the links and the broadcasts.
    """
    live = _index(await asyncio.to_thread(storage.list_volumes))

    # list() because _on_arrival re-reads the volume after mounting it and the
    # result is written back into `live` — rebinding a value mid-iteration is
    # legal today and one refactor away from being a RuntimeError.
    for path, volume in list(live.items()):
        previous = was.get(path)
        # An arrival is a device that is new, OR one that was here and has just
        # been mounted. Treating only the first as an arrival is why a disk
        # mounted by hand from a file manager never got its stable link.
        if previous is None or (volume.is_mounted and not previous.is_mounted):
            live[path] = await _on_arrival(volume, ws)

    for path, volume in was.items():
        gone = path not in live
        unmounted = not gone and volume.is_mounted and not live[path].is_mounted
        if (gone or unmounted) and volume.is_mounted:
            await _on_departure(volume, ws)

    return live


async def run() -> None:
    """Watch for disks arriving and leaving, for as long as the box is up."""
    from .. import ws

    log.info("storage_monitor: started (every %.0fs)", STORAGE_POLL)

    # Links left over from a session that ended with the disk attached: the box
    # was powered off and came up without it, so nothing generated a departure.
    # The link still resolves, is still a directory, and scans zero games —
    # which reads exactly like a library that has lost its contents.
    live: dict[str, storage.Volume] = {}
    try:
        attached = await asyncio.to_thread(storage.list_volumes)
        for stale in await asyncio.to_thread(storage.stale_links, attached):
            try:
                stale.unlink()
                log.info("storage_monitor: dropped stale link %s", stale)
            except OSError:
                log.warning("storage_monitor: could not drop stale link %s", stale)

        # Only the ALREADY-MOUNTED disks are adopted into the starting state,
        # and that asymmetry is the point. Adopting an unmounted one too would
        # make it identical to itself on the first poll — no arrival, so never
        # mounted — and a disk left plugged in across a reboot would come back
        # invisible until someone unplugged and replugged it.
        for volume in attached:
            if volume.is_mounted:
                # Re-linked but NOT announced: a box coming up with its disk in
                # is not news, and a toast at every boot is how a player learns
                # to ignore the one that matters.
                await asyncio.to_thread(storage.link_volume, volume)
                live[volume.path] = volume
    except Exception:
        log.exception("storage_monitor: startup sweep failed")

    while True:
        try:
            live = await poll_once(live, ws)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("storage_monitor: poll failed — retrying")
        await asyncio.sleep(STORAGE_POLL)
