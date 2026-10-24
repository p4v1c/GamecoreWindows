"""Audio outputs and volume. The Core Audio calls: services/platform/audio.py."""
import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ...services.platform import audio as platform_audio

router = APIRouter(prefix="/settings/audio", tags=["audio"])
log = logging.getLogger(__name__)


@router.get("")
async def get_audio():
    try:
        volume, muted = await asyncio.to_thread(platform_audio.get_volume)
    except OSError as e:
        log.warning("audio: volume unreadable: %s", e)
        volume, muted = 100, False
    return {"volume": max(0, min(100, volume)), "muted": muted}


@router.get("/sinks")
async def list_sinks():
    """The audio outputs, and only those: render endpoints that are active."""
    try:
        return await asyncio.to_thread(platform_audio.list_outputs)
    except OSError as e:
        log.warning("audio: outputs unreadable: %s", e)
        return []


class VolumeRequest(BaseModel):
    volume: int


@router.post("/volume")
async def set_volume(req: VolumeRequest):
    vol = max(0, min(100, req.volume))
    try:
        await asyncio.to_thread(platform_audio.set_volume, vol)
    except OSError as e:
        return {"ok": False, "error": str(e) or "Could not set the volume."}
    return {"ok": True, "volume": vol}


class SinkRequest(BaseModel):
    sink: str  # endpoint id, as /sinks lists it


@router.post("/sink")
async def set_sink(req: SinkRequest):
    try:
        await asyncio.to_thread(platform_audio.set_default_output, req.sink)
    except OSError as e:
        log.warning("audio: output %s refused: %s", req.sink, e)
        return {"ok": False, "error": str(e)}
    log.info("audio: output set to %s", req.sink)
    return {"ok": True}
