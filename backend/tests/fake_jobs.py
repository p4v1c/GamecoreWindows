"""A game's Job Object, played by the test: no process starts, nothing is frozen.

`process_manager` reaches a game only through `platform/jobs.py` (`spawn`,
`open_job`) and the `GameJob` it gets back (`alive`, `pids`, `suspend`,
`resume`, `terminate`, `close`). `FakeJob` answers the same calls and records
them in order as `(leader_pid, call)`, so a test asserts what was frozen,
thawed or killed. `test_platform_jobs.py` checks the real thing on real
processes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services import process_manager as pm


class FakeJob:
    """One launched game. `exit()` is the game ending on its own."""

    def __init__(self, leader_pid: int = 4242, calls: list | None = None,
                 name: str | None = None):
        self.leader_pid = leader_pid
        self.name = name or f"Local\\GameCore-fake{leader_pid}"
        self.calls = calls if calls is not None else []
        self.running = True
        self.frozen = False
        self.closed = False
        #: Calls that fail ("suspend", "resume", "terminate"), as access denied would.
        self.refuse: set[str] = set()

    def alive(self) -> bool:
        return self.running and not self.closed

    def pids(self) -> list[int]:
        return [self.leader_pid] if self.alive() else []

    def _record(self, call: str) -> bool:
        self.calls.append((self.leader_pid, call))
        return call not in self.refuse and self.alive()

    def suspend(self) -> bool:
        if not self._record("suspend"):
            return False
        self.frozen = True
        return True

    def resume(self) -> bool:
        if not self._record("resume"):
            return False
        self.frozen = False
        return True

    def terminate(self) -> bool:
        if not self._record("terminate"):
            return False
        self.running = False
        return True

    def exit(self) -> None:
        self.running = False

    def close(self) -> None:
        self.closed = True


class Spawner:
    """Stands in for `jobs.spawn`: hands out the queued jobs, keeps the argv."""

    def __init__(self):
        self.calls: list[tuple[int, str]] = []
        self.queue: list[FakeJob] = []
        self.argv: list[list[str]] = []
        self.envs: list[dict] = []
        self._next_pid = 1000

    def job(self, pid: int | None = None) -> FakeJob:
        """Queue the job the next launch gets, and return it."""
        if pid is None:
            self._next_pid += 1
            pid = self._next_pid
        job = FakeJob(pid, self.calls)
        self.queue.append(job)
        return job

    def __call__(self, cmd, *, env, stdout, cwd=None):
        self.argv.append(list(cmd))
        self.envs.append(env)
        if not self.queue:
            self.job()
        return self.queue.pop(0)


@pytest.fixture
def spawner(monkeypatch) -> Spawner:
    """`jobs.spawn` faked, the desktop check passed, the env not built."""
    fake = Spawner()
    monkeypatch.setattr(pm.jobs, "spawn", fake)
    monkeypatch.setattr(pm.session, "launch_blocker", lambda: "")
    monkeypatch.setattr(pm.session, "display_env", AsyncMock(return_value={}))
    return fake


def quiet_manager(monkeypatch) -> pm.ProcessManager:
    """A manager that writes no session file, broadcasts nowhere and moves no window."""
    m = pm.ProcessManager()
    monkeypatch.setattr(pm.ws, "broadcast", AsyncMock())
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(m, "_save_state", lambda: None)
    monkeypatch.setattr(m, "_clear_session", lambda: None)
    monkeypatch.setattr(m, "_give_back_the_screen", AsyncMock())
    monkeypatch.setattr(m, "_raise_interface", AsyncMock())
    return m
