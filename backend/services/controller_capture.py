"""Live pad capture — turning "the owner pressed this" into an SDL mapping line.

The wizard shows one button at a time and waits. What has to be WRITTEN is
SDL's own vocabulary — `b0`, `a3`, `h0.1` — where the numbers are SDL's
joystick indices. The backend reads the pad through SDL itself
(`platform/gamepads.py`, raw layer), so the indices arrive as SDL numbers
them: button n, axis n, and each hat as two signed pseudo axes that
`binding_for` folds back into SDL's hat bitmask.

## Why a session

One pad at a time, keyed by controller (`controller_registry.key_for`): with
two pads connected there is no way to know which one the owner is holding.

## Rest, not zero

An axis is pressed when it is far from where it RESTS, measured per direction
(`Axis`): a stick rests at its centre, a trigger at its minimum, and SDL
reports both on the same -32768..32767 scale. The rest comes from SDL's
initial axis state, read when the pad was opened.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from . import controller_registry
from .configgen import mapping_db, sdl_probe
from .configgen.controllers import (
    bundled_sdl2,
    detect_pads,
    display_name,
    sdl2_probe,
)
from .platform import gamepads as platform_gamepads

log = logging.getLogger(__name__)

# The raw layer's vocabulary (`platform/gamepads.py`): a button is EV_KEY n,
# an axis EV_ABS n, hat h the pseudo axes ABS_HAT0X + 2h and ABS_HAT0Y + 2h.
EV_KEY, EV_ABS = 0x01, 0x03
ABS_HAT0X = 0x10
# Above this the pseudo hat axes start: SDL axis indices stay below it.
MAX_AXES = ABS_HAT0X
SDL_AXIS_RANGE = (-32768, 32767)

# The SDL hat bitmask: the pseudo axes are folded back into it.
HAT_UP, HAT_RIGHT, HAT_DOWN, HAT_LEFT = 1, 2, 4, 8

# How far an axis must be from where it rests to count as pressed, and how
# close it must come back to count as released, both as a fraction of the
# travel available in that direction.
#
# Two thresholds rather than one, because a single one chatters: an axis
# hovering at the boundary would alternate press and release, and the wizard
# reads those edges as a gesture. The gap is wide because nothing here needs
# precision — the question is "has the player pushed this", and half a stick is
# not an accident.
AXIS_PRESSED, AXIS_AT_REST = 0.6, 0.3


@dataclass(frozen=True)
class Layout:
    """SDL's view of one device's inputs: `raw-layer code → SDL index`.

    A code the device does not have is simply absent, which is what makes a
    press on it unmappable rather than mapped to something plausible and wrong.
    """
    buttons: dict[int, int] = field(default_factory=dict)
    axes: dict[int, int] = field(default_factory=dict)
    hats: dict[int, int] = field(default_factory=dict)

    @property
    def counts(self) -> tuple[int, int, int]:
        """(buttons, axes, hats) — what SDL reports through its Num* calls, and
        the cheapest thing to compare a derivation against."""
        return len(self.buttons), len(self.axes), len(set(self.hats.values()))


@dataclass(frozen=True)
class Axis:
    """One analogue axis: where it rests, and how far it can go from there.

    The rest is what tells a stick from a trigger: SDL reports both on the
    same scale, and only where they sit at rest separates them.

    Travel is measured PER DIRECTION. A stick at its midpoint can move half
    the scale either way; a trigger at its minimum can move all of it.
    Dividing by the full span instead would make a pushed stick read as half
    pressed.
    """
    minimum: int
    maximum: int
    rest: int

    @property
    def travel(self) -> int:
        return max(self.maximum - self.rest, self.rest - self.minimum)

    def deflection(self, value: int) -> float:
        """How far from rest, as a fraction of how far it could go. 0 at rest."""
        return abs(value - self.rest) / self.travel if self.travel else 0.0


def sdl_layout(buttons: int, axes: int, hats: int) -> Layout:
    """The raw layer's codes for a joystick SDL counts this way: identity maps."""
    return Layout(buttons={i: i for i in range(buttons)},
                  axes={i: i for i in range(min(axes, MAX_AXES))},
                  hats={ABS_HAT0X + 2 * h + d: h for h in range(hats) for d in (0, 1)})


