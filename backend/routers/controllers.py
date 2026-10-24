"""Controllers — peripherals, the autoconfig switch, and the mapping wizard.

**The wizard** (`/controllers/mapping/*`) is for a pad SDL does not know. There
is no hand-made config to fall back on, because the owner cannot make one — the
emulator's input UI will not bind a device its SDL never enumerated. So the pad
is mapped HERE, once, button by button, and the result is written as an SDL
mapping line that every SDL-based emulator on the box reads at startup. One
gesture, thirteen systems.

There used to be a second mechanism next to it, "Scan mapping" and its inverse
"Forget mapping" (`POST`/`DELETE /controllers/scan-mapping`): copy the connected
pad's hand-made config out of the GUID-bound emulators into a snapshot. Both
were removed. The snapshots already on disk are NOT touched and are still
restored on connect by the autoconfig pipeline (`configgen/snapshots.py`,
`restore()`), so nobody loses a mapping they saved — they only lose the button
that created new ones.

Ryujinx never needed a snapshot at all: its GUID is read live from SDL2 and
converted exactly (controller_profiles.ryu_guid_from_sdl2), and its bindings are
role names that carry from one controller to the next.

The wizard also reaches the GUID-bound emulators, in `configgen/derive.py`: a
pad the wizard has just described is a pad whose raw indices are finally known,
which is what lets those emulators be written from a capture instead of
demanding a manual pass.
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..services import (
    controller_autoconfig,
    controller_capture,
    controller_profiles,
    controller_roster,
    gamepad_monitor,
    usb_devices,
)
from ..services.catalog import load_catalog
from ..services.configgen import mapping_db

router = APIRouter(tags=["controllers"])


@router.get("/controllers/devices")
def usb_peripherals():
    """The declared peripherals that are not SDL pads, present or absent.

    The player slots above this list answer "who is player 2". They cannot
    answer "is the GameCube adapter plugged in", because an adapter Dolphin
    drives over raw libusb is not a joystick SDL opens and therefore never
    enters the roster at all. Without this list the two failure modes — not plugged in,
    and plugged in but not seen — look identical from a sofa, and only one of
    them is fixed by touching the cable.
    """
    return {"ok": True, "devices": usb_devices.report()}


# ── the autoconfig switch ────────────────────────────────────────────────────
#
# Two writes, and BOTH of them destroy work somebody did. Turning it off empties
# the slots GameCore filled; turning it back on overwrites whatever the owner
# set up while it was off. The UI warns before each — see
# `frontend/src/settings/controllers.js` — and the endpoints below are shaped so
# that it can: each one answers with what it actually did, by name, so the
# screen reports a fact rather than an assumption.

def _autoconfig_view() -> dict:
    """The switch, plus every emulator that profiles pads and where it stands.

    `effective` is not `enabled` and the difference is the whole point: with the
    global switch off, every emulator is off whatever its own row says. A screen
    that showed only `enabled` would present rows reading "on" for emulators
    that are not running, which is the one thing this feature must not do.

    `releasable` is the second thing the screen cannot work out for itself: four
    emulators have no inverse, so switching them off empties nothing and the
    confirmation must not promise it does. See `configgen.can_release`.
    """
    st = controller_autoconfig.state()
    packs = []
    catalog = load_catalog()
    for pack in controller_profiles.profilable_packs(catalog):
        own = controller_autoconfig.own_switch(
            st, pack.id, controller_profiles.superseded_ids(catalog, pack.id))
        packs.append({
            "id": pack.id,
            "label": pack.data.get("label") or pack.id,
            "enabled": own,
            "effective": st["enabled"] and own,
            "releasable": controller_profiles.can_release(pack),
        })
    return {"ok": True, "enabled": st["enabled"], "packs": packs}


@router.get("/controllers/pads")
def connected_pads():
    """The connected pads for the controller screen: name, slot, battery,
    whether SDL3 can name them and which standard controls they have."""
    return {"pads": controller_roster.connected_pads()}


@router.get("/controllers/autoconfig")
def autoconfig_state():
    return _autoconfig_view()


class AutoconfigBody(BaseModel):
    enabled: bool
    # Absent means the global switch. A pack id means one emulator's exception,
    # which is deliberately the same endpoint: the two settings compose, and
    # splitting them into two routes would let a caller change one without the
    # other ever recomputing what is effectively in force.
    pack: str | None = None


@router.post("/controllers/autoconfig")
def set_autoconfig(body: AutoconfigBody):
    """Flip the switch, and make the box match it straight away.

    The clean-up and the ordering that makes it honest live in
    `configgen.set_autoconfig` — with the pipeline, where the characterisation
    suite can drive them. What is here is the HTTP shape: reject an unknown
    emulator before anything is written, and answer with what actually
    happened so the screen can report a fact rather than an assumption.
    """
    if body.pack is not None:
        known = {p["id"] for p in _autoconfig_view()["packs"]}
        if body.pack not in known:
            return {"ok": False, "error": f"no such emulator: {body.pack}"}

    released = controller_profiles.set_autoconfig(body.enabled, body.pack)
    if body.enabled:
        gamepad_monitor.request_reprofile()

    return {**_autoconfig_view(), "released": released,
            "reprofiling": body.enabled}


# ── the wizard ───────────────────────────────────────────────────────────────

class CommitBody(BaseModel):
    """What the wizard collected.

    `bindings` is SDL field name → SDL token (`a` → `b0`, `lefttrigger` →
    `+a4`). The BACKEND does not track which step the UI is on: skipping a
    button, going back and re-recording one are things the player does with the
    pad in their hands, and a session that had to stay in step with them would
    turn every dropped WebSocket frame into a wrong binding. The UI sends what
    it ended up with.
    """
    bindings: dict[str, str] = Field(default_factory=dict)
    name: str = ""


@router.post("/controllers/mapping/start")
def mapping_start():
    return controller_capture.start()


@router.post("/controllers/mapping/commit")
def mapping_commit(body: CommitBody):
    return controller_capture.commit(body.bindings, body.name)


@router.post("/controllers/mapping/cancel")
def mapping_cancel():
    return {"ok": True, "closed": controller_capture.cancel()}


@router.get("/controllers/mapping/saved")
def mapping_saved():
    """The mappings captured on this box, for the settings screen.

    Read straight from the user file rather than from a cache: it is the thing
    that survives an OTA, so it is also the only thing that can be trusted to
    describe what the box will still know tomorrow.
    """
    lines = mapping_db.read_user()
    live = set(mapping_db.servable(lines))
    saved = []
    for line in lines:
        parsed = mapping_db.parse(line)
        if parsed:
            guid, name, _bindings = parsed
            # Stored and served are not the same thing — see `mapping_db.
            # servable`. A capture for a pad SDL drives through HIDAPI is kept
            # here and not handed to SDL, and a screen that could not say so
            # would show the owner a mapping that is doing nothing.
            saved.append({"guid": guid, "name": name, "line": line,
                          "served": line in live})
    return {"ok": True, "saved": saved, "file": str(mapping_db.USER_DB)}


class ForgetBody(BaseModel):
    guid: str


@router.post("/controllers/mapping/forget")
def mapping_forget(body: ForgetBody):
    """Drop a captured mapping. A POST with a body rather than a DELETE with a
    path parameter, so the cross-origin guard in main.py covers it the same way
    it covers every other write that matters."""
    return {"ok": True, "forgotten": mapping_db.remove(body.guid)}


@router.websocket("/ws/controllers/mapping")
async def mapping_events(websocket: WebSocket):
    """Push every press on the pad being mapped, as an SDL token.

    The origin check is the same one `main.py` applies to `/ws`, repeated
    rather than shared because a WebSocket does not pass through the HTTP
    middleware — a page we do not serve must not be able to watch the owner's
    controller, which is a keylogger with extra steps.
    """
    from ..main import _origin_ok

    if not _origin_ok(websocket.headers):
        await websocket.close(code=1008)          # policy violation
        return

    session = controller_capture.current()
    if session is None:
        await websocket.accept()
        await websocket.send_json({"event": "error",
                                   "data": {"error": "no capture session is open"}})
        await websocket.close()
        return

    await websocket.accept()
    await websocket.send_json({"event": "ready",
                               "data": {"session": session.id,
                                        "nodes": session.nodes}})
    try:
        async for event in controller_capture.events(session):
            await websocket.send_json({"event": "input", "data": event})
        # The stream ended by itself: the pad is gone, or the
        # session ran out of time. Say so instead of closing in silence — the
        # wizard is a screen waiting for a press that can no longer come, and
        # the only other way out of it was the pad that just left.
        await websocket.send_json({"event": "ended", "data": {"reason": "no input left"}})
    except WebSocketDisconnect:
        pass
    except Exception:
        # Never propagate: a pad unplugged mid-wizard ends its stream, and the
        # player must get a socket that ends rather than a backend traceback.
        try:
            await websocket.send_json({"event": "ended", "data": {}})
        except Exception:
            pass
