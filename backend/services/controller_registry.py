"""Console-style player slots for connected controllers.

gamepad_monitor calls connect()/disconnect() as pads come and go; each pad
gets the lowest free slot (1-based), like on a console — plug a second pad
while the first is connected and it becomes player 2; unplug player 1 and
the next pad to arrive takes slot 1 back.

A pad is keyed by its Bluetooth address when SDL reports one (its serial),
else by its SDL device, so battery alerts and the TopBar pills can say
"Controller 2" instead of a device name.

Pure logic, no SDL access — unit-testable (see tests/test_controller_registry.py).
"""
import logging
import re

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})")

# registry key (normalized MAC, else SDL device) → player slot (1-based)
_slots: dict[str, int] = {}
_labels: dict[str, str] = {}


def normalize_mac(value: str | None) -> str:
    """Extract a lowercased aa:bb:cc:dd:ee:ff from any MAC-ish string.

    Handles an SDL serial ("84:30:95:07:C8:1C") and names that embed one
    ("ps-controller-battery-84:30:95:07:c8:1c"). Returns '' when no MAC is
    present.
    """
    if not value:
        return ""
    m = _MAC_RE.search(value.lower())
    return m.group(1) if m else ""


def key_for(uniq: str | None, path: str) -> str:
    """Stable registry key for a pad: its MAC when known, else its SDL device."""
    return normalize_mac(uniq) or path


def identity(key: str, vendor: str, product: str) -> str:
    """What a profile remembers a pad by: its MAC, else its model. Two
    identical pads without a MAC read as one; an SDL device changes on replug."""
    return normalize_mac(key) or f"{vendor}:{product}"


def has(key: str) -> bool:
    return key in _slots


def connect(key: str, label: str = "") -> int:
    """Assign the lowest free slot to `key`; idempotent for a known key."""
    if key in _slots:
        if label:
            _labels[key] = label
        return _slots[key]
    used = set(_slots.values())
    player = next(n for n in range(1, len(used) + 2) if n not in used)
    _slots[key] = player
    if label:
        _labels[key] = label
    log.info("controller_registry: player %d ← %s (%s)", player, key, label or "?")
    return player


def disconnect(key: str) -> int | None:
    """Free `key`'s slot and return the player number it had, if any."""
    _labels.pop(key, None)
    player = _slots.pop(key, None)
    if player is not None:
        log.info("controller_registry: player %d freed (%s)", player, key)
    return player


def player_for(key: str) -> int | None:
    return _slots.get(key)


def label_for(key: str) -> str:
    return _labels.get(key, "")


def player_for_mac(value: str | None) -> int | None:
    """Player slot for any MAC-bearing string (e.g. a power-supply name)."""
    mac = normalize_mac(value)
    return _slots.get(mac) if mac else None


def snapshot() -> list[dict]:
    """Connected pads ordered by slot: [{player, label}]."""
    return [
        {"player": player, "label": _labels.get(key, "")}
        for key, player in sorted(_slots.items(), key=lambda kv: kv[1])
    ]


def compact() -> dict[str, tuple[int, int]]:
    """Renumber the occupied slots to 1..N, preserving their order.

    Returns `key → (old, new)` for the pads that moved; empty when there was
    nothing to close up.

    Slots are handed out lowest-free-first and never given back while a pad
    stays connected — deliberately, so nobody changes player number mid-game.
    The cost is a gap: unplug player 1 during a two-player session and the pad
    that remains keeps slot 2 for good. Observed on the reference box with a
    single DualShock 4 left holding Player 2, and Ryujinx therefore presenting
    no Player 1 at all to a Switch game that wanted one.

    Deciding WHEN to call this is the caller's job, and it is the whole
    difficulty: closing the gap while a game runs would swap who player 1 is,
    mid-session. `_reconcile` only calls it with no game running.
    """
    moved: dict[str, tuple[int, int]] = {}
    for new, (key, old) in enumerate(sorted(_slots.items(), key=lambda kv: kv[1]), 1):
        if old != new:
            _slots[key] = new
            moved[key] = (old, new)
            log.info("controller_registry: player %d → %d (%s)", old, new,
                     _labels.get(key, "?"))
    return moved
