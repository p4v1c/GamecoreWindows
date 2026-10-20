"""A game's process tree in a real Job Object, on this Windows.

The "game" is Python: a parent that starts a child, so every test proves the
whole tree moves, not one process. Every job a test starts is terminated at
the end, whatever happens.
"""
from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from backend.services.platform import jobs

# A parent that starts a child at once, and a second one a moment later
# (a process born after the launch must join the job too).
TREE = ("import subprocess, sys, time\n"
        "sleep = [sys.executable, '-c', 'import time; time.sleep(120)']\n"
        "subprocess.Popen(sleep)\n"
        "time.sleep(0.5)\n"
        "subprocess.Popen(sleep)\n"
        "time.sleep(120)\n")


@pytest.fixture
def started():
    """Spawn TREE in a job; terminate it afterwards."""
    made: list[jobs.GameJob] = []

    def spawn(code: str = TREE) -> jobs.GameJob:
        job = jobs.spawn([sys.executable, "-c", code], env=None,
                         stdout=subprocess.DEVNULL)
        made.append(job)
        return job

    yield spawn
    for job in made:
        survivor = jobs.open_job(job.name, job.leader_pid)
        for handle in (job, survivor):
            if handle is not None:
                handle.terminate()
                handle.close()


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _statuses(job: jobs.GameJob) -> set[str]:
    out = set()
    for pid in job.pids():
        try:
            out.add(psutil.Process(pid).status())
        except psutil.NoSuchProcess:
            pass
    return out


def _tree_started(job: jobs.GameJob) -> bool:
    # The venv's python.exe is a launcher that starts the real interpreter, so
    # the tree is at least the parent and two children, each maybe doubled.
    return _wait_for(lambda: len(job.pids()) >= 3)


def test_the_game_and_every_process_it_starts_are_in_the_job(started):
    job = started()
    assert job.alive()
    assert _tree_started(job), job.pids()
    assert job.leader_pid in job.pids()


def test_suspending_freezes_the_whole_tree_and_resuming_thaws_it(started):
    job = started()
    assert _tree_started(job)
    assert job.suspend()
    assert _statuses(job) == {psutil.STATUS_STOPPED}
    assert job.resume()
    assert _statuses(job) == {psutil.STATUS_RUNNING}


def test_terminating_kills_the_whole_tree_frozen_or_not(started):
    job = started()
    assert _tree_started(job)
    tree = job.pids()
    assert job.suspend()
    assert job.terminate()
    assert _wait_for(lambda: not job.alive())
    assert psutil.wait_procs([p for p in map(_process, tree) if p], timeout=5)[1] == []


def _process(pid: int) -> psutil.Process | None:
    try:
        return psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None


def test_closing_the_handle_does_not_kill_the_game(started):
    """A backend that restarts closes its handles; the game must survive to be
    adopted. No kill-on-close."""
    job = started()
    job.close()
    time.sleep(0.3)
    assert psutil.pid_exists(job.leader_pid)


def test_a_restarted_backend_opens_the_same_job_by_its_name(started):
    job = started()
    assert _tree_started(job)
    pids = set(job.pids())
    job.close()
    again = jobs.open_job(job.name, job.leader_pid)
    assert again is not None, "the name died with the backend's handle"
    try:
        assert set(again.pids()) == pids
        assert again.leader_pid == job.leader_pid
    finally:
        again.close()


def test_a_job_that_ended_cannot_be_opened_again(started):
    job = started("pass")
    assert _wait_for(lambda: not job.alive())
    name, pid = job.name, job.leader_pid
    job.close()
    assert jobs.open_job(name, pid) is None


def test_a_suspend_that_fails_half_way_thaws_what_it_froze(started, monkeypatch):
    """All or nothing: a half-frozen game would be described as suspended
    while part of it still runs."""
    job = started()
    assert _tree_started(job)
    real = jobs._nt_call
    calls = {"suspends": 0}

    def second_refused(function, pid):
        if function is jobs._ntdll.NtSuspendProcess:
            calls["suspends"] += 1
            if calls["suspends"] == 2:
                return False
        return real(function, pid)

    monkeypatch.setattr(jobs, "_nt_call", second_refused)
    assert not job.suspend()
    monkeypatch.setattr(jobs, "_nt_call", real)
    assert _statuses(job) == {psutil.STATUS_RUNNING}


def test_a_missing_executable_is_the_error_popen_gives(tmp_path):
    with pytest.raises(FileNotFoundError):
        jobs.spawn([str(tmp_path / "no-such-emulator.exe")], env=None,
                   stdout=subprocess.DEVNULL)


def test_what_the_game_prints_reaches_its_log(tmp_path):
    log = tmp_path / "launch.log"
    with log.open("wb") as out:
        job = jobs.spawn([sys.executable, "-c", "print('hello'); import sys; "
                          "print('oops', file=sys.stderr)"], env=None, stdout=out)
    assert _wait_for(lambda: not job.alive())
    job.close()
    assert sorted(log.read_text().split()) == ["hello", "oops"]
