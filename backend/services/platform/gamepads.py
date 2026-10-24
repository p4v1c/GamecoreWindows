"""Pads through SDL2, read by the backend while a game has the focus.

Replaces evdev and `/dev/input`. One thread owns SDL (every SDL call is made
from it): it opens each joystick as it arrives, keeps a snapshot of what is
connected, and hands every input to the subscribers of that device on their
event loop.

Events keep the evdev vocabulary the services were written against, so the
guide double-press, the Start+Select chord and standby's activity filter did
not change: `PadEvent(type, code, value, layer)`.

  layer "pad"  the game-controller view (SDL's mapping): guide is `BTN_MODE`,
               Start `BTN_START`, Select `BTN_SELECT`, sticks and triggers
               `ABS_*` in SDL's ranges; a joystick SDL cannot map reports its
               raw buttons as `BTN_JOYSTICK + n`, for activity only;
  layer "raw"  SDL's joystick indices for the mapping wizard: button n is
               `EV_KEY n`, axis n is `EV_ABS n`, hat h is the two pseudo axes
               `ABS_HAT0X + 2h` / `ABS_HAT0Y + 2h` at -1, 0 or 1.

`SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS` is the point of all this: Chromium sees
no guide button and nothing at all while a game has the focus. Rumble stays
off on PS4 and PS5 pads: enabling it switches a Bluetooth DualShock 4 to its
extended reports, which the interface's own Gamepad API then misreads.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import namedtuple
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

PadEvent = namedtuple("PadEvent", "type code value layer")
#: Put on a subscriber's queue when its device is gone.
GONE = PadEvent(0, 0, 0, "gone")

EV_KEY, EV_ABS = 0x01, 0x03
BTN_JOYSTICK = 0x120
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11

# SDL_GameControllerButton → the evdev key the services listen for.
CONTROLLER_BUTTONS = {0: 0x130, 1: 0x131, 2: 0x134, 3: 0x133,   # A B X Y
                      4: 0x13A, 5: 0x13C, 6: 0x13B,             # back guide start
                      7: 0x13D, 8: 0x13E, 9: 0x136, 10: 0x137}  # sticks, shoulders
# The d-pad buttons, as the hat axis and direction evdev pads report.
DPAD = {11: (ABS_HAT0Y, -1), 12: (ABS_HAT0Y, 1), 13: (ABS_HAT0X, -1), 14: (ABS_HAT0X, 1)}
# SDL_GameControllerAxis → evdev axis: LX LY RX RY LT RT.
CONTROLLER_AXES = {0: 0x00, 1: 0x01, 2: 0x03, 3: 0x04, 4: 0x02, 5: 0x05}
TRIGGER_AXES = frozenset({0x02, 0x05})
STICK_RANGE, TRIGGER_RANGE = (-32768, 32767), (0, 32767)
# SDL hat bitmask → (x, y) in evdev's signed hat axes.
_HAT_X = {2: 1, 8: -1, 3: 1, 6: 1, 9: -1, 12: -1}
_HAT_Y = {1: -1, 4: 1, 3: -1, 9: -1, 6: 1, 12: 1}
# SDL_JoystickPowerLevel → a percent the battery watcher can threshold.
POWER_PERCENT = {0: 5, 1: 20, 2: 60, 3: 100, 5: 100}       # EMPTY LOW MEDIUM FULL MAX
POWER_WIRED = 4
_JOYSTICK_TYPES_PADLIKE = frozenset({0, 1})                # unknown, game controller
_MIN_PAD_BUTTONS = 4
_POWER_POLL_SECS = 5.0

HINTS = {b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": b"1",
         b"SDL_JOYSTICK_HIDAPI_PS4_RUMBLE": b"0",
         b"SDL_JOYSTICK_HIDAPI_PS5_RUMBLE": b"0"}


@dataclass
class PadInfo:
    """One connected joystick, as the snapshot holds it."""
    device: str                 # "sdl<instance id>": sorts in connection order
    name: str
    vendor: str                 # 4 hex digits
    product: str
    guid: str                   # 32 hex digits, SDL's own
    serial: str                 # the Bluetooth address when SDL knows it
    bus: int                    # 0x03 USB, 0x05 Bluetooth (from the GUID)
    is_pad: bool                # takes a player slot
    has_guide: bool
    counts: tuple[int, int, int] = (0, 0, 0)            # buttons, axes, hats
    rest: tuple[int, ...] = ()                          # each axis at rest
    power: int | None = None                            # SDL power level
    controller: bool = field(default=False, repr=False)


def bus_of(guid: str) -> int:
    """The transport an SDL GUID encodes in its first two bytes (little endian)."""
    try:
        return int(guid[2:4] + guid[0:2], 16)
    except ValueError:
        return 0


def normalize_serial(raw: str) -> str:
    """`40-1b-5f-b9-ea-8d` or `401b5fb9ea8d` → `40:1b:5f:b9:ea:8d`; anything else as is."""
    digits = "".join(c for c in raw if c.isalnum()).lower()
    if len(digits) == 12 and all(c in "0123456789abcdef" for c in digits):
        return ":".join(digits[i:i + 2] for i in range(0, 12, 2))
    return raw.strip()


def is_padlike(joystick_type: int, buttons: int, controller: bool) -> bool:
    """A player: SDL maps it as a game controller, or an unknown joystick with face buttons.

    Wheels, flight sticks and throttles never take a slot.
    """
    return controller or (joystick_type in _JOYSTICK_TYPES_PADLIKE
                          and buttons >= _MIN_PAD_BUTTONS)


def hat_events(hat: int, mask: int, layer: str) -> list[PadEvent]:
    """An SDL hat position as the two signed axes evdev reports."""
    return [PadEvent(EV_ABS, ABS_HAT0X + 2 * hat, _HAT_X.get(mask, 0), layer),
            PadEvent(EV_ABS, ABS_HAT0Y + 2 * hat, _HAT_Y.get(mask, 0), layer)]


def controller_button(button: int, pressed: bool) -> PadEvent | None:
    """A game-controller button in evdev words; None for one the services ignore."""
    if button in DPAD:
        code, direction = DPAD[button]
        return PadEvent(EV_ABS, code, direction if pressed else 0, "pad")
    code = CONTROLLER_BUTTONS.get(button)
    return PadEvent(EV_KEY, code, int(pressed), "pad") if code is not None else None


def axis_range(code: int) -> tuple[int, int]:
    """(min, max) SDL reports on a pad-layer axis: triggers rest at their minimum."""
    return TRIGGER_RANGE if code in TRIGGER_AXES else STICK_RANGE


class _Reader:
    """The SDL thread, its snapshot and its subscribers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pads: dict[int, PadInfo] = {}
        self._handles: dict[int, tuple] = {}
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._thread: threading.Thread | None = None
        self.error = ""

    # ── called from any thread ──
    def start(self) -> bool:
        with self._lock:
            if self._thread is not None:
                return not self.error
            ready = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(ready,),
                                            name="sdl-pads", daemon=True)
            self._thread.start()
        ready.wait(10)
        return not self.error

    def snapshot(self) -> list[PadInfo]:
        with self._lock:
            return sorted(self._pads.values(), key=lambda p: int(p.device[3:]))

    def subscribe(self, device: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(device, []).append(
                (asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, device: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(device, [])
            subs[:] = [(loop, q) for loop, q in subs if q is not queue]

    # ── the SDL thread ──
    def _post(self, device: str, events) -> None:
        with self._lock:
            subs = list(self._subscribers.get(device, ()))
        for loop, queue in subs:
            for event in events:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                except RuntimeError:          # that loop is closed
                    self.unsubscribe(device, queue)

    def _run(self, ready: threading.Event) -> None:
        try:
            import sdl2
            for name, value in HINTS.items():
                sdl2.SDL_SetHint(name, value)
            if sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK | sdl2.SDL_INIT_GAMECONTROLLER) != 0:
                self.error = (sdl2.SDL_GetError() or b"SDL_Init failed").decode(errors="replace")
        except Exception as e:                # no SDL2.dll: the box works without pads
            self.error = str(e) or type(e).__name__
        ready.set()
        if self.error:
            log.error("gamepads: SDL unavailable (%s): no pad can be read", self.error)
            return
        self._loop(sdl2)

    def _loop(self, sdl2) -> None:
        import ctypes

        event = sdl2.SDL_Event()
        next_power = 0.0
        while True:
            while sdl2.SDL_WaitEventTimeout(ctypes.byref(event), 200):
                try:
                    self._handle(sdl2, event)
                except Exception:
                    log.exception("gamepads: event %d failed", event.type)
            if time.monotonic() >= next_power:
                next_power = time.monotonic() + _POWER_POLL_SECS
                self._poll_power(sdl2)

    def _handle(self, sdl2, event) -> None:
        kind = event.type
        if kind == sdl2.SDL_JOYDEVICEADDED:
            self._open(sdl2, event.jdevice.which)
        elif kind == sdl2.SDL_JOYDEVICEREMOVED:
            self._close(sdl2, event.jdevice.which)
        elif kind in (sdl2.SDL_JOYBUTTONDOWN, sdl2.SDL_JOYBUTTONUP):
            self._raw_button(event.jbutton.which, event.jbutton.button,
                             kind == sdl2.SDL_JOYBUTTONDOWN)
        elif kind == sdl2.SDL_JOYAXISMOTION:
            self._raw_axis(event.jaxis.which, event.jaxis.axis, event.jaxis.value)
        elif kind == sdl2.SDL_JOYHATMOTION:
            self._raw_hat(event.jhat.which, event.jhat.hat, event.jhat.value)
        elif kind in (sdl2.SDL_CONTROLLERBUTTONDOWN, sdl2.SDL_CONTROLLERBUTTONUP):
            mapped = controller_button(event.cbutton.button,
                                       kind == sdl2.SDL_CONTROLLERBUTTONDOWN)
            if mapped:
                self._post_pad(event.cbutton.which, [mapped])
        elif kind == sdl2.SDL_CONTROLLERAXISMOTION:
            code = CONTROLLER_AXES.get(event.caxis.axis)
            if code is not None:
                self._post_pad(event.caxis.which, [PadEvent(EV_ABS, code, event.caxis.value, "pad")])

    def _device(self, instance: int) -> PadInfo | None:
        with self._lock:
            return self._pads.get(instance)

    def _post_pad(self, instance: int, events) -> None:
        pad = self._device(instance)
        if pad is not None:
            self._post(pad.device, events)

    def _raw_button(self, instance: int, button: int, pressed: bool) -> None:
        pad = self._device(instance)
        if pad is None:
            return
        events = [PadEvent(EV_KEY, button, int(pressed), "raw")]
        if not pad.controller:
            events.append(PadEvent(EV_KEY, BTN_JOYSTICK + button, int(pressed), "pad"))
        self._post(pad.device, events)

    def _raw_axis(self, instance: int, axis: int, value: int) -> None:
        pad = self._device(instance)
        if pad is not None:
            self._post(pad.device, [PadEvent(EV_ABS, axis, value, "raw")])

    def _raw_hat(self, instance: int, hat: int, mask: int) -> None:
        pad = self._device(instance)
        if pad is None:
            return
        events = hat_events(hat, mask, "raw")
        if not pad.controller:
            events += hat_events(hat, mask, "pad")
        self._post(pad.device, events)

    def _open(self, sdl2, index: int) -> None:
        import ctypes

        joystick = sdl2.SDL_JoystickOpen(index)
        if not joystick:
            return
        controller = sdl2.SDL_GameControllerOpen(index) if sdl2.SDL_IsGameController(index) else None
        instance = sdl2.SDL_JoystickInstanceID(joystick)
        guid_text = ctypes.create_string_buffer(33)
        sdl2.SDL_JoystickGetGUIDString(sdl2.SDL_JoystickGetGUID(joystick), guid_text, 33)
        guid = guid_text.value.decode()
        buttons = sdl2.SDL_JoystickNumButtons(joystick)
        axes = sdl2.SDL_JoystickNumAxes(joystick)
        rest = []
        for axis in range(max(0, axes)):
            state = ctypes.c_int16(0)
            sdl2.SDL_JoystickGetAxisInitialState(joystick, axis, ctypes.byref(state))
            rest.append(state.value)
        serial = sdl2.SDL_JoystickGetSerial(joystick) or b""
        has_guide = bool(controller) and bool(
            sdl2.SDL_GameControllerHasButton(controller, sdl2.SDL_CONTROLLER_BUTTON_GUIDE))
        pad = PadInfo(
            device=f"sdl{instance}",
            name=(sdl2.SDL_JoystickName(joystick) or b"").decode(errors="replace"),
            vendor=f"{sdl2.SDL_JoystickGetVendor(joystick):04x}",
            product=f"{sdl2.SDL_JoystickGetProduct(joystick):04x}",
            guid=guid, serial=normalize_serial(serial.decode(errors="replace")),
            bus=bus_of(guid),
            is_pad=is_padlike(sdl2.SDL_JoystickGetType(joystick), buttons, bool(controller)),
            has_guide=has_guide,
            counts=(buttons, max(0, axes), max(0, sdl2.SDL_JoystickNumHats(joystick))),
            rest=tuple(rest), power=sdl2.SDL_JoystickCurrentPowerLevel(joystick),
            controller=bool(controller))
        with self._lock:
            self._pads[instance] = pad
            self._handles[instance] = (joystick, controller)
        log.info("gamepads: %s connected (%s:%s, %s)", pad.name, pad.vendor, pad.product,
                 pad.device)

    def _close(self, sdl2, instance: int) -> None:
        with self._lock:
            pad = self._pads.pop(instance, None)
            joystick, controller = self._handles.pop(instance, (None, None))
        if controller:
            sdl2.SDL_GameControllerClose(controller)
        if joystick:
            sdl2.SDL_JoystickClose(joystick)
        if pad is not None:
            log.info("gamepads: %s disconnected (%s)", pad.name, pad.device)
            self._post(pad.device, [GONE])

    def _poll_power(self, sdl2) -> None:
        with self._lock:
            handles = dict(self._handles)
        for instance, (joystick, _controller) in handles.items():
            level = sdl2.SDL_JoystickCurrentPowerLevel(joystick)
            with self._lock:
                if instance in self._pads:
                    self._pads[instance].power = level


_reader = _Reader()


def start() -> bool:
    """Start the SDL thread (once). False when SDL cannot run on this PC."""
    return _reader.start()


def unavailable() -> str:
    """Why no pad can be read, or "" when SDL runs."""
    return _reader.error


def pads() -> list[PadInfo]:
    """Every connected joystick, in connection order."""
    return _reader.snapshot()


def subscribe(device: str) -> asyncio.Queue:
    """A queue of `PadEvent` for `device`, on the running loop; `GONE` ends it."""
    return _reader.subscribe(device)


def unsubscribe(device: str, queue: asyncio.Queue) -> None:
    _reader.unsubscribe(device, queue)


def sdl2_library() -> str:
    """Path of the SDL2.dll the backend loads, for the probe subprocesses; "" if none."""
    try:
        import sdl2dll
        path = os.path.join(sdl2dll.get_dllpath(), "SDL2.dll")
    except ImportError:
        return ""
    return path if os.path.isfile(path) else ""
