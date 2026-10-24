"""External disks: what is plugged in, where it lands, and when it goes away.

Two roots, and the difference between them is the whole design:

  the DRIVE LETTER     where Windows put the disk today (`E:\\`). Not ours,
                       not stable, and not something to record anywhere.
  the STABLE LINK      `<DATA>/volumes/<slug>`: a junction WE own, named after
                       the disk's label, pointing at wherever it landed today.

Windows hands out the first free letter, so the same disk is `E:` one day and
`F:` the next (a phone or a card reader plugged first). A `romsPath` recorded
against a letter is a library that silently scans nothing the day another
device takes it. Recorded against the stable link it survives, because the
link is re-pointed on every arrival.

`<DATA>/volumes/my-disk/roms` is a relative path like any other in
systems.json, so `paths.resolve_data_path` already resolves it and no consumer
needed changing.

Windows mounts a disk itself: plugged in and mounted are one state here, and
`mount()` only says so. Every call that touches a device goes through
`platform/volumes.py`, which the tests replace: no test ejects a real disk.

**Removal is expected, not exceptional.** A living-room box has its disk pulled
mid-game sooner or later. Every reader here treats a vanished path as a normal
state, so the failure mode is an empty system, never a traceback that takes
the grid down.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .platform import filesystem
from .platform import volumes as platform_volumes

log = logging.getLogger(__name__)

# Filesystems with no journal: an interrupted write (a cable pulled, a power
# cut) can leave a save half written, with no way back. ROMs are only ever read
# and are perfectly happy there; saves belong on NTFS. `describe()` is what
# says so out loud rather than letting someone find out when a save is gone.
NO_JOURNAL = frozenset({"exfat", "fat32", "fat", "vfat", "msdos"})

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Volume:
    """One filesystem that could hold a library."""
    name: str                 # E:
    path: str                 # E: (the handle the API uses)
    label: str
    uuid: str
    fstype: str
    mountpoint: str           # E:\ ("" when Windows gave it no letter)
    removable: bool
    size: str

    @property
    def is_mounted(self) -> bool:
        return bool(self.mountpoint)

    @property
    def slug(self) -> str:
        return slug_for(self.label, self.uuid)

    @property
    def keeps_permissions(self) -> bool:
        """Safe for saves: a journaled filesystem (the API's historical name)."""
        return self.fstype not in NO_JOURNAL


def slug_for(label: str, uuid: str = "") -> str:
    """The stable directory name for a disk, from its label.

    Derived from the LABEL because that is the only name the owner chose and
    the only one they will recognise in a settings screen — `roms-disk`, not
    `A4E2-19F7`. The uuid is the fallback, not the primary: a disk with no
    label still has to be reachable, but naming every disk by uuid would make
    the one path a person has to type unreadable.

    Lowercased and stripped to `[a-z0-9-]` because this becomes a path
    component that ends up inside `systems.json`, and a label may legitimately
    contain a space, a slash or a quote. `My ROMs/2` naming a directory two
    levels deep is a library that scans the wrong place, silently.

    A label that survives none of that (`"///"`, or Cyrillic) falls back to the
    uuid too — an empty slug would collide with every other empty slug and
    point every unlabelled disk at one directory.
    """
    slug = _SLUG_STRIP.sub("-", label.strip().lower()).strip("-")
    if slug:
        return slug
    tail = _SLUG_STRIP.sub("-", uuid.strip().lower()).strip("-")
    return f"disk-{tail}" if tail else ""


# ── reading what is plugged in ───────────────────────────────────────────────

def list_volumes() -> list[Volume]:
    """Every external disk the PC can see. Never raises."""
    try:
        return [Volume(**row) for row in platform_volumes.list_volumes()]
    except Exception:
        log.exception("storage: could not list volumes")
        return []


# ── the stable link ──────────────────────────────────────────────────────────

def link_path(volume: Volume) -> Path | None:
    """`<DATA>/volumes/<slug>`, or None for a disk with no usable name."""
    slug = volume.slug
    return (paths.volumes_dir() / slug) if slug else None


def link_volume(volume: Volume) -> Path | None:
    """Point `<DATA>/volumes/<slug>` at wherever this disk landed today.

    A junction, re-pointed on every arrival, because the drive letter is not
    stable and a `romsPath` is. A real folder the owner put at that path is
    left alone: replacing it would hide their files behind the disk.
    """
    if not volume.is_mounted:
        return None
    target = link_path(volume)
    if target is None:
        log.warning("storage: %s has neither a usable label nor a uuid — "
                    "no stable path can be derived for it", volume.path)
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Replaced, never merged into. An is_dir() check would follow the old
        # link and answer about the PREVIOUS disk.
        if filesystem.is_link(target):
            target.unlink()
        elif target.exists():
            log.warning("storage: %s is a real folder, not ours; not linking %s",
                        target, volume.mountpoint)
            return None
        filesystem.link_dir(target, Path(volume.mountpoint))
    except OSError:
        log.exception("storage: could not link %s → %s", target, volume.mountpoint)
        return None
    log.info("storage: %s → %s", target, volume.mountpoint)
    return target


def unlink_volume(volume: Volume) -> bool:
    """Drop the stable link for a disk that has gone.

    Deliberately leaves a folder that is not ours alone: a link check, never a
    recursive delete. `<DATA>/volumes/<slug>` is a path
    an owner may legitimately have replaced with a real directory, and this
    module removing a directory tree it did not create is the one bug in here
    that would cost someone their ROMs.
    """
    target = link_path(volume)
    if target is None or not filesystem.is_link(target):
        return False
    try:
        target.unlink()
    except OSError:
        log.exception("storage: could not remove %s", target)
        return False
    return True


def stale_links(live: list[Volume]) -> list[Path]:
    """Links under `<DATA>/volumes` that point nowhere reachable.

    A PC is powered off with a disk attached and comes up without it: nothing
    generated an arrival or a departure, so the link from the last session is
    still there, pointing at a drive letter that is gone or now belongs to
    another device.
    """
    keep = {link_path(v) for v in live if v.is_mounted}
    try:
        entries = list(paths.volumes_dir().iterdir())
    except OSError:
        return []
    return [p for p in entries
            if filesystem.is_link(p) and p not in keep and not p.is_dir()]


# ── acting on a device ───────────────────────────────────────────────────────

def mount(volume: Volume) -> tuple[bool, str]:
    """Windows mounts a disk itself: (True, its drive) or why it has none."""
    if volume.is_mounted:
        return True, volume.mountpoint
    return False, "Windows gave this disk no drive letter. Assign one in Disk Management."


def unmount(volume: Volume) -> tuple[bool, str]:
    """Safely remove, so the cable can be pulled. (ok, message).

    The whole point is the flush. Pulling a disk that has unwritten data is how
    a save is lost, and "it looked finished" is not something a player can
    check.
    """
    if not volume.is_mounted:
        return True, "not mounted"
    ok, detail = platform_volumes.eject(volume.path)
    if ok:
        unlink_volume(volume)
    return ok, detail


# ── what the UI is told ──────────────────────────────────────────────────────

def describe(volume: Volume) -> dict:
    """One row for the storage screen."""
    link = link_path(volume)
    return {
        "name": volume.name,
        "device": volume.path,
        "label": volume.label,
        "uuid": volume.uuid,
        "fstype": volume.fstype,
        "size": volume.size,
        "mountpoint": volume.mountpoint,
        "mounted": volume.is_mounted,
        "slug": volume.slug,
        # What a romsPath should be written against — never the mountpoint.
        "stable_path": str(link) if link else "",
        "keeps_permissions": volume.keeps_permissions,
        "saves_warning": "" if volume.keeps_permissions else SAVES_WARNING,
    }


SAVES_WARNING = (
    "This disk is FAT32 or exFAT, which keeps no journal. ROMs are fine here: "
    "they are only ever read. A save being written when the cable is pulled or "
    "the power goes can be lost. Keep saves on an NTFS disk."
)


def report() -> list[dict]:
    """Every external disk. Never raises."""
    try:
        return [describe(v) for v in list_volumes()]
    except Exception:
        log.exception("storage: report failed")
        return []


def find(device: str) -> Volume | None:
    """The volume for a device (`E:`), or None; case does not matter.

    By device path and not by index: a list position is not a handle, and an
    Unmount button that acted on "the third row" would unmount the wrong disk
    the moment one arrived while the screen was open.
    """
    for volume in list_volumes():
        if volume.path.upper() == device.upper():
            return volume
    return None


# ── who is affected when a disk goes ─────────────────────────────────────────

def path_is_under(path: str | Path, mountpoint: str) -> bool:
    """Is `path` inside `mountpoint` — without touching the disk.

    Purely lexical, deliberately. The question is asked precisely when the
    mount has just vanished, so anything that stats the path answers about a
    directory that is no longer there. `Path.is_relative_to` on the strings is
    the only form that still works after the cable is out.
    """
    if not path or not mountpoint:
        return False
    try:
        return Path(path).is_relative_to(Path(mountpoint))
    except (TypeError, ValueError):
        return False
