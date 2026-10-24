"""OTA update via GitHub Releases."""
import asyncio
import logging
import re
import sys
from fastapi import APIRouter, HTTPException
import httpx

from ..config import APP_VERSION, GITHUB_REPO, UPDATE_ASSET, GAMECORE_ROOT
from ..services.process_manager import kill_process_group
from .. import ws

router = APIRouter(prefix="/update", tags=["update"])
log = logging.getLogger(__name__)

_GH_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# 10 min hard cap on stage 1 (download, check, unpack); stage 2 runs detached.
_UPDATE_TIMEOUT = 600.0


def _version_int(tag: str) -> int:
    """Tolerant x.y.z ordering — 'v2.1.0-rc1' or a malformed tag must never
    raise (this runs on the GitHub response, outside our control)."""
    nums = re.findall(r"\d+", tag)[:3]
    return sum(int(n) * (10000 ** (2 - i)) for i, n in enumerate(nums))


@router.get("/check")
async def check_update():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(_GH_API, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise HTTPException(503, f"GitHub unreachable: {e}")

    remote_tag = data.get("tag_name", "")
    download_url = ""
    for asset in data.get("assets", []):
        if asset["name"] == UPDATE_ASSET:
            download_url = asset["browser_download_url"]
            break

    if _version_int(remote_tag) > _version_int(APP_VERSION):
        return {
            "update_available": True,
            "current": APP_VERSION,
            "latest": remote_tag,
            "download_url": download_url,
        }
    return {"update_available": False, "current": APP_VERSION, "latest": remote_tag}


# The task handle is the busy check, same as routers/addons.py: testing and
# assigning it with no await in between is what makes it atomic.
#
# The Linux updater worked in a fixed directory it wiped on entry. Nothing
# stopped a second run: the UI's `installing` flag is local to UpdatePage, so
# navigating away and back re-enabled the button, and the second run wiped the
# first one's files mid-copy. update/windows.py also takes a lock.
_current: asyncio.Task | None = None


@router.get("/status")
def update_status():
    """Whether an update is running right now — the backend is the source of truth.

    The UI cannot keep this in component state: it is lost the moment the user
    leaves the page, and an update outlives that by minutes.
    """
    return {"running": _current is not None and not _current.done()}


@router.post("/apply")
async def apply_update():
    """Run update/windows.py (stage 1) in background, stream progress via WebSocket.

    `update:done` success means the release is checked and unpacked: stage 2
    then stops GameCore, swaps the code root and restarts it.
    """
    global _current
    script = GAMECORE_ROOT / "update" / "windows.py"
    # The backend's own interpreter: the installation's venv.
    cmd = [sys.executable, str(script), "install"]

    if not script.exists():
        raise HTTPException(404, f"Update script not found: {script}")

    if _current is not None and not _current.done():
        raise HTTPException(409, "an update is already running")

    async def _run_update():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def _pump() -> None:
            # timeout must cover the read loop too — a hung script never
            # closes stdout, so a timeout on proc.wait() alone never fires
            if proc.stdout:
                async for line in proc.stdout:
                    text = line.decode(errors="replace").rstrip()
                    log.info("ota: %s", text)
                    await ws.broadcast("update:log", {"line": text})
            await proc.wait()

        try:
            await asyncio.wait_for(_pump(), timeout=_UPDATE_TIMEOUT)
            code = proc.returncode or 0
            log.info("ota: script exited with %d", code)
            await ws.broadcast("update:done", {"success": code == 0, "code": code})
        except asyncio.TimeoutError:
            log.warning("update script timed out after %ss — killing", _UPDATE_TIMEOUT)
            # The whole tree: killing the parent alone left its children
            # writing while the UI had already been told the update was aborted.
            await kill_process_group(proc)
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
            await ws.broadcast("update:log", {"line": f"[ERROR] Update timed out after {int(_UPDATE_TIMEOUT)}s — aborted."})
            await ws.broadcast("update:done", {"success": False, "code": -1})

    _current = asyncio.create_task(_run_update())

    def _log_err(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            log.warning("update task failed: %s", exc)
    _current.add_done_callback(_log_err)

    return {"ok": True, "message": "Update started"}
