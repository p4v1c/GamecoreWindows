"""GameCore — FastAPI backend."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from .config import DEBUG
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.WARNING)

# Standby and the controller monitor speak at INFO whatever the rest does, and
# that exception is deliberate. Both fail SILENTLY by nature: a screen that
# does not come back produces no error, and a controller nobody is reading
# looks exactly like a controller nobody is touching. Their log.info lines —
# "watcher started", "watching /dev/input/event14", "active → sleep" — are the
# only trace either leaves, and at WARNING they were all dropped. Everything
# noisy in these two modules (a key code per press, a scan pass) is at DEBUG
# and stays there.
#
# desktop_power belongs here for the same reason and was left out of the first
# pass, which cost a diagnosis immediately: it hands the screen timeout between
# GameCore and the desktop, and every step it takes — claimed, restored, given
# back — was logged at INFO and therefore invisible. Only its failure warning
# got through, so the box could say "I could not take it" but never "I gave it
# back", and there was no way to tell a handover that happened from one that
# never ran.
for _observable in ("backend.services.standby", "backend.services.gamepad_monitor",
                    "backend.services.desktop_power", "backend.services.rest_mode",
                    "backend.services.resume", "backend.services.bt_wake"):
    logging.getLogger(_observable).setLevel(logging.INFO)

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from . import ws
from .routers import systems, games, playtime, covers, media, metadata, sysinfo, update, addons, catalog
from .routers import auth as auth_routes
from .routers import bios as bios_router
from .routers import pergame as pergame_router
from .routers import ready as ready_router
from .routers import standby as standby_router
from .routers import controllers as controllers_router
from .routers import themes as themes_router
from .routers import storage as storage_router
from .routers import logs as logs_router
from .routers import profiles as profiles_router
from .routers import power as power_router
from .routers.settings import wifi, audio, bluetooth, display
from .services import (battery, boot, bt_wake, desktop_power, gamepad_monitor, http_cache,
                       logs, playtime_repair, prefetch, resume, standby,
                       standby_videos, storage_monitor)
from .services.errors import ServiceError
from .services.process_manager import process_manager
from .config import BACKEND_PORT
from .services.paths import backend_data_dir, covers_dir, frontend_dist_dir, themes_dir
from .services.platform.media_types import pin_media_types

log = logging.getLogger(__name__)

# Before any response is built: the registry's types differ from PC to PC.
pin_media_types()

async def _repair_playtime() -> None:
    """Carry playtime across a change in what the library lists.

    A game's playtime is keyed by the filename the library listed, so a change
    in what gets listed silently orphans it. Hiding a .bin behind its .cue does
    exactly that — the hours are still in the database and nothing points at
    them any more. Idempotent: after the first pass no row matches a hidden
    name any more.

    Off the startup path, and this is the one step whose cost belongs to the
    player's own machine rather than to GameCore: it walks every system's ROM
    directory, so a shelf of two thousand games pays for two thousand, on
    whatever disk that box has. It used to do that BEFORE the API answered
    anything at all, which turned "how big is your library" into "how long is
    your boot".

    What that costs, stated: for the moment it is running, a library screen can
    show the playtime a game had before its row was moved. The figures are on
    disk either way and nothing on screen is untrue — so when rows do move, say
    so, and the front end re-reads them.
    """
    try:
        moved = await playtime_repair.rekey_shadowed_entries()
        if moved:
            log.info("playtime: %d entr%s re-keyed onto their disc descriptor",
                     moved, "y" if moved == 1 else "ies")
            await ws.broadcast("playtime:rekeyed", {"moved": moved})
        boot.done("playtime_repair")
    except Exception:
        log.exception("lifespan: playtime repair failed")
        boot.failed("playtime_repair")


async def _settle_the_screen() -> None:
    """The screen, and who owns its timeout. Both off the startup path.

    Standby state lives in memory but its effect does not — `xset dpms force
    off` belongs to the X server, which SDDM owns and which does not restart
    with us. A box that went to sleep and then had its backend restarted came
    back believing it was awake with the TV still dark, and nothing could wake
    it: pad events arrive over evdev, not X, so DPMS never re-armed on its own.
    Unconditional, so restarting the backend — what anyone stuck like that will
    try — actually fixes it.

    It talks to the X server, which at cold boot is the one thing that may not
    exist yet: the backend's own unit waits up to twenty seconds for a display
    that answers. Awaiting it before serving made a screen that was slow to
    appear into an API that was slow to answer, for a call whose entire effect
    is on a screen nobody is looking at yet.
    """
    try:
        await standby.resume_after_restart()
        boot.done("screen")
    except Exception:
        log.exception("lifespan: could not force the screen back on")
        boot.failed("screen")

    # One owner for the screen timeout. The desktop's own power manager was
    # running a second, invisible timer that capped whatever the settings page
    # said — "Never" meant fifteen minutes on the reference box. Claimed only
    # while GameCore's standby is on; handed straight back otherwise, because
    # the two disarmed together is a television that never goes dark behind a
    # switch that promises the opposite.
    try:
        if standby.load_config()["enabled"]:
            await desktop_power.claim()
        else:
            await desktop_power.release()
        boot.done("power")
    except Exception:
        log.exception("lifespan: could not settle who owns the screen timeout")
        boot.failed("power")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Everything that must be true before the first request, and nothing else.

    Two steps are required, and the test for both is the same: would answering
    without it make the front end tell the player something false?

      · the database — every library screen reads it;
      · the running-game adoption — serving before it means answering "nothing
        is running" while an emulator is on screen, and handing the player a
        home they can navigate over a live game with the pad driving both.

    Everything else was here because this is where startup code accumulates,
    not because anything depended on it. It now runs beside the server rather
    than in front of it, and `/api/ready` reports each step by name — see
    services/boot.py for the rule and for why each one is on the side it is.
    """
    boot.begin()
    # Here, not at import: importing the app (tests, tools) must not write logs.
    logs.install()

    await init_db()
    boot.done("database")

    # Re-attach to a game a previous process left running, so the double-PS
    # shortcut can still close it.
    try:
        await process_manager.adopt_orphan()
    except Exception:
        log.exception("lifespan: could not adopt the previous session")
    # Marked done even when it raised: what this protects against is answering
    # BEFORE the question has been asked. It has been asked now, and a failure
    # to adopt is a box with no game running as far as anything can tell —
    # which is exactly what the API will say. Blocking the boot on it would
    # trade a rare wrong answer for a certain black screen.
    boot.done("session")

    tasks = [
        asyncio.create_task(_repair_playtime()),
        asyncio.create_task(_settle_the_screen()),
        asyncio.create_task(gamepad_monitor.run()),
        asyncio.create_task(battery.run()),
        asyncio.create_task(standby.run()),
        asyncio.create_task(prefetch.run()),
        asyncio.create_task(standby_videos.run()),
        asyncio.create_task(storage_monitor.run()),
        asyncio.create_task(logs.run()),
        asyncio.create_task(resume.run()),
        asyncio.create_task(bt_wake.run()),
    ]
    yield
    # Handed back on the way out: a backend that stops and does not come back
    # would otherwise leave the desktop's own screen-off disabled for good.
    try:
        await desktop_power.release()
    except Exception:
        log.exception("lifespan: could not hand the screen timeout back")
    for t in tasks:
        t.cancel()
    # Actually wait for them: cancel() only schedules the cancellation, so
    # shutdown used to return with the tasks still mid-await.
    await asyncio.gather(*tasks, return_exceptions=True)
    # The running game is deliberately left alone — see
    # process_manager._save_state(). Killing it here would take the player's
    # unsaved progress with it on any OTA or restart, and would do nothing at
    # all for the case that actually strands them: a crash, where this code
    # never runs. The job recorded on disk covers both.


