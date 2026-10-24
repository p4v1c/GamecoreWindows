"""The peripherals that are not SDL gamepads, and whether they are on the bus.

The autoconfig pipeline knows exactly one kind of device: a pad SDL opens.
That is the whole roster — `gamepad_monitor` reads SDL's list,
`controller_registry` hands out player slots, and a generator writes a config
for whatever took a slot.

Every peripheral that does not enter through that door is invisible:

  · the **GameCube adapter** Dolphin drives over raw libusb. SDL does not see
    it at all, so nothing in the pipeline can, and the owner has no way to
    tell "the adapter is not plugged in" from "Dolphin is ignoring it";
  · the **DolphinBar** and its Wiimotes, which enumerate as several HID
    interfaces whose shape depends on the mode switch on the bar;
  · **arcade sticks** that enumerate as a keyboard, which SDL does not open;
  · **wheels**, whose force-feedback node is separate from their button node;
  · the **DS3 passthrough** RPCS3 wants over raw HID.

`gamepad_monitor` is right to keep dropping these: a player slot is for
something that can be player 2, and a light gun is not. The gap is that there
was no *other* list either. This is that list.

What a pack declares is in `catalog/<id>/pack.json` under `usb`; whether the
box has it is here. Two verdicts, and nothing in between:

    present     a device with this vendor:product is on the USB bus
    absent      it is not

**Read-only, always.** This module reads Windows' device list
(`platform/usb.py`) and does nothing else. The Linux packs' udev rule text is
carried through to `installer/applier.py`, which is the only place allowed to
put one on a disk, and only at install time.

**Absent never refuses a launch.** That is deliberate, and it is the one design
decision here worth arguing with. Dolphin plays perfectly with a DualShock 4
and no adapter; RPCS3 does not need a real DS3. A USB accessory is by nature
optional, so refusing to start would be GameCore inventing a fault — the exact
mistake `bios.py` documents at length under `required: false`. What the owner
lacks is not permission to play, it is the sentence "the adapter you plugged in
is not being seen", which is what `launch_notice()` returns.
"""
from __future__ import annotations

import logging
from .catalog import load_catalog
from .platform import usb as platform_usb

log = logging.getLogger(__name__)

# The classes a pack may declare. This is the roster widening: `gamepad` is what
# the pipeline already handled, and the other four are the ones it could not
# express at all.
#
# It is an enum in the schema too, so a shipped pack cannot invent a sixth. It
# is NOT enforced here: a local pack under config/catalog.d/ is data the
# operator wrote, and a class this release has never heard of must degrade to
# "listed, not understood" rather than take the controllers screen down with a
# KeyError. `class_of()` is that degradation.
CLASSES = ("gamepad", "adapter", "wheel", "lightgun", "arcade")

UNKNOWN = "unknown"

PRESENT = "present"
ABSENT = "absent"


def class_of(spec: dict) -> str:
    """The declared class, or UNKNOWN for one this release does not know.

    Never raises and never guesses. A pack from a newer catalogue naming
    `dancemat` gets listed as unknown — the owner still sees the device and its
    note, which is the whole point of the screen, and the box does not pretend
    the class means something it does not.
    """
    value = spec.get("class", "")
    return value if value in CLASSES else UNKNOWN


def normalize_vid_pid(raw: str) -> str:
    """`054C:0BA0` → `054c:0ba0`, and anything unparseable → "".

    Windows writes upper case, pack.json is schema-pinned to lowercase, and a
    rule conventionally uses whichever case the person writing it preferred.
    One spelling reaches the comparison, so a rule and a pack that name the
    same adapter in different case are still the same adapter.
    """
    parts = raw.strip().lower().split(":")
    if len(parts) != 2:
        return ""
    vendor, product = parts
    if len(vendor) != 4 or len(product) != 4:
        return ""
    if not all(c in "0123456789abcdef" for c in vendor + product):
        return ""
    return f"{vendor}:{product}"


def inventory() -> dict[str, str]:
    """`vid:pid` → the friendliest name Windows offers, for everything on the bus.

    Never raises. This feeds a settings screen and a launch, and a PC whose
    device list cannot be read must produce an empty inventory ("nothing
    detected") rather than an exception on the way to the grid.
    """
    try:
        return platform_usb.inventory()
    except OSError:
        log.debug("usb_devices: the device list could not be read", exc_info=True)
        return {}


