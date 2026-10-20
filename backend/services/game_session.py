"""One launched thing — a game or an app — and what describes it on screen,
on disk and in the playtime statistics. `process_manager.py` decides; this
module only holds the facts."""
from __future__ import annotations

import time

from . import process_identity


class Session:
    """A session and everything needed to describe it, suspend it, resume it,
    kill it and bill it for playtime.

    An object rather than fields on the manager: putting a game in the
    background and starting something else used to overwrite the first one's
    identity in place, leaving a frozen emulator nothing could reach.
    """

    __slots__ = ("job", "adopted", "game_key", "system_id", "rom_path",
                 "exec_path", "launch_args", "start_time", "session_id",
                 "state", "bg_since", "bg_total", "leader_start")

    def __init__(self, *, job=None, adopted: bool = False, game_key: str = "",
                 system_id: str = "", rom_path: str = "", exec_path: str = "",
                 launch_args: list[str] | None = None, start_time: float = 0.0,
                 session_id: int = 0, state: str = "foreground",
                 bg_since: float = 0.0, bg_total: float = 0.0, leader_start: str = ""):
        #: The game's process tree (`platform/jobs.py` GameJob, or a test fake).
        self.job = job
        #: Re-opened from session.json by a restarted backend: no watcher awaits
        #: it, so whoever notices its end announces it (`ProcessManager._reap`).
        self.adopted = adopted
        self.game_key = game_key
        self.system_id = system_id
        self.rom_path = rom_path
        self.exec_path = exec_path
        self.launch_args = launch_args or []
        self.start_time = start_time
        self.session_id = session_id
        self.state = state
        #: When the current suspended stretch began, 0.0 while in the foreground.
        self.bg_since = bg_since
        #: Seconds already spent suspended, over every stretch that has ended.
        self.bg_total = bg_total
        self.leader_start = leader_start

    # ── identity ─────────────────────────────────────────────────────────────

    @property
    def pid(self) -> int:
        """The first process of the tree, for logs and the identity check.
        Never what is signalled: the job is (see `platform/jobs.py`)."""
        return self.job.leader_pid if self.job is not None else 0

    def alive(self) -> bool:
        """Some process of the tree still exists, running or frozen. Not the
        first process alone: a launcher that starts the game and exits leaves
        the game running."""
        return self.job is not None and self.job.alive()

    @property
    def is_app(self) -> bool:
        """An application tile rather than a game: a tile with no ROM launches
        with `game_key == system_id` (services/launch.py), and themes need the
        difference to say "Close app" rather than "Close game"."""
        return bool(self.game_key) and self.game_key == self.system_id

    # ── time ─────────────────────────────────────────────────────────────────

    def background_secs(self, now: float) -> float:
        """Suspended seconds, including the stretch still open."""
        open_stretch = (now - self.bg_since
                        if self.state == "background" and self.bg_since else 0.0)
        return self.bg_total + max(0.0, open_stretch)

    def played_secs(self, now: float) -> int:
        """Wall time minus every second the game was frozen.

        A suspended game is not being played; counting it would reward leaving
        one suspended overnight. Clamped at zero: a clock that moved backwards
        must not subtract from a player's hours.
        """
        return max(0, int(now - self.start_time - self.background_secs(now)))

    # ── how it is described to everyone else ─────────────────────────────────

    def describe(self) -> dict:
        return {
            "game_key": self.game_key,
            "system_id": self.system_id,
            "rom_path": self.rom_path,
            "session": self.session_id,
            "state": self.state,
            "kind": "app" if self.is_app else "game",
        }

    def to_disk(self) -> dict:
        """The `config/session.json` row (`docs/architecture/07-config-and-data.md`)."""
        # Read while the first process lives: adoption compares it to reject a
        # reused pid.
        if not self.leader_start:
            self.leader_start = process_identity.start_time(self.pid)
        return {
            "job": self.job.name if self.job is not None else "",
            "pid": self.pid,
            "game_key": self.game_key,
            "system_id": self.system_id,
            "exec_path": self.exec_path,
            "rom_path": self.rom_path,
            "launch_args": self.launch_args,
            "started_at": self.start_time,
            "state": self.state,
            "bg_since": self.bg_since,
            "bg_total": self.bg_total,
            "boot_id": process_identity.boot_id(),
            "leader_start": self.leader_start,
        }


def from_disk(entry: dict, job, session_id: int) -> Session:
    """An adopted session from a `session.json` row whose job is open again."""
    try:
        started = float(entry.get("started_at") or time.time())
    except (TypeError, ValueError):
        started = time.time()
    state = entry.get("state")
    return Session(
        job=job,
        adopted=True,
        game_key=str(entry.get("game_key") or ""),
        system_id=str(entry.get("system_id") or ""),
        exec_path=str(entry.get("exec_path") or ""),
        rom_path=str(entry.get("rom_path") or ""),
        launch_args=[str(a) for a in (entry.get("launch_args") or [])],
        start_time=started,
        session_id=session_id,
        state=state if state in ("foreground", "background") else "foreground",
        bg_since=_as_float(entry.get("bg_since")),
        bg_total=_as_float(entry.get("bg_total")),
        leader_start=str(entry.get("leader_start") or ""),
    )


def _as_float(value) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0
