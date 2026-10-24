"""Controller battery reading (SDL) + low-battery watcher.

read_batteries() is the single source of truth — sysinfo uses it for the
TopBar pills, and run() watches thresholds (25/15/10/5 %) and broadcasts
"gp:battery" over the WebSocket so the UI can pop a toast.

SDL2 reports a power LEVEL, not a percent (`platform/gamepads.py`
`POWER_PERCENT`: empty 5, low 20, medium 60, full 100), so a pad is reported
at the top of its band and an alert fires when it enters "low" (the 25 %
threshold) and "empty" (5 %). A wired pad reports no level and is not listed.
"""
import asyncio
import logging

from . import controller_registry
from .platform import gamepads as platform_gamepads

log = logging.getLogger(__name__)

# Warn when the level crosses each threshold going down, once per crossing
THRESHOLDS = (25, 15, 10, 5)
# SDL refreshes the level from the pad's own reports; what this interval
# governs is how fast a change or a pad appearing reaches the UI. The broadcast
# below only fires when something changed, so the socket stays quiet.
_POLL_SECS = 10
# Re-arm a threshold once the level climbs back above it by this margin
# (avoids toast spam when a reading oscillates around the threshold)
_REARM_MARGIN = 5


def read_batteries() -> list[dict]:
    """Pad batteries: [{name, label, player, level, charging}].

    `name` is the device, `player` its console slot (None when it has none).
    """
    result = []
    for pad in platform_gamepads.pads():
        level = platform_gamepads.POWER_PERCENT.get(pad.power)
        if level is None or not pad.is_pad:
            continue
        key = controller_registry.key_for(pad.serial, pad.device)
        result.append({
            "name": pad.device,
            "label": pad.name or pad.device,
            "player": controller_registry.player_for(key),
            "level": level,
            "charging": False,
        })
    return result


# supply name → set of thresholds already fired
_fired: dict[str, set[int]] = {}


def _check(batteries: list[dict]) -> list[dict]:
    """Return the alerts to send for this poll (pure logic — unit-testable)."""
    alerts = []
    seen = set()
    for b in batteries:
        name, level, charging = b["name"], b["level"], b["charging"]
        seen.add(name)
        fired = _fired.setdefault(name, set())
        if charging:
            # Charging resets everything — a later discharge should warn again
            fired.clear()
            continue
        # Ascending: report the tightest crossed threshold (a pad plugged in
        # at 4% is a "5%" alert, not a "15%" one)
        for t in sorted(THRESHOLDS):
            if level <= t and t not in fired:
                fired.add(t)
                # Also mark higher thresholds: connecting a pad at 4% must
                # yield ONE toast (5%), not three
                fired.update(x for x in THRESHOLDS if x >= t)
                alerts.append({"name": b["label"], "player": b.get("player"), "level": level, "threshold": t})
                break
        # Re-arm thresholds the level has climbed well above
        for t in list(fired):
            if level > t + _REARM_MARGIN:
                fired.discard(t)
    # Forget disconnected pads so a reconnect starts fresh
    for name in list(_fired):
        if name not in seen:
            del _fired[name]
    return alerts


def _signature(batteries: list[dict]) -> str:
    """What the UI actually renders — so an unchanged poll broadcasts nothing."""
    return "|".join(f"{b['name']}:{b['level']}:{b['charging']}:{b['player']}"
                    for b in batteries)


async def run() -> None:
    """Poll the pads, push controller status on change, broadcast low-battery alerts."""
    from .. import ws

    log.info("battery: watcher started (thresholds=%s, poll=%ss)", THRESHOLDS, _POLL_SECS)
    last = None
    while True:
        # Sleep FIRST: at backend startup the UI isn't connected to the
        # WebSocket yet — checking immediately would broadcast a crossed
        # threshold to zero clients and mark it fired, losing the alert
        # (seen after every OTA restart with a pad already below 25%).
        await asyncio.sleep(_POLL_SECS)
        try:
            batteries = read_batteries()

            # Push the whole picture when it moves. The TopBar used to poll
            # /api/sysinfo every 15s for this, which meant the battery pill
            # could contradict the connection toast for a quarter of a minute.
            sig = _signature(batteries)
            if sig != last:
                last = sig
                await ws.broadcast("gp:controllers", {"controllers": batteries})

            for alert in _check(batteries):
                log.info("battery: %(name)s at %(level)d%% (threshold %(threshold)d%%)", alert)
                await ws.broadcast("gp:battery", alert)
        except Exception:
            log.exception("battery: poll failed")