def _rows_for(pack, present: dict[str, str]) -> list[dict]:
    """One row per `usb` entry the pack declares, against a live inventory."""
    rows = []
    for spec in pack.data.get("usb") or []:
        vid_pid = normalize_vid_pid(spec.get("vidPid", ""))
        here = vid_pid in present
        rows.append({
            "system_id": pack.id,
            "system_label": pack.data["label"],
            "vid_pid": vid_pid or spec.get("vidPid", ""),
            "class": class_of(spec),
            # The pack's own words. A generic "device not found" is what the
            # owner already has; "the mode switch on the bar must be on 1" is
            # the thing that ends the call.
            "label": spec.get("label", "") or spec.get("vidPid", ""),
            "note": spec["note"],
            "detected_as": present.get(vid_pid, ""),
            "status": PRESENT if here else ABSENT,
        })
    return rows


def report(*, packs: dict | None = None, present: dict[str, str] | None = None) -> list[dict]:
    """Every declared peripheral on the box, in catalogue order.

    This is what the controllers screen lists under the player slots. Without
    it there is no way to tell an adapter that is not plugged in from one the
    box cannot see — the two look identical from a sofa, and only one of them
    is fixed by plugging it in.

    Never raises: same reason as `bios.summary`.
    """
    try:
        packs = load_catalog() if packs is None else packs
        present = inventory() if present is None else present
        ordered = sorted(packs.values(),
                         key=lambda p: (p.data.get("order", 10_000), p.id))
        out: list[dict] = []
        for pack in ordered:
            out.extend(_rows_for(pack, present))
        return out
    except Exception:
        log.exception("usb_devices: report failed")
        return []


def missing_for(system_id: str, *, packs: dict | None = None,
                present: dict[str, str] | None = None) -> list[dict]:
    """The peripherals this system declares and the box has not got.

    Never raises. A check that cannot run must cost the player a missing
    sentence, never a game that does not start.
    """
    try:
        packs = load_catalog() if packs is None else packs
        pack = packs.get(system_id)
        if pack is None or not pack.data.get("usb"):
            return []
        present = inventory() if present is None else present
        return [r for r in _rows_for(pack, present) if r["status"] == ABSENT]
    except Exception:
        log.exception("usb_devices: check for %r failed", system_id)
        return []


def launch_notice(system_id: str, *, packs: dict | None = None,
                  present: dict[str, str] | None = None) -> str:
    """One sentence about the accessories that are not here, or "".

    A NOTICE, not a blocker — see the module docstring. The caller broadcasts
    it and starts the emulator anyway.
    """
    missing = missing_for(system_id, packs=packs, present=present)
    if not missing:
        return ""
    label = missing[0]["system_label"]
    parts = [f"{r['label']} ({r['vid_pid']}) is not on the USB bus. {r['note']}"
             for r in missing]
    return f"{label}: " + " ".join(parts)


def declares_usb(pack) -> bool:
    """Does this pack need a udev re-fire on launch, beyond the pad case.

    `launch.gamepadTrigger` re-fires udev so a Flatpak app sees a pad plugged in
    after it started. Every word of that reason applies to an adapter, a wheel
    and a light gun — and none of them could ask for it, because the flag was
    spelled for pads and only Stremio set it. A GameCube adapter plugged in
    after Dolphin's sandbox started stayed invisible until the game was quit
    and relaunched, which is indistinguishable from an adapter that does not
    work.
    """
    return bool(pack.data.get("usb"))


def udev_rules(pack) -> list[str]:
    """The rule lines this pack declares, in order, with a comment naming why.

    Returned as text, never written: `installer/applier.py` is the only place
    that puts one on a disk, and only at install time as root. Nothing on a
    running box calls this.
    """
    lines: list[str] = []
    for spec in pack.data.get("usb") or []:
        rule = spec.get("udevRule")
        if not rule:
            continue
        label = spec.get("label", "") or spec.get("vidPid", "")
        lines.append(f"# {pack.id}: {label} — {spec['note']}")
        lines.append(rule)
    return lines