app = FastAPI(title="GameCore", version="1.0.0", lifespan=lifespan)


@app.exception_handler(ServiceError)
async def _service_error(_request: Request, exc: ServiceError):
    """A service's refusal, as the HTTPException it replaces: `{"detail": ...}`."""
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


# ── Cross-origin guard ───────────────────────────────────────────────────────
# The core is unauthenticated on loopback, and the box runs browsers that can
# reach it — the Firefox kiosk profiles arch.sh installs, and Stremio. A page in
# one of those could carry
#     <form action="http://127.0.0.1:8765/api/games/kill" method="post">
# and auto-submit it: the running game dies, the save with it, from an ad on an
# unrelated site. Only endpoints taking no Pydantic body are reachable that way
# — a form can send urlencoded, multipart or text/plain and FastAPI answers 422
# to anything else — which still leaves games/kill, update/apply,
# addons/{name}/install and standby/exit.
#
# There was no middleware at all here: no CORS, no TrustedHost, no origin check.

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _hostport(netloc: str, default_port: int) -> tuple[str, int]:
    """(host, port) from a netloc — lowercased, IPv6 brackets stripped."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):                      # [::1]:8765
        host, _, rest = netloc.partition("]")
        host, port = host[1:], rest.lstrip(":")
    else:
        host, _, port = netloc.partition(":")
    try:
        return host, int(port) if port else default_port
    except ValueError:
        return host, default_port


def _origin_ok(headers) -> bool:
    """False for a write driven by a page we are not serving."""
    if headers.get("sec-fetch-site") == "cross-site":
        return False

    origin = headers.get("origin")
    if not origin:
        # curl, the addon CLI, the install scripts. A browser always attaches
        # Origin to a cross-origin write, so nothing we are defending against
        # arrives without one.
        return True

    scheme, _, rest = origin.partition("://")
    if not rest:
        return False
    o_host, o_port = _hostport(rest, 443 if scheme == "https" else 80)
    h_host, h_port = _hostport(headers.get("host", ""), o_port)

    # Same-origin. This is the branch that keeps LAN login working: /login and
    # /api/auth/* are proxied by Caddy from https://<whatever address the client
    # used>:8443, the box has no fixed name, and Caddy passes the client's Host
    # through — so comparing the two is the check, with no hardcoded host.
    if (o_host, o_port) == (h_host, h_port):
        return True

    # The TV. Electron loads http://localhost:<BACKEND_PORT> while the socket
    # may report 127.0.0.1; both spellings mean this machine. Deliberately not a
    # blanket loopback pass — another local app on a different port is not the UI.
    return o_host in _LOOPBACK and h_host in _LOOPBACK and o_port in (h_port, BACKEND_PORT)


@app.middleware("http")
async def cross_origin_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request.headers):
        return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    return await call_next(request)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(systems.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(playtime.router, prefix="/api")
app.include_router(covers.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(metadata.router, prefix="/api")
app.include_router(sysinfo.router, prefix="/api")
# Asked in a loop by the shell while the box starts — see routers/ready.py.
app.include_router(ready_router.router, prefix="/api")
app.include_router(update.router, prefix="/api")
app.include_router(pergame_router.router, prefix="/api")
app.include_router(addons.router, prefix="/api")
# The pack catalogue: what this box could run, and installing it without
# re-running the installer. Same busy-lock and WebSocket shape as addons.
app.include_router(catalog.router, prefix="/api")
app.include_router(bios_router.router, prefix="/api")
app.include_router(standby_router.router, prefix="/api")
app.include_router(controllers_router.router, prefix="/api")
app.include_router(storage_router.router, prefix="/api")
app.include_router(logs_router.router, prefix="/api")
app.include_router(profiles_router.router, prefix="/api")
app.include_router(power_router.router, prefix="/api")
app.include_router(themes_router.router, prefix="/api")
app.include_router(wifi.router, prefix="/api")
app.include_router(audio.router, prefix="/api")
app.include_router(display.router, prefix="/api")
app.include_router(bluetooth.router, prefix="/api")
app.include_router(auth_routes.router, prefix="/api")

# ── Web managers ─────────────────────────────────────────────────────────────
# The ROM manager moved to the rom-manager addon (port 8770) —
# see https://github.com/p4v1c/gamecore-addons

@app.get("/gc/addons", include_in_schema=False)
def gc_addons():
    # Same payload as /api/addons, on a path Caddy proxies to the LAN without
    # auth: the shared nav bar of the addon UIs needs it before login state
    # is known (see docs/SECURITY.md).
    return addons.list_installed()


@app.get("/login", include_in_schema=False)
def login_page():
    # Self-contained login form for LAN clients, proxied without auth by
    # Caddy. The TV never sees it (loopback bypasses Caddy entirely).
    return FileResponse(
        str(Path(__file__).parent / "templates" / "login.html"),
        media_type="text/html",
    )


# ── Static files (covers served directly) ────────────────────────────────────
# Create the directories before mounting: a conditional mount decided at
# import time would leave /covers dead until a restart on a fresh checkout
# (the covers dir used to be created later, in the lifespan).
# Logos are NOT a static mount any more: they live in catalog/<id>/logo.png
# and only assets/logos/ holds the operator's own replacements. The route in
# routers/systems.py serves both, override first — see public_router there.
app.include_router(systems.public_router)

class _RevalidatedStatic(StaticFiles):
    """Static content the browser keeps but must ask about before reusing.

    Covers and the data directory are files whose URL stays put while their
    bytes may be replaced, a re-scraped jacket for one. `no-cache` keeps them on disk and lets the ETag answer 304, so a
    second boot transfers nothing and a replacement is still seen at once.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = http_cache.REVALIDATE
        return resp