def binding_for(layout: Layout, ev_type: int, code: int, value: int,
                axis: Axis | None = None) -> str | None:
    """The SDL token for one event, or None when it says nothing.

    `axis` describes the analogue axis this event belongs to, and an axis event
    without one is unmappable rather than mapped: deciding what a reading means
    needs to know where the thing rests.
    """
    if ev_type == EV_KEY:
        if value != 1:                       # presses only, never the release
            return None
        index = layout.buttons.get(code)
        return f"b{index}" if index is not None else None

    if ev_type != EV_ABS:
        return None

    if code in layout.hats:
        hat = layout.hats[code]
        if value == 0:
            return None
        horizontal = (code - ABS_HAT0X) % 2 == 0
        if horizontal:
            mask = HAT_RIGHT if value > 0 else HAT_LEFT
        else:
            mask = HAT_DOWN if value > 0 else HAT_UP
        return f"h{hat}.{mask}"

    index = layout.axes.get(code)
    if index is None or axis is None:
        return None
    if axis.deflection(value) < AXIS_PRESSED:
        return None
    # A full axis, signed. The wizard tells the two halves apart itself: a
    # stick step keeps `a3`, a trigger step narrows it to `+a3`, because a
    # trigger that rests at its minimum and a stick that rests at centre are
    # the same event here and only the caller knows which was asked for.
    return f"a{index}"


def half_axis(binding: str, value: int, axis: Axis | None = None) -> str:
    """`a3` → `+a3` or `-a3`. For the steps where the direction is the point —
    a trigger, or a stick direction bound to a d-pad.

    Which half is decided against where the axis RESTS, not against zero: a
    trigger resting at -32768 reads negative when half pulled.
    """
    if not binding.startswith("a"):
        return binding
    return ("+" if value > (axis.rest if axis else 0) else "-") + binding


# ── what the wizard asks for, in order ───────────────────────────────────────
# SDL's own output names. The order is the order the buttons are asked for on
# screen, chosen so the face buttons — the ones every pad has — come first and a
# session abandoned halfway still produced something usable.
STEPS: tuple[tuple[str, str, str], ...] = (
    ("a",             "button", "A / Cross — the confirm button"),
    ("b",             "button", "B / Circle — the back button"),
    ("x",             "button", "X / Square"),
    ("y",             "button", "Y / Triangle"),
    ("dpup",          "button", "D-pad up"),
    ("dpdown",        "button", "D-pad down"),
    ("dpleft",        "button", "D-pad left"),
    ("dpright",       "button", "D-pad right"),
    ("leftshoulder",  "button", "Left shoulder (L1 / LB)"),
    ("rightshoulder", "button", "Right shoulder (R1 / RB)"),
    ("lefttrigger",   "axis",   "Left trigger (L2 / LT)"),
    ("righttrigger",  "axis",   "Right trigger (R2 / RT)"),
    ("back",          "button", "Select / Share / View"),
    ("start",         "button", "Start / Options / Menu"),
    ("guide",         "button", "The big centre button (PS / Xbox / Home)"),
    ("leftstick",     "button", "Press the LEFT stick down (L3)"),
    ("rightstick",    "button", "Press the RIGHT stick down (R3)"),
    ("leftx",         "axis",   "Left stick — push RIGHT"),
    ("lefty",         "axis",   "Left stick — push DOWN"),
    ("rightx",        "axis",   "Right stick — push RIGHT"),
    ("righty",        "axis",   "Right stick — push DOWN"),
)

