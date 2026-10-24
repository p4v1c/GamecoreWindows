"""Bluetooth: paired devices, scan, pair, forget. The WinRT calls: services/platform/bluetooth.py.

Nothing is ever paired automatically. The scan lists what is in range and the
player picks: a box that paired with whatever was discoverable would adopt the
neighbours' headphones.

Windows connects a paired device when the device asks and has no call to force
it, so Connect and Disconnect report the state rather than change it.
"""
import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services.logs import mask_mac
from ...services.platform import bluetooth as platform_bluetooth

router = APIRouter(prefix="/settings/bluetooth", tags=["bluetooth"])
log = logging.getLogger(__name__)

_MAC_RE = re.compile(r'^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$', re.I)

# How long the adapter looks around. Long enough for a pad just put into
# pairing mode to advertise; the UI says how long it will be.
SCAN_SECS = 10
# A pairing is a negotiation with a device that may be asleep or out of range.
PAIR_SECS = 25

NOT_FORCED = ("Windows connects it by itself: switch the device on, or press "
              "its connect button.")


def unnamed(name: str, mac: str) -> bool:
    """A device with no name yet, or whose name is its own address."""
    bare = lambda v: v.replace(":", "").replace("-", "").strip().upper()  # noqa: E731
    return not name.strip() or bare(name) == bare(mac)


def named_first(devices: list[dict]) -> list[dict]:
    """Devices with a real name first, then the bare addresses, each group alphabetical."""
    return sorted(devices, key=lambda d: (unnamed(d["name"], d["mac"]), d["name"].lower()))


def _public(devices: list[dict], paired: bool) -> list[dict]:
    return named_first([{"mac": d["mac"], "name": d["name"] or d["mac"],
                         "connected": bool(d["connected"]) and paired, "paired": paired}
                        for d in devices])


def _address(mac: str) -> str:
    mac = mac.upper()
    if not _MAC_RE.fullmatch(mac):
        raise HTTPException(status_code=400, detail="Not a Bluetooth address")
    return mac


async def _paired() -> list[dict]:
    try:
        return await platform_bluetooth.paired_devices()
    except OSError as e:
        log.warning("bluetooth: paired list unreadable: %s", e)
        return []


@router.get("/devices")
async def list_devices():
    """Every paired device, with whether it is connected right now."""
    return _public(await _paired(), paired=True)


@router.post("/scan")
async def start_scan():
    """Look around for SCAN_SECS, then answer with what is in range and not paired."""
    try:
        found = await platform_bluetooth.scan(SCAN_SECS)
    except OSError as e:
        log.warning("bluetooth: scan failed: %s", e)
        found = []
    return {"ok": True, "found": _public(found, paired=False), "seconds": SCAN_SECS}


class DeviceRequest(BaseModel):
    mac: str


@router.post("/pair")
async def pair_device(req: DeviceRequest):
    """Pair the device the player chose; Windows connects it once it is awake."""
    mac = _address(req.mac)
    try:
        ok, message = await asyncio.wait_for(platform_bluetooth.pair(mac), PAIR_SECS)
    except asyncio.TimeoutError:
        ok, message = False, "The device did not answer in time"
    except OSError as e:
        ok, message = False, str(e) or "Pairing failed"
    if not ok:
        log.warning("bluetooth: pairing %s failed: %s", mask_mac(mac), message)
        return {"ok": False, "message": message}
    connected = any(d["mac"] == mac and d["connected"] for d in await _paired())
    log.info("bluetooth: paired %s, %s", mask_mac(mac),
             "connected" if connected else "not connected yet")
    if connected:
        return {"ok": True, "message": "Paired and connected"}
    # Paired but not yet connected is a real state: the device is known now.
    return {"ok": True, "message": "Paired. Switch the device on to connect it"}


@router.post("/connect")
async def connect_device(req: DeviceRequest):
    mac = _address(req.mac)
    if any(d["mac"] == mac and d["connected"] for d in await _paired()):
        return {"ok": True, "message": "Connected"}
    return {"ok": False, "message": NOT_FORCED}


@router.post("/disconnect")
async def disconnect_device(req: DeviceRequest):
    """Windows has no call to drop one device's link: ok only if it is already down."""
    mac = _address(req.mac)
    connected = any(d["mac"] == mac and d["connected"] for d in await _paired())
    return {"ok": not connected}


@router.delete("/devices/{mac}")
async def remove_device(mac: str):
    """Unpair and forget, then prove it went: the paired list is read back."""
    mac = _address(mac)
    try:
        ok, message = await platform_bluetooth.unpair(mac)
    except OSError as e:
        ok, message = False, str(e)
    if any(d["mac"] == mac for d in await _paired()):
        log.warning("bluetooth: %s is still paired after unpair", mask_mac(mac))
        return {"ok": False, "message": message if not ok else "The device is still paired"}
    log.info("bluetooth: forgot %s", mask_mac(mac))
    return {"ok": True, "message": "Forgotten. Pair it again to reconnect"}