for _dir, _route, _name in (
    (covers_dir(), "/covers", "covers"),
    (backend_data_dir(), "/data", "data"),
):
    _dir.mkdir(parents=True, exist_ok=True)
    app.mount(_route, _RevalidatedStatic(directory=str(_dir)), name=_name)


class _NoCacheStatic(StaticFiles):
    """Static files that must never be served from the browser cache.

    A theme is a folder of ES modules the browser imports directly. The loader
    can bust the entry point's URL, but the entry's own relative imports
    (home.js, settings.js, ...) and its stylesheet resolve without that query,
    so the browser pins the first version it ever saw. Editing a theme then
    changes nothing on screen, and a fix shipped by update stays invisible.
    These files are small and local; not caching them costs nothing.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp


# Theme modules and their assets — imported by the browser from here.
_themes_dir = themes_dir()
_themes_dir.mkdir(parents=True, exist_ok=True)
app.mount("/themes", _NoCacheStatic(directory=str(_themes_dir)), name="themes")

# ── WebSocket (must be registered before the catch-all static mount) ──────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # A WebSocket handshake is a GET and is not subject to CORS, so any page in
    # any browser on this box could open ws://127.0.0.1:8765/ws and read every
    # event the UI sees — what launched, what is installed, controller activity.
    # The HTTP middleware never sees this route; check the same rule here.
    if not _origin_ok(websocket.headers):
        await websocket.close(code=1008)   # policy violation
        return
    await ws.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws.disconnect(websocket)


# ── Serve built frontend (production) ────────────────────────────────────────
class _BundleStatic(StaticFiles):
    """The built frontend, cached by how it is named — see services/http_cache.

    `index.html` is never stored; a hash-named asset is stored for a year. That
    split is what lets electron/main.js stop clearing the whole HTTP cache on
    every start: the only file that could pin an old bundle is the one file the
    browser is now forbidden to keep.
    """

    def file_response(self, full_path, *args, **kwargs):
        resp = super().file_response(full_path, *args, **kwargs)
        # The path RELATIVE to the bundle root, because that is what the rule
        # reads: `assets/index-Dr_k0yci.js` is a build artefact, and no file
        # name on its own can prove that — plenty of covers end in a hyphen and
        # eight word characters. See services/http_cache.is_hashed_asset.
        try:
            rel = str(Path(full_path).relative_to(self.directory))
        except ValueError:
            rel = Path(full_path).name
        resp.headers["Cache-Control"] = http_cache.for_frontend(rel)
        return resp


frontend_dist = frontend_dist_dir()
if frontend_dist.exists():
    app.mount("/", _BundleStatic(directory=str(frontend_dist), html=True), name="frontend")
