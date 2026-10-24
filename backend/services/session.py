"""The interactive desktop games appear on, as seen from the backend.

A window opens on the desktop of the session its process runs in, and only
the active console session is on the TV (`platform/console_session.py`). The
backend runs inside the console user's session (logon task, dev-log D8), so a
game it starts inherits that desktop: no `DISPLAY` or `XAUTHORITY` to rebuild,
as the Linux service had to. `launch_blocker()` says when that is not true
(the backend started as a service in session 0, a Remote Desktop session on
the console), so a launch is refused instead of opening where nobody sees it.
"""
import logging
import os

from .platform import console_session

log = logging.getLogger(__name__)


# Community-maintained button/axis mappings (github.com/mdqinc/SDL_GameControllerDB).
# SDL (2.0.10+ and SDL3 alike) loads the file named by the
# SDL_GAMECONTROLLERCONFIG_FILE hint/env var at init and merges it into its
# built-in database, so any emulator linked against SDL correctly maps a
# controller it doesn't otherwise recognize — no per-emulator manual
# configuration needed. (An earlier revision exported SDL_GAMECONTROLLERDB,
# which is not a variable SDL has ever read — the DB was silently ignored.)
#
# The path now comes from mapping_db.served(), not from the vendored file
# directly: what an emulator must read is the community database WITH the
# owner's own captures appended. Naming the vendored file here would have made
# the mapping wizard write to a database nothing loads.


def _controller_db():
    """Path for SDL_GAMECONTROLLERCONFIG_FILE, or None.

    Imported inside the call rather than at module scope: `configgen` pulls in
    the catalogue loader, and this module is imported from `main` before the
    app has decided anything. Never raises — a database that cannot be built
    must cost the captured mappings, not the launch.
    """
    try:
        from .configgen import mapping_db
        return mapping_db.served()
    except Exception:
        log.warning("session: no controller database to hand to SDL",
                    exc_info=True)
        return None


def desktop() -> dict:
    """Who and what is on the screen, for the launch log and diagnostics."""
    mine = console_session.own_session_id()
    console = console_session.console_session_id()
    return {
        "session": mine,
        "console_session": console,
        "user": console_session.session_user(console)
        if console != console_session.NO_CONSOLE_SESSION else "",
        "display": console_session.primary_display(),
    }


def launch_blocker() -> str:
    """Why a game started now would not appear on the TV, or "" when it would.

    Only the session is checked, not the display: a TV that is off still
    lists its output, and a launch from the phone wakes the screen itself.
    """
    state = desktop()
    if state["console_session"] == console_session.NO_CONSOLE_SESSION or not state["user"]:
        return "Nobody is logged on to the TV. Sign in on the PC, then try again."
    if state["session"] != state["console_session"]:
        return ("GameCore is not running on the TV's desktop, so the game would "
                "open where nobody can see it. Restart GameCore from the TV.")
    return ""


async def display_env() -> dict:
    """The environment a game or app is started with: the backend's own (the
    console user's, desktop included), plus the controller database for SDL."""
    env = os.environ.copy()
    if not env.get("SDL_GAMECONTROLLERCONFIG_FILE"):
        db = _controller_db()
        if db:
            env["SDL_GAMECONTROLLERCONFIG_FILE"] = str(db)
    return env
