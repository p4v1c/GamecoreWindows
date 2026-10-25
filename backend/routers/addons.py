"""Addon registry + lifecycle.

The core stays minimal: it reads the registry written by the `gamecore-addon`
CLI (config/addons.json — excluded from OTA rsync, so it survives updates),
and drives that same CLI for one-click install/remove from the Addons screen.
It knows nothing about addon contents.
"""
import asyncio
import json
import logging
import re
import sys
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.paths import config_dir, install_bin_dir
from .. import ws

router = APIRouter(prefix="/addons", tags=["addons"])
log = logging.getLogger(__name__)

ADDONS_FILE = config_dir() / "addons.json"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CLI_TIMEOUT = 600.0
_busy_lock = asyncio.Lock()  # one CLI action at a time


def _cli() -> list[str]:
    """The CLI is a Python script: run it on the backend's own interpreter, from the installation."""
    return [sys.executable, str(install_bin_dir() / "gamecore-addon")]


def _registry() -> dict:
    try:
        return json.loads(ADDONS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


@router.get("")
def list_installed():
    """Installed addons, as a list — consumed by the shared nav of every addon UI."""
    return [{"name": name, **entry} for name, entry in sorted(_registry().items())]


@router.get("/available")
async def list_available():
    """Available + installed state, from `gamecore-addon list --json` (may clone the repo)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *_cli(), "list", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except (FileNotFoundError, asyncio.TimeoutError) as e:
        raise HTTPException(503, f"gamecore-addon CLI unavailable: {e}")
    if proc.returncode != 0:
        raise HTTPException(503, f"gamecore-addon list failed: {err.decode().strip()}")
    return json.loads(out.decode())


class NotifyBody(BaseModel):
    event: str
    data: dict = {}


@router.post("/notify")
async def notify(body: NotifyBody):
    """Generic hook for addons to reach the TV UI over the core WebSocket
    (e.g. the ROM Manager addon broadcasts rom_uploaded after an upload)."""
    if not body.event or len(body.event) > 64:
        raise HTTPException(400, "invalid event name")
    log.info("addons: notify %r", body.event)
    await ws.broadcast(body.event, body.data)
    return {"ok": True}


async def _run_cli(action: str, name: str) -> None:
    async with _busy_lock:
        proc = await asyncio.create_subprocess_exec(
            *_cli(), action, name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def _pump() -> None:
            # The whole read loop must sit under the timeout: a script that
            # blocks (interactive prompt, hung pip) never closes stdout, and
            # a timeout on proc.wait() alone would never fire — leaving
            # _busy_lock held forever.
            if proc.stdout:
                async for line in proc.stdout:
                    text = line.decode(errors="replace").rstrip()
                    log.info("addons: %s %s: %r", action, name, text)
                    await ws.broadcast("addon:log", {"line": text})
            await proc.wait()

        try:
            await asyncio.wait_for(_pump(), timeout=_CLI_TIMEOUT)
            code = proc.returncode or 0
        except asyncio.TimeoutError:
            log.warning("gamecore-addon %s %s timed out — killing", action, name)
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            await ws.broadcast("addon:log", {"line": f"[ERROR] {action} timed out."})
            code = -1
        await ws.broadcast("addon:done", {"action": action, "name": name, "success": code == 0})


# The task handle, not the lock, is the busy check: two requests arriving in
# the same loop tick both saw the lock unlocked (the task hadn't started yet)
# and the second silently queued instead of getting its 409. Checking and
# assigning _current is atomic — no await in between.
_current: asyncio.Task | None = None


def _start(action: str, name: str) -> dict:
    global _current
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(400, "invalid addon name")
    if _current is not None and not _current.done():
        raise HTTPException(409, "another addon operation is running")
    _current = asyncio.create_task(_run_cli(action, name))
    _current.add_done_callback(lambda t: t.cancelled() or (t.exception() and log.warning("addon task failed: %s", t.exception())))
    return {"ok": True, "message": f"{action} {name} started"}


@router.post("/{name}/install")
async def install_addon(name: str):
    return _start("install", name)


@router.post("/{name}/update")
async def update_addon(name: str):
    return _start("update", name)


@router.delete("/{name}")
async def remove_addon(name: str):
    return _start("remove", name)
