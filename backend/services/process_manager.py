"""Manages the launched games and apps: start, suspend, resume, kill, watch.

Each session's process tree lives in a Windows Job Object
(`platform/jobs.py`): the job is what is suspended, resumed and killed, never
one process. `game_session.py` holds what describes a session.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import ws
from ..db import get_db
from . import logs, playtime_rows, process_identity, profile_saves, session
from .command_line import launch_argv
from .game_session import Session, from_disk
from .paths import config_dir
from .platform import jobs, processes

log = logging.getLogger(__name__)

# The running sessions' jobs, so a restarted backend can find them again.
# config/ survives an update, and this file is state rather than settings: it
# is removed as soon as the last session exits.
SESSION_FILE = config_dir() / "session.json"

#: How often a watcher looks whether its game's job is empty. Bounds how late
#: `game:finished` follows the game's last process.
WATCH_POLL_SECS = 0.5


async def kill_process_group(proc) -> None:
    """Kill a child started without a job and all it started (the update
    script: killing the shell alone left its copies writing)."""
    if proc is None or proc.returncode is not None:
        return
    processes.kill_tree(proc.pid)


class SessionConflict(RuntimeError):
    """A lifecycle request the box cannot honour now. Carries the sentence the
    player reads (a 409 at the router): only the manager knows what is in the way."""


#: RAM below which a third resident session is not offered: a frozen emulator
#: keeps its memory (RPCS3: gigabytes, and its VRAM).
_THIRD_SLOT_MIN_BYTES = 16 * 1024 ** 3


def _resident_cap() -> int:
    """How many launched things may be resident at once, suspended or not.

    Read from the installed memory, never from free memory: a cap that moved
    with what is running would refuse a launch that succeeded a minute ago.
    Two is the floor, and the answer when the memory cannot be read.
    """
    return 3 if processes.physical_memory() >= _THIRD_SLOT_MIN_BYTES else 2


#: Read once: the cap must be the same for the whole run, and every test that
#: fills the box to it monkeypatches this name.
MAX_SESSIONS = _resident_cap()


class ProcessManager:
    """A few slots, one screen, and at most one resident game.

    At most `MAX_SESSIONS` launched things, one of them in front. Apps may
    coexist with a game; a second game may not (enforced on launch, never on
    suspend, so the player is never trapped in a game).
    """

    def __init__(self):
        #: Every resident session, in launch order. At most one is "foreground".
        self._sessions: list[Session] = []
        self._launching: bool = False  # claimed before the first await in launch()
        # One suspend or resume at a time: each moves windows (awaits) before
        # it freezes, and two at once would freeze one game twice.
        self._switching = asyncio.Lock()
        # Which run is current. Every launch and every adoption takes the next
        # number; a watcher keeps the one it was started with.
        self._seq: int = 0
        #: Named while a launch is in flight, so `current_game` is not None
        #: during the window `_launching` covers. Cleared by launch()'s finally.
        self._pending_key: str = ""
        self._pending_system: str = ""

    # ── what the box is doing ────────────────────────────────────────────────

    def _reap(self) -> bool:
        """Drop slots whose process tree has gone; say whether anything changed.

        Not only adopted ones: a watcher runs late, and a dead slot would
        refuse the next launch. A watcher keeps its `Session` and its playtime.
        """
        keep = [s for s in self._sessions if s.alive()]
        if len(keep) == len(self._sessions):
            return False
        # An adopted session has no watcher: its finish is announced here, or
        # every connected client keeps a session bar for a game that is gone.
        departed = [s for s in self._sessions if s not in keep and s.adopted]
        for s in departed:
            profile_saves.release_session(s)
            s.job.close()
        self._sessions = keep
        self._save_state()
        self._publish()
        for s in departed:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # A synchronous caller with no loop: the reconnect snapshot is
                # already correct, which is the half that survives a reload.
                break
            loop.create_task(self._announce("game:finished", s))
        return True

    @property
    def is_running(self) -> bool:
        """A session EXISTS, on the screen or frozen behind it. "May I start
        something" is `is_foreground`."""
        self._reap()
        return self._launching or any(s.alive() for s in self._sessions)

    @property
    def is_foreground(self) -> bool:
        """Something owns the screen: what gates a launch, holds the standby
        clock and defers the cover prefetcher."""
        self._reap()
        return self._launching or self._fg is not None

    @property
    def _fg(self) -> Session | None:
        """The one session on the screen. Not reaped: callers that need
        liveness go through the public properties below."""
        return next((s for s in self._sessions
                     if s.state == "foreground" and s.alive()), None)

    @property
    def foreground_session(self) -> Session | None:
        self._reap()
        return self._fg

    @property
    def background_sessions(self) -> list[Session]:
        """Every suspended session, oldest first."""
        self._reap()
        return [s for s in self._sessions if s.state == "background" and s.alive()]

    @property
    def background_session(self) -> Session | None:
        """The suspended session a bare "resume" means: the most recent one."""
        held = self.background_sessions
        return held[-1] if held else None

    @property
    def current_game(self) -> dict | None:
        """The session on the screen, or None (a suspended one answers None)."""
        s = self.foreground_session
        if s is None:
            return {"game_key": self._pending_key, "system_id": self._pending_system,
                    "rom_path": "", "session": self._seq} if self._launching else None
        return {"game_key": s.game_key, "system_id": s.system_id,
                "rom_path": s.rom_path, "session": s.session_id}

    def session_state(self) -> dict:
        """Everything resident: flat fields for the FOREGROUND session (what
        `/api/games/session` always returned), `background` for the rest."""
        state: dict = {}
        fg = self.foreground_session
        if fg is not None:
            state.update(fg.describe())
        held = self.background_sessions
        if held:
            state["background"] = [s.describe() for s in held]
        return state

    # ── the sessions on disk ─────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Remember the jobs so a restarted backend can still reach them:
        without it a restart left the game on screen and unkillable."""
        rows = [s.to_disk() for s in self._sessions if s.job is not None]
        try:
            if not rows:
                SESSION_FILE.unlink(missing_ok=True)
                return
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SESSION_FILE.with_name(SESSION_FILE.name + ".tmp")
            tmp.write_text(json.dumps({"sessions": rows}), encoding="utf-8")
            os.replace(tmp, SESSION_FILE)
        except OSError:
            log.warning("could not record the running session in %s", SESSION_FILE)

    def _clear_session(self) -> None:
        """Forget every session on disk."""
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    async def adopt_orphan(self) -> None:
        """Re-attach at startup to sessions a previous backend left behind.

        Only a job that opens again by its name AND whose first process has
        the boot id and start time on record: adopting a stranger's processes
        would let double-PS kill them.
        """
        try:
            rows = json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("sessions")
        except (OSError, ValueError, AttributeError):
            return
        adopted = [s for s in (self._adopt_row(row) for row in rows or []) if s]
        if not adopted:
            self._clear_session()
            return

        seen_foreground = False
        for s in adopted:
            if len(self._sessions) >= MAX_SESSIONS:
                # Kept, not dropped: the job is the only handle that can ever
                # close it. New launches stay refused, which is the cap's job.
                log.warning("recovering a session beyond the resident cap: %s "
                            "— launches stay refused until it is closed",
                            s.game_key or "?")
            if s.state == "background":
                # Still frozen: a suspension outlives the backend that made it.
                if not s.bg_since:
                    s.bg_since = time.time()
            elif seen_foreground:
                # Two foregrounds (a crash mid-swap): the first keeps the screen.
                # Frozen for real before being described as frozen, or the bar
                # would offer to resume a game still running behind the UI.
                if self._suspend(s):
                    s.state = "background"
                    s.bg_since = time.time()
                else:
                    log.error("could not suspend the recovered session %s — it "
                              "is left as it is rather than described wrongly",
                              s.game_key or "?")
            else:
                seen_foreground = True
            self._sessions.append(s)

        self._save_state()
        self._publish()
        for s in self._sessions:
            log.warning("adopted a session left by a previous backend: %s "
                        "(pid %d, %s)", s.game_key or "?", s.pid, s.state)

    def _adopt_row(self, row) -> Session | None:
        if not isinstance(row, dict):
            return None
        try:
            pid = int(row.get("pid") or 0)
        except (TypeError, ValueError):
            return None
        if not (pid and process_identity.matches(row, pid)):
            return None
        job = jobs.open_job(str(row.get("job") or ""), pid)
        if job is None:
            return None
        if not job.alive():
            job.close()
            return None
        self._seq += 1
        return from_disk(row, job, self._seq)

    # ── announcing ───────────────────────────────────────────────────────────

    def _publish(self) -> None:
        """Refresh what a newly connected websocket client is told."""
        ws.set_current_game(self.session_state() or None)

    async def _announce(self, event: str, session: Session) -> None:
        """One lifecycle event, carrying the WHOLE state after it: a swap moves
        two slots at once, and the transition alone would lose one."""
        payload = dict(session.describe())
        payload["state_snapshot"] = self.session_state()
        try:
            await ws.broadcast(event, payload)
        except Exception:
            log.exception("failed to broadcast %s", event)

    # ── launching ────────────────────────────────────────────────────────────

    def _refuse_launch(self, game_key: str, system_id: str) -> Session | None:
        """Raise SessionConflict if this launch may not happen; return the
        suspended game to resume instead, if it is the same one."""
        if self.is_foreground:
            raise SessionConflict("A game is already running")
        self._reap()
        if game_key != system_id:  # application tiles intentionally coexist
            games = [s for s in self.background_sessions if not s.is_app]
            same = next((s for s in games
                         if s.game_key == game_key and s.system_id == system_id), None)
            if same is not None:
                return same
            if games:
                title = games[0].game_key or "another game"
                raise SessionConflict(
                    f"Another game is still running ({title}). Close it before "
                    "starting a different game")
        if len(self._sessions) >= MAX_SESSIONS:
            held = ", ".join(s.game_key or "?" for s in self._sessions)
            raise SessionConflict(
                f"The box is already holding {len(self._sessions)} suspended "
                f"sessions ({held}) — close one before starting another")
        if blocker := session.launch_blocker():
            raise SessionConflict(blocker)
        return None

    async def launch(self, exec_path: str, exec_args: str, rom_path: str = "",
                     game_key: str = "", system_id: str = "") -> bool:
        """Start a new session, or resume the matching suspended game.

        Returns true when no process was started because the requested game
        was already resident. Repeats the router's gate at the last moment,
        closing the race between validation and spawning.
        """
        same = self._refuse_launch(game_key, system_id)
        if same is not None:
            await self.foreground(same.session_id)
            return True
        # Claimed synchronously: two concurrent launch() calls both pass the
        # checks above otherwise.
        self._launching = True
        self._pending_key = game_key
        self._pending_system = system_id
        try:
            cmd = launch_argv(exec_path, exec_args, rom_path)
            env = {**await session.display_env(), **logs.child_env(system_id)}
            log.info("launch: %s", " ".join(cmd))
            with logs.launch_output(system_id, game_key or rom_path or exec_path, cmd) as out:
                job = jobs.spawn(cmd, env=env, stdout=out)
        finally:
            self._launching = False
            self._pending_key = ""
            self._pending_system = ""

        self._seq += 1
        started = Session(
            job=job,
            game_key=game_key or Path(rom_path or exec_path).name,
            system_id=system_id,
            rom_path=rom_path,
            exec_path=exec_path,
            launch_args=cmd[1:],
            start_time=time.time(),
            session_id=self._seq,
        )
        self._sessions.append(started)
        self._save_state()
        self._publish()
        await ws.broadcast("game:started", {
            "game_key": started.game_key,
            "system_id": started.system_id,
            "session": started.session_id,
        })

        asyncio.create_task(self._watch(started)).add_done_callback(_log_watch_error)
        return False

    # ── suspend and resume ───────────────────────────────────────────────────

    def _suspend(self, s: Session) -> bool:
        """Freeze every process of the session's job; say whether it landed.

        A failure is reported, not raised: the game may have exited between
        the check and the call, which is the game having ended.
        """
        try:
            return s.job is not None and s.job.suspend()
        except OSError:
            return False

    def _resume(self, s: Session) -> bool:
        try:
            return s.job is not None and s.job.resume()
        except OSError:
            return False

    async def background(self) -> dict:
        """Freeze the session on the screen and give the interface back."""
        async with self._switching:
            if self._launching:
                raise SessionConflict("A game is still starting")
            s = self.foreground_session
            if s is None:
                raise SessionConflict("Nothing is running")
            await self._freeze(s)

            log.info("session %s (%s) suspended", s.game_key or "?", s.system_id)
            self._save_state()
            self._publish()
            await self._announce("game:backgrounded", s)
            return self.session_state()

    async def _freeze(self, s: Session) -> None:
        """Put the interface over `s`, then suspend it. Raises SessionConflict.

        In that order: a frozen window in front never answers the activation
        messages, so Windows could not put it behind the interface any more.
        A suspend that fails gives the screen back to the game still running.
        """
        await self._raise_interface(s)
        if not self._suspend(s):
            await self._give_back_the_screen(s)
            raise SessionConflict("That session could not be suspended")
        s.state = "background"
        s.bg_since = time.time()

    async def foreground(self, session_id: int | None = None) -> dict:
        """Wake a frozen session, and put whatever holds the screen behind it.

        One operation rather than two: only one thing can own the screen, so
        resuming a game while an app is up implies suspending the app. With no
        number this resumes the most recent suspended session.
        """
        async with self._switching:
            return await self._swap_in(session_id)

    async def _swap_in(self, session_id: int | None) -> dict:
        if self._launching:
            # A launch in flight is about to claim the screen; a resume racing
            # it would put two sessions in front of the player.
            raise SessionConflict("A game is still starting")
        if session_id is None:
            s = self.background_session
        else:
            s = next((x for x in self.background_sessions
                      if x.session_id == session_id), None)
        if s is None:
            raise SessionConflict("Nothing is in the background")

        outgoing = self.foreground_session
        if outgoing is not None:
            try:
                await self._freeze(outgoing)
            except SessionConflict:
                raise SessionConflict("The running session could not be suspended") from None

        if not self._resume(s):
            await self._undo_swap(outgoing)
            raise SessionConflict("That session could not be resumed")

        now = time.time()
        if s.bg_since:
            s.bg_total += max(0.0, now - s.bg_since)
        s.bg_since = 0.0
        s.state = "foreground"
        self._save_state()
        self._publish()
        log.info("session %s (%s) resumed", s.game_key or "?", s.system_id)
        if outgoing is not None:
            await self._announce("game:backgrounded", outgoing)
        await self._announce("game:foregrounded", s)
        await self._give_back_the_screen(s)
        return self.session_state()

    async def _undo_swap(self, outgoing: Session | None) -> None:
        """Put the screen back after a failed resume, and describe truthfully
        whatever was actually managed."""
        if outgoing is None:
            return
        if self._resume(outgoing):
            outgoing.bg_total += max(0.0, time.time() - outgoing.bg_since)
            outgoing.bg_since = 0.0
            outgoing.state = "foreground"
        # If that ALSO failed, the outgoing session is frozen with nothing on
        # the screen: persisted and announced, so a session bar can still
        # resume or close it.
        self._save_state()
        self._publish()
        await self._announce(
            "game:foregrounded" if outgoing.state == "foreground"
            else "game:backgrounded", outgoing)
        if outgoing.state == "background":
            await self._raise_interface(outgoing)
        else:
            # It was put behind the interface before its freeze.
            await self._give_back_the_screen(outgoing)

    async def _give_back_the_screen(self, s: Session) -> None:
        """Bring a resumed session's window to the front. Never fatal: a game
        merely behind the interface is still reachable, and an exception here
        would report a resume that already succeeded as failed."""
        try:
            from . import window_focus
            await window_focus.activate(s.system_id, s.job)
        except Exception:
            log.debug("could not raise the resumed session's window", exc_info=True)

    async def _raise_interface(self, s: Session) -> None:
        """Put the interface in front of a session about to be frozen. Never
        fatal either: the suspend goes ahead whatever the windows did."""
        try:
            from . import window_focus
            await window_focus.hide(s.system_id)
        except Exception:
            log.debug("could not raise the interface over the frozen session",
                      exc_info=True)

    # ── killing ──────────────────────────────────────────────────────────────

    async def kill(self, session_id: int | None = None) -> None:
        """End a session: the one on screen unless another is named, else the
        suspended one. The whole job, frozen processes included; never a polite
        close, which some emulators answer with a dialog no pad can click."""
        if session_id is None:
            target = self.foreground_session or self.background_session
        else:
            target = next((s for s in self._sessions
                           if s.session_id == session_id), None)
        if target is None:
            return
        if target.adopted:
            await self._kill_orphan(target)
            return
        if target.job is not None and not target.job.terminate():
            log.warning("could not terminate %s (pid %d)", target.game_key or "?",
                        target.pid)

    async def _kill_orphan(self, target: Session) -> None:
        """Kill a session adopted from a previous backend: no watcher awaits it."""
        log.info("killing adopted session %s (pid %d)", target.game_key or "?", target.pid)
        target.job.terminate()
        target.job.close()
        profile_saves.release_session(target)
        self._sessions = [s for s in self._sessions if s is not target]
        self._save_state()
        self._publish()
        # No playtime: `start_time` came off disk from a process that may have
        # died long ago, so the elapsed figure would be a guess in the stats.
        try:
            await ws.broadcast("game:finished", {
                "game_key": target.game_key, "system_id": target.system_id,
                "elapsed": target.played_secs(time.time()),
                "session": target.session_id,
            })
        except Exception:
            log.exception("_kill_orphan: failed to broadcast game:finished")

    # ── the end of a session ─────────────────────────────────────────────────

    async def _watch(self, s: Session | None = None) -> None:
        """Wait for one session's job to empty, and speak only for that session.

        Everything it needs is on the object it was handed; it never reads the
        manager's fields after its wait, which a new launch may have changed.
        """
        s = s or self._fg
        if s is None or s.job is None:
            return
        while s.alive():
            await asyncio.sleep(WATCH_POLL_SECS)
        elapsed = s.played_secs(time.time())

        profile_saves.release_session(s)
        # Only the slot that still holds this session may be cleared by it.
        if s in self._sessions:
            self._sessions = [x for x in self._sessions if x is not s]
            self._save_state()
            self._publish()
        s.job.close()

        # A one-frame crash is not a play session.
        if elapsed > 5:
            try:
                db = await get_db()
                now = datetime.now(timezone.utc).isoformat()
                await playtime_rows.record(db, s.game_key, s.system_id, elapsed, now)
            except Exception:
                log.exception("_watch: failed to save playtime for %s", s.game_key)

        try:
            await ws.broadcast("game:finished", {
                "game_key": s.game_key,
                "system_id": s.system_id,
                "elapsed": elapsed,
                # Which run ended: a finish belonging to a game the player has
                # left behind must not unlock the screen over the current one.
                "session": s.session_id,
            })
        except Exception:
            log.exception("_watch: failed to broadcast game:finished")


def _log_watch_error(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.warning("watch task failed: %s", task.exception())


process_manager = ProcessManager()
