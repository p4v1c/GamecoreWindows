r"""Dev-only: serve the app WITHOUT its lifespan, with the boot steps marked
done so the UI leaves its splash, and READ-ONLY.

Read-only because this is the real backend on the real box: a ✕ press during
an audit once launched Steam. Every write is refused (403) except switching
the theme, which the audit needs. Never run the real lifespan in dev either:
it changes the power plan, the display mode and the screen timers of the
machine it runs on.

    $env:GAMECORE_PATH = $PWD; $env:GAMECORE_DATA = "$env:TEMP\gcdata"
    $env:PYTHONPATH = $PWD
    .venv\Scripts\python .claude/skills/gamecore-legibility/scripts/devserve.py

GAMECORE_DATA needs config/systems.json and config/apps.json (copy them from
install/generated/*.dist), config/themes/ (copy from the repo), and ROMs as
empty files under emu/<system>/ for a library with games.
"""
import uvicorn
from backend import main
from backend.db import init_db
from backend.services import boot

ALLOWED_WRITES = {"/api/themes/active"}
_db_ready = False


async def read_only(scope, receive, send):
    global _db_ready
    if not _db_ready:       # the lifespan creates the playtime tables; it is off
        await init_db()
        _db_ready = True
    if (scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and scope["path"] not in ALLOWED_WRITES):
        print(f"devserve: refused {scope['method']} {scope['path']}", flush=True)
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"devserve is read-only"})
        return
    await main.app(scope, receive, send)


for step in getattr(boot, "REQUIRED", []):
    boot.done(step)
uvicorn.run(read_only, host="127.0.0.1", port=8766, lifespan="off")
