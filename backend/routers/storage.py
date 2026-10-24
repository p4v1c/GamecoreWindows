"""External disks, from the settings screen.

Listing is the easy half. The half that matters is **Eject**: pulling a disk
with unwritten data is how a save is lost, and "has it finished writing" is not
a question anyone can answer by looking at it. A button that flushes and detaches
is the only honest answer, and it is why this router exists rather than a
paragraph of documentation asking players to be careful.

Windows mounts a disk itself; Eject is Windows' "Safely remove"
(`platform/volumes.py`).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import storage

router = APIRouter(tags=["storage"])


@router.get("/storage/volumes")
def list_volumes():
    """Every external disk, mounted or not.

    Internal disks are never listed: an Eject button next to the system drive
    is a way to break the PC from a sofa.
    """
    return {"ok": True, "volumes": storage.report()}


class DeviceBody(BaseModel):
    """A drive (`E:`), never a list index.

    A row position is not a handle: a disk arriving while the screen is open
    renumbers the list, and an Eject that acted on "the third row" would then
    detach the wrong disk. The device path is what the player is looking at.
    """
    device: str


def _volume_or_404(device: str) -> storage.Volume:
    volume = storage.find(device)
    if volume is None:
        # Genuinely routine: the disk was pulled between the screen being drawn
        # and the button being pressed. 404 says so; a 500 would read as a bug.
        raise HTTPException(404, f"{device} is not attached")
    return volume


@router.post("/storage/mount")
def mount_volume(body: DeviceBody):
    ok, detail = storage.mount(_volume_or_404(body.device))
    if not ok:
        raise HTTPException(503, detail)
    return {"ok": True, "detail": detail}


@router.post("/storage/unmount")
def unmount_volume(body: DeviceBody):
    """Flush and detach, so the cable can safely be pulled.

    A POST with a body rather than a DELETE with a path parameter, so the
    cross-origin guard in main.py covers it exactly as it covers every other
    write that matters — the same reason `/controllers/mapping/forget` is one.
    """
    ok, detail = storage.unmount(_volume_or_404(body.device))
    if not ok:
        # "busy" is the common failure and it is actionable: a game is still
        # reading the disk. The reason is passed through as it is.
        raise HTTPException(409, detail)
    return {"ok": True, "detail": detail}
