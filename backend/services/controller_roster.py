"""The connected pads as the controller screen shows them.

One row per pad the scan loop can see: its slot, a name for a human, how it is
connected, its battery, whether the SDL3 emulators can name it, and which
standard controls its SDL mapping actually has. The screen draws a diagram by
position, so "which controls exist" is the part a brand picture used to fake.
"""
from __future__ import annotations

import logging

from . import battery, controller_registry, gamepad_monitor
from .configgen import mapping_db
from .configgen.controllers import SDL3_TRUSTED, display_name, resolve_name, vidpid_of
from .configgen.sdl_probe import sdl2_probe

log = logging.getLogger(__name__)

# SDL GameController field → the control the diagram draws.
_SDL_CONTROLS = {
    "a": "south", "b": "east", "x": "west", "y": "north",
    "back": "select", "start": "start", "guide": "home",
    "leftshoulder": "l1", "rightshoulder": "r1",
    "lefttrigger": "l2", "righttrigger": "r2",
    "dpup": "up", "dpdown": "down", "dpleft": "left", "dpright": "right",
    "leftx": "ls", "rightx": "rs",
}

# Bus types, as an SDL GUID encodes them (`platform/gamepads.py` `bus_of`).
_BUS = {0x03: "USB", 0x05: "Bluetooth"}


def parse_controls(bindings: str) -> dict:
    """`{"controls": [...], "analogTriggers": bool}` from SDL mapping fields.

    A trigger is analog when SDL binds it to an axis (`a2`, `+a5`); a pad whose
    triggers are buttons (`b6`) reports travel 0 or 1 and is drawn as buttons.
    """
    fields = dict(tok.partition(":")[::2] for tok in bindings.split(",") if ":" in tok)
    controls = sorted({c for k, c in _SDL_CONTROLS.items() if fields.get(k)})
    triggers = [fields.get(k, "") for k in ("lefttrigger", "righttrigger")]
    analog = any(t.lstrip("+-").startswith("a") for t in triggers)
    return {"controls": controls, "analogTriggers": analog}


def _captured_line(vendor: str, product: str) -> str:
    """The owner's wizard capture for this vendor:product, or ""."""
    for line in mapping_db.read_user():
        parsed = mapping_db.parse(line)
        if parsed and vidpid_of(parsed[0]) == (vendor, product):
            return parsed[2]
    return ""


def _layout(vendor: str, product: str) -> tuple[dict | None, bool]:
    """(controls, mapped by the owner). None when SDL gives no mapping."""
    captured = _captured_line(vendor, product)
    if captured:
        return parse_controls(captured), True
    probe = sdl2_probe(vendor, product)
    return (parse_controls(probe["map"]) if probe.get("map") else None), False


def _known(vendor: str, product: str, name: str, mapped: bool) -> str:
    if mapped:
        return "mapped"
    source = resolve_name(vendor, product, name).source
    if source not in SDL3_TRUSTED:
        return "unknown"
    return "sdl" if source == "sdl3_live" else "table"


def connected_pads() -> list[dict]:
    """Every pad the scan loop holds, ordered by player slot."""
    batteries = {b["player"]: b for b in battery.read_batteries() if b["player"]}
    rows = []
    for key, (vendor, product, name, bus) in gamepad_monitor.roster().items():
        player = controller_registry.player_for(key)
        layout, mapped = _layout(vendor, product)
        power = batteries.get(player)
        rows.append({
            "id": controller_registry.identity(key, vendor, product),
            "player": player,
            "name": display_name(vendor, product, name),
            "kernelName": name,
            "vendor": vendor,
            "product": product,
            "connection": _BUS.get(bus, "Wired"),
            "battery": power["level"] if power else None,
            "charging": bool(power and power["charging"]),
            "known": _known(vendor, product, name, mapped),
            "controls": layout["controls"] if layout else None,
            "analogTriggers": layout["analogTriggers"] if layout else True,
        })
    return sorted(rows, key=lambda r: r["player"] or 99)
