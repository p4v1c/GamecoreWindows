"""Which connected joysticks are pads, and one entry per physical pad.

Read from the SDL snapshot (`platform/gamepads.py`). A device is a string such
as `sdl3` (SDL's instance id, so the order is the order pads connected); its
`uniq` is the Bluetooth address when SDL knows one, which is what keeps a
pad's player slot across reconnections.
"""
import logging

from . import controller_registry
from .platform import gamepads as platform_gamepads

log = logging.getLogger(__name__)

# The evdev vocabulary `platform/gamepads.py` speaks (its pad layer).
BTN_MODE = 0x13C
GUIDE_CODES = frozenset({BTN_MODE})
BTN_SOUTH = 0x130
EV_KEY = 1    # key/button events
EV_ABS = 3    # axes: sticks, triggers, and the d-pad as a hat
KEY_DOWN = 1  # event value for key press
# Deliberately not called KEY_UP: evdev already has a KEY_UP and it is the
# arrow key, code 103. This is the *value* a release carries.
KEY_RELEASE = 0

# Devices already reported as "kept, but has no Guide button": the scan runs
# every few seconds and this should be said once per device, not per pass.
_logged_no_guide: set[str] = set()


def _find_gamepad_devices() -> dict[str, tuple[str, str, bool, str, str, int]]:
    """device → (name, uniq, is_pad, vendor, product, bustype), in connection order.

    `bustype` is the transport (0x03 USB, 0x05 Bluetooth). It is part of what
    a config is written FOR: an SDL GUID encodes the bus, so the same pad has
    another GUID over a cable, and a GUID-bound emulator needs rewriting.

    `is_pad` tells pads apart from wheels and flight sticks, which are watched
    (they wake the box) but take no player slot. A pad without a Guide button
    takes a slot and cannot trigger the guide shortcut.
    """
    found: dict[str, tuple[str, str, bool, str, str, int]] = {}
    for pad in platform_gamepads.pads():
        found[pad.device] = (pad.name, pad.serial, pad.is_pad, pad.vendor, pad.product, pad.bus)
        if pad.is_pad and not pad.has_guide and pad.device not in _logged_no_guide:
            _logged_no_guide.add(pad.device)
            log.info("gamepad_monitor: %s (%s) has no Guide button: taking a "
                     "player slot, but it cannot trigger the guide shortcut",
                     pad.name, pad.device)
    _logged_no_guide.intersection_update(found)
    return found


def _event_sort_key(device: str) -> tuple[str, int]:
    """Sort `sdl10` after `sdl2`, not before it: slots are handed out in this
    order on the first scan, so connection order decides, not string order."""
    head = device.rstrip("0123456789")
    tail = device[len(head):]
    return head, int(tail) if tail else -1


def pads_by_key(devices: dict[str, tuple[str, str, bool, str, str, int]]
                ) -> dict[str, tuple[str, str, str, int]]:
    """registry key → (vendor, product, name, bustype), one entry per PHYSICAL pad.

    Pads whose ids are still zero are left out: a slot handed out to a pad we
    refuse to profile would be held for ever.
    """
    out: dict[str, tuple[str, str, str, int]] = {}
    for device in sorted(devices, key=_event_sort_key):
        name, uniq, is_pad, vendor, product, bustype = devices[device]
        if not is_pad or vendor == "0000" or product == "0000":
            continue
        key = controller_registry.key_for(uniq, device)
        out.setdefault(key, (vendor, product, name, bustype))
    return out


def dup_indexes(roster: dict[str, tuple[int, str]]) -> dict[str, int]:
    """key → how many pads sharing the same RESOLVED NAME hold a lower slot.

    Every consumer counts by name: Dolphin writes `SDL/<dup>/<name>` and RPCS3
    `<name> <dup+1>`, both per-name counters. Counting by vendor:product gave a
    DualShock 4 v1 and a v2 (one SDL name) dup 0 both, and one pad drove two
    ports while the other was dead.
    """
    return {key: sum(1 for k2, (slot2, name2) in roster.items()
                     if k2 != key and name2 == name and slot2 < slot)
            for key, (slot, name) in roster.items()}
