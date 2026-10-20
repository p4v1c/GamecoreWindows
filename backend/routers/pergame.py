"""Per-game settings, from the sofa.

Three things the options panel needs and cannot work out for itself:

  · what this game IS. The id comes out of the dump — a PARAM.SFO, a disc
    header, a meta.xml — and the frontend has no business opening game files.
  · whether the emulator supports this at all, and if not, the pack's own
    sentence saying why. An empty panel and an impossible feature look
    identical from a sofa, and only one of them is worth investigating.
  · whether a shipped profile is in place, and whether the player has taken it
    off. Three states, not two: "applied", "refused", and "exists but this box
    runs an emulator the profile does not claim to cover".

There is deliberately no endpoint for editing an individual setting. GameCore
does not know what a setting MEANS, and a screen that offered `Video` /
`Write Color Buffers` / `true` as three text boxes would be a config file
editor on a television. The button opens the emulator's own window instead,
and everything the player sets there GameCore then keeps, per game.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import configgen, pergame
from ..services.launch import start_window_task
from ..utils import SYSTEM_ID_RE
from ..services.catalog import launch as catalog_launch
from ..services.process_manager import SessionConflict, process_manager

router = APIRouter(tags=["pergame"])

_SYSTEM_ID_RE = SYSTEM_ID_RE  # the shared boundary — see backend/utils.py


def _checked(system_id: str) -> None:
    if not _SYSTEM_ID_RE.match(system_id):
        raise HTTPException(400, "Invalid system id")


def _state(system_id: str, rom: str) -> dict:
    """Everything the panel shows for one game, in one round trip.

    One request rather than three because the panel opens under a cursor the
    player is still moving: three sequential fetches is three chances to render
    a half-answered screen, and "supported but no game id yet" is a state that
    should never be visible.
    """
    if not pergame.supported(system_id):
        return {"system_id": system_id, "supported": False,
                "why": pergame.unsupported_reason(system_id),
                "gameId": None, "settings": {}, "profile": {"available": False},
                "canOpenSettings": False}

    game_id = pergame.identify(system_id, rom) if rom else None
    record = pergame.record(system_id, game_id) if game_id else {}
    return {
        "system_id": system_id,
        "supported": True,
        "why": None,
        # None here is not an error: a dump the readers cannot name is a normal
        # answer for a system whose identity lives inside a container nobody
        # has the keys to. The panel says so rather than showing an empty list.
        "gameId": game_id,
        "settings": record.get("settings") or {},
        "source": record.get("source"),
        "profile": (pergame.profile_state(system_id, game_id) if game_id
                    else {"available": False}),
        "canOpenSettings": pergame.settings_launcher(system_id) is not None,
    }


@router.get("/pergame/{system_id}")
async def get_per_game(system_id: str, rom: str = ""):
    """Never 404s. A system with no per-game support is a normal answer — ten
    of the thirteen — and an error status here would put a failed request in
    the log every time somebody opened the options panel on a working box."""
    _checked(system_id)
    return _state(system_id, rom)


class ProfileAction(BaseModel):
    rom: str
    # "remove" takes the shipped profile back off and remembers that the player
    # said so; "restore" is its inverse. Both are needed, or "remove" is a
    # one-way door nobody can safely try.
    action: str


@router.post("/pergame/{system_id}/profile")
async def act_on_profile(system_id: str, body: ProfileAction):
    _checked(system_id)
    if body.action not in ("remove", "restore"):
        raise HTTPException(400, "action must be 'remove' or 'restore'")
    game_id = pergame.identify(system_id, body.rom)
    if not game_id:
        raise HTTPException(404, "This game could not be identified")
    if not pergame.profile_for(system_id, game_id):
        raise HTTPException(404, "No shipped profile for this game")

    home = configgen.HOME
    if body.action == "remove":
        pergame.dismiss_profile(system_id, game_id, home)
    else:
        pergame.restore_profile(system_id, game_id, home)
    return {"ok": True, **_state(system_id, body.rom)}


class OpenSettings(BaseModel):
    rom: str = ""


@router.post("/pergame/{system_id}/open")
async def open_emulator_settings(system_id: str, body: OpenSettings):
    """Start the emulator's own window so the player can set what they came for.

    Through `process_manager`, exactly like a game, and that is the point: the
    box already knows how to hand the screen to a foreign window and take it
    back when the window closes. A second way of starting a process would be a
    second way of getting stuck in one.
    """
    _checked(system_id)
    launcher = pergame.settings_launcher(system_id)
    if launcher is None:
        raise HTTPException(404, "This emulator has no settings window to open")
    # The screen slot, not the resident count: this opens a window through
    # process_manager exactly like a game, so it is refused for the same reason
    # a launch is — something else is already in front of the player.
    if process_manager.is_foreground:
        raise HTTPException(409, "A game is already running")

    exec_path, exec_args = launcher
    try:
        exec_args = catalog_launch.resolve_args(system_id, exec_args)
    except LookupError as e:
        # The emulator is not installed. Said with the pack's own words rather
        # than as flatpak's error about an app id nobody typed.
        raise HTTPException(424, str(e)) from e

    # No ROM is passed. The emulator opens on its library, which is where its
    # per-title settings live — handing it a path would start the game instead,
    # which is the one thing the player did NOT ask for from this button.
    try:
        await process_manager.launch(
            exec_path=exec_path, exec_args=exec_args,
            game_key=f"{system_id}:settings", system_id=system_id)
    except SessionConflict as e:
        # The resident cap. Same door as /games/launch: unhandled this is a 500,
        # and a 500 tells the player nothing about the suspended sessions that
        # are actually in the way.
        raise HTTPException(409, str(e)) from e
    start_window_task({}, system_id)
    return {"ok": True, "opened": Path(body.rom).name or None}