# Steps a pad may legitimately not have. The wizard lets any step be skipped,
# but only these produce a mapping that is complete rather than crippled — the
# distinction the UI needs to decide whether to warn.
OPTIONAL = frozenset({"guide", "leftstick", "rightstick",
                      "rightx", "righty", "lefttrigger", "righttrigger"})


def mapping_line(guid: str, name: str, bindings: dict[str, str],
                 platform: str = "Windows") -> str:
    """One SDL_GameControllerDB line, for SDL on Windows.

    SDL ignores a line whose `platform:` is another OS, so a capture filed as
    `Linux` here would be stored and never used. Fields are emitted in STEPS
    order rather than in whatever order the wizard filled them, so the same pad
    mapped twice produces the same line and a diff between two captures is
    readable.
    """
    if not name.strip():
        raise ValueError("a mapping line needs a device name")
    parts = [guid.lower(), name.strip().replace(",", " ")]
    for field_name, _kind, _label in STEPS:
        token = bindings.get(field_name)
        if token:
            parts.append(f"{field_name}:{token}")
    if len(parts) <= 2:
        raise ValueError("a mapping line needs at least one binding")
    parts.append(f"platform:{platform}")
    return ",".join(parts) + ","


# ── which GUIDs to write the line for ────────────────────────────────────────

def sdl_guids(vendor: str, product: str, app_ids=()) -> list[str]:
    """Every distinct GUID an SDL on this box computes for this pad.

    Not one GUID, several, and that is the same fact `Pad.guid_for()` is built
    around: each Flatpak brings its own SDL and the same pad therefore has
    several simultaneous identities — a DualShock 4 is `0500…` to the host's
    SDL3 and `0300…` to Ryujinx's bundled SDL2, one byte of bus type apart.

    A mapping filed under one of them is invisible to the emulators that
    compute another. Since a `gamecontrollerdb` file may carry as many lines as
    it likes and SDL only ever looks up the GUID it computed itself, writing one
    line per identity costs a few hundred bytes and removes the entire class of
    "it works in RPCS3 and not in Ryujinx".

    Ordered, deduplicated, host first.
    """
    found: list[str] = []

    def add(guid: str | None) -> None:
        if guid and len(guid) == 32 and guid.lower() not in found:
            found.append(guid.lower())

    add(sdl2_probe(vendor, product).get("guid"))
    for app_id in app_ids:
        if not app_id or not sdl_probe.flatpak_location(app_id):
            continue
        lib = bundled_sdl2(app_id)
        if lib:
            add(sdl2_probe(vendor, product, lib).get("guid"))
    return found


def _profilable_app_ids() -> list[str]:
    """The app ids of the packs that profile controllers.

    Read from the catalogue rather than listed here: a hardcoded tuple in this
    file is exactly the single-source-of-truth break that made
    `profilable_packs()` miss a new emulator entirely.
    """
    try:
        from .catalog import load_catalog
        from .configgen import profilable_packs
        return [p.app_id for p in profilable_packs(load_catalog()) if p.app_id]
    except Exception:
        log.warning("controller_capture: could not read the catalogue for "
                    "bundled-SDL ids", exc_info=True)
        return []


# ── the session ──────────────────────────────────────────────────────────────

# A session left open subscribes to the pad's events (reading is shared, the
# interface still gets them) but it is still state, and the UI it belongs to is
# one a player can walk away from. Ten minutes is long enough for the slowest
# pass through 21 buttons.
SESSION_TTL = 600.0


@dataclass
class Session:
    """One pad being mapped. Never more than one at a time.

    A session is keyed by CONTROLLER (`controller_registry.key_for`), never by
    devnode — see the module docstring.
    """
    id: str
    key: str
    vendor: str
    product: str
    name: str
    nodes: list[str]
    layouts: dict[str, Layout]
    guids: list[str]
    started: float
    rests: dict[str, tuple[int, ...]] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started > SESSION_TTL


