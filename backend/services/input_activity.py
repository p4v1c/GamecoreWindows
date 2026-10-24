"""Which pad events mean somebody is at the controller: standby's input.

The events are `platform/gamepads.py`'s pad layer, in the evdev vocabulary.
"""
from types import SimpleNamespace

from .gamepad_devices import EV_ABS, EV_KEY, KEY_DOWN
from .platform.gamepads import axis_range

# The d-pad, as the pad layer reports it: a hat, not four buttons.
# ABS_HAT0X/Y and their second-hat siblings.
HAT_CODES = frozenset({0x10, 0x11, 0x12, 0x13})

# How far an axis must move, as a fraction of its full travel, before it counts
# as somebody being there.
#
# There has to be a threshold at all, and that is the whole difficulty of
# reading axes for standby. A resting stick reports a trickle of noise forever;
# a box that took that for activity would never sleep, which is a worse fault
# than the one this fixes. A quarter of the travel is far past any drift and far
# short of a deliberate push.
STICK_TRAVEL = 0.25


class _PadAxes:
    """The axis ranges of the pad layer, in the shape `ActivityFilter` asks for."""

    def absinfo(self, code: int):
        low, high = axis_range(code)
        return SimpleNamespace(min=low, max=high)


PAD_AXES = _PadAxes()


class ActivityFilter:
    """Does this event mean somebody is at the controller?

    Split out of the read loop so it can be asked directly — see
    tests/test_standby_input.py. Counting `EV_KEY` down alone misses the d-pad
    (a hat) and the sticks: browsing with them alone brought the screensaver
    up on somebody actively using the box.

    Movement is what counts, not position: measured against the axis's last
    reported value rather than its centre. Position would need to know where
    each axis rests, and a trigger rests at its minimum while a stick rests in
    the middle — a rule written as "far from the centre" calls every resting
    trigger a held one. Travel needs to know nothing, and it also means a slow
    drift never adds up to a press however far it wanders.
    """

    def __init__(self, dev):
        self._dev = dev
        self._last: dict[int, int] = {}
        self._span: dict[int, float] = {}

    def _threshold(self, code: int) -> float | None:
        """How much travel is a lot, on this axis. None if the pad cannot say."""
        if code in self._span:
            return self._span[code] or None
        try:
            info = self._dev.absinfo(code)
            span = (info.max - info.min) * STICK_TRAVEL
        except Exception:
            # No absinfo, no idea what "a long way" means here. Ignoring the
            # axis keeps a device nobody can measure from holding the box awake.
            span = 0.0
        self._span[code] = span
        return span or None

    def is_activity(self, event) -> bool:
        if event.type == EV_KEY:
            return event.value == KEY_DOWN
        if event.type != EV_ABS:
            return False
        if event.code in HAT_CODES:
            return event.value != 0          # a hat is a d-pad: digital
        threshold = self._threshold(event.code)
        if threshold is None:
            return False
        previous = self._last.get(event.code)
        self._last[event.code] = event.value
        # The first reading of an axis only takes a bearing. A pad announces
        # every axis as it connects, and a burst of "activity" then would wake
        # the box each time a Bluetooth pad re-paired itself overnight.
        if previous is None:
            return False
        return abs(event.value - previous) > threshold
