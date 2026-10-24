"""Standby state + configuration."""
from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services import desktop_power, standby, standby_videos

router = APIRouter(tags=["standby"])


@router.get("/standby")
def get_standby():
    return {"state": standby.get_state(), **standby.load_config()}


class StandbyConfig(BaseModel):
    enabled: bool | None = None
    screensaver_mins: int | None = None
    sleep_mins: int | None = None
    wake_settle_secs: float | None = None


@router.post("/standby/config")
async def set_config(cfg: StandbyConfig):
    saved = standby.save_config({k: v for k, v in cfg.model_dump().items() if v is not None})
    # Switching standby OFF has to be able to end the standby it is switching
    # off. The watcher's first line is "not enabled → nothing to do", so nothing
    # ever undid what the last tick had done: the box answered "standby
    # disabled" and stayed asleep, screen and all. Somebody reaching for that
    # switch from a phone is doing it precisely because the television is dark.
    #
    # Only OFF. Turning it on is not a reason to light the screen, and waking on
    # any config write would mean the box could never settle while somebody was
    # adjusting the timings.
    # And the screen timeout goes back to the desktop, because GameCore has
    # just stopped managing it. Without this the switch disarms BOTH: GameCore
    # steps back and the desktop's own timer is still disabled behind it, so
    # the television never goes dark again — the exact opposite of what a
    # switch marked "standby" is understood to do, and invisible until somebody
    # notices the TV has been on all night.
    if cfg.enabled is False:
        await desktop_power.release()
        await standby.exit_standby()
    elif cfg.enabled is True:
        await desktop_power.claim()
    return {"ok": True, **saved}


class DesktopMode(BaseModel):
    active: bool


@router.post("/standby/desktop")
async def desktop_mode(body: DesktopMode):
    """The shell left for the Windows desktop, or came back from it."""
    await standby.set_desktop(body.active)
    return {"ok": True}


@router.post("/standby/exit")
async def wake():
    await standby.exit_standby()
    return {"ok": True}


@router.post("/standby/dark")
async def ui_dark():
    """The UI has faded to black; the sleep stage may switch the screen off."""
    standby.ui_dark()
    return {"ok": True}


@router.get("/standby/videos")
async def standby_playlist(favourite: list[str] = Query(default=[])):
    """Clips on disk for the standby TV, plus screenshots for games without one.

    `favourite` (repeatable, `system_id:filename`) makes those games come up
    more often; favourites live in the theme, not on the backend.
    """
    favs = {(f.split(":", 1)[0].lower(), f.split(":", 1)[1]) for f in favourite if ":" in f}
    return await standby_videos.playlist(favourites=favs)