_session: Session | None = None


def current() -> Session | None:
    global _session
    if _session is not None and _session.expired:
        log.info("controller_capture: session %s expired", _session.id)
        _session = None
    return _session


def cancel() -> bool:
    """Drop the open session. True when there was one."""
    global _session
    had = _session is not None
    _session = None
    return had


def _pad_device(vendor: str, product: str) -> tuple[str, dict[str, Layout],
                                                 dict[str, tuple[int, ...]]]:
    """(registry key, {device: layout}, {device: axis rests}) for that pad.

    One device: SDL opens a pad once, whatever nodes a driver splits it into.
    """
    for pad in platform_gamepads.pads():
        if (pad.vendor, pad.product) == (vendor, product) and pad.is_pad:
            key = controller_registry.key_for(pad.serial, pad.device)
            return key, {pad.device: sdl_layout(*pad.counts)}, {pad.device: pad.rest}
    return "", {}, {}


def start() -> dict:
    """Open a capture session on the one connected pad.

    Exactly one, deliberately: with two pads connected there is no way to know
    which one the owner is holding, and a mapping filed under the wrong GUID is
    worse than no mapping — it is a wrong answer that survives reboots.
    """
    global _session
    pads = detect_pads()
    if len(pads) != 1:
        return {"ok": False,
                "error": ("connect exactly one controller — the one you want "
                          f"to map — and disconnect the others (found {len(pads)})")}
    vendor, product, sdl_name = pads[0]

    key, layouts, rests = _pad_device(vendor, product)
    if not layouts:
        return {"ok": False,
                "error": f"{vendor}:{product} was detected but has gone again. Reconnect it."}

    guids = sdl_guids(vendor, product, _profilable_app_ids())
    if not guids:
        return {"ok": False,
                "error": ("no SDL on this box could name a GUID for this pad, "
                          "so there is nothing to file a mapping under")}

    _session = Session(
        id=f"{key}-{int(time.time())}",
        key=key, vendor=vendor, product=product,
        name=display_name(vendor, product, sdl_name),
        nodes=sorted(layouts), layouts=layouts, guids=guids,
        started=time.monotonic(), rests=rests)
    log.info("controller_capture: mapping %s (%s:%s), %d GUID(s)",
             _session.name, vendor, product, len(guids))
    return {"ok": True, "session": _session.id, "controller": _session.name,
            "vendor": vendor, "product": product, "guids": guids,
            "nodes": _session.nodes,
            "steps": [{"field": f, "kind": k, "label": lbl}
                      for f, k, lbl in STEPS],
            "optional": sorted(OPTIONAL)}


def _axes(session: Session) -> dict[tuple[str, int], Axis]:
    """Every axis of the session's pad, with where it rests."""
    low, high = SDL_AXIS_RANGE
    return {(device, index): Axis(minimum=low, maximum=high, rest=rest)
            for device, rests in session.rests.items()
            for index, rest in enumerate(rests)}


async def events(session: Session):
    """Yield `{binding, pressed, kind, signed, code, value, node}` for every input.

    Reads the pad's raw layer; nothing is taken from the interface, which
    still sees every press (the owner must be able to cancel the wizard).
    """
    queues = {device: platform_gamepads.subscribe(device) for device in session.nodes}
    merged: asyncio.Queue = asyncio.Queue()

    async def pump(device: str, queue: asyncio.Queue) -> None:
        while True:
            event = await queue.get()
            await merged.put((device, event))
            if event.layer == platform_gamepads.GONE.layer:
                return

    tasks = [asyncio.create_task(pump(d, q), name=f"capture:{d}") for d, q in queues.items()]
    live = len(tasks)
    axes = _axes(session)

    # What each input is currently reporting, so a return to rest can be sent
    # as the RELEASE of the thing that was held.
    #
    # The wizard's "hold this button, the pad does not have it" gesture needs
    # both edges: a press alone cannot be told from a hold until it ends.
    active: dict[tuple[str, int], str] = {}
    try:
        while True:
            # The session's own deadline, as one wakeup rather than a poll. A
            # capture nobody finishes has to end on its own too: `current()`
            # forgets it after SESSION_TTL, and a stream still reading a pad
            # for a session that no longer exists answers to nobody.
            remaining = SESSION_TTL - (time.monotonic() - session.started)
            if remaining <= 0:
                log.info("controller_capture: session %s expired — ending the stream",
                         session.id)
                return
            try:
                path, event = await asyncio.wait_for(merged.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue                     # the check above ends it
            if event.layer == platform_gamepads.GONE.layer:
                live -= 1
                if live <= 0:
                    log.info("controller_capture: %s is gone — ending the stream",
                             session.name)
                    return
                continue
            if event.layer != "raw":
                continue
            layout = session.layouts.get(path)
            if layout is None:
                continue
            axis = axes.get((path, event.code))
            token = binding_for(layout, event.type, event.code, event.value, axis)
            if token is not None:
                active[(path, event.code)] = token
                yield {"binding": token, "pressed": True,
                       "kind": "button" if event.type == EV_KEY else "axis",
                       "signed": half_axis(token, event.value, axis),
                       "code": event.code, "value": event.value, "node": path}
                continue
            # Only a genuine return to rest is a release. An unmappable code or
            # a key autorepeat must not be reported as one, or the UI would see
            # a button let go that was never pressed.
            #
            # The release threshold is LOWER than the press one on purpose: an
            # axis loitering on a single boundary would otherwise alternate
            # press and release, and the wizard reads those edges as gestures.
            at_rest = (event.value == 0 if event.type == EV_KEY
                       else axis is None
                       or axis.deflection(event.value) <= AXIS_AT_REST)
            if not at_rest:
                continue
            previous = active.pop((path, event.code), None)
            if previous is None:
                continue
            yield {"binding": previous, "pressed": False,
                   "kind": "button" if event.type == EV_KEY else "axis",
                   "signed": previous, "code": event.code,
                   "value": event.value, "node": path}
    finally:
        for task in tasks:
            task.cancel()
        for device, queue in queues.items():
            platform_gamepads.unsubscribe(device, queue)


def commit(bindings: dict[str, str], name: str = "") -> dict:
    """Write the captured mapping — one line per GUID — and rebuild the DB.

    Returns the lines written, so the UI can put one on the clipboard for
    SDL_GameControllerDB. A mapping that only ever lives on this box helps this
    box; contributed upstream it reaches everyone with the same pad, which is
    how the community file the box already ships got written.
    """
    session = current()
    if session is None:
        return {"ok": False, "error": "no capture session is open — start one first"}
    usable = {f: t for f, t in bindings.items()
              if t and f in {step[0] for step in STEPS}}
    if not usable:
        return {"ok": False, "error": "nothing was captured"}

    label = (name or session.name).strip()
    lines = []
    for guid in session.guids:
        try:
            line = mapping_line(guid, label, usable)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        mapping_db.upsert(line)
        lines.append(line)

    missing = sorted({step[0] for step in STEPS} - set(usable) - OPTIONAL)
    # Saved is not the same as in force: `mapping_db.servable` withholds a
    # capture filed under a HIDAPI GUID, where it would REPLACE SDL's own,
    # correct mapping rather than add to it. Reporting the count rather than
    # only "ok" is what lets the caller, and the log, tell a capture that took
    # effect from one that was stored and set aside.
    served = len(mapping_db.servable(lines))
    cancel()
    log.info("controller_capture: saved %d line(s) for %s (%d bindings), "
             "%d handed to SDL", len(lines), label, len(usable), served)
    return {"ok": True, "controller": label, "lines": lines, "served": served,
            "bindings": len(usable), "missing": missing,
            "database": str(mapping_db.USER_DB)}
