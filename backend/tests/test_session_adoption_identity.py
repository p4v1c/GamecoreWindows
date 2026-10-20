"""A saved session is adopted only if it is still the game we started.

session.json lives in config/ and survives a power cut. After a reboot
Windows hands the same PID to an unrelated process, and adopting it would make
the box say "a game is already running" and kill the wrong tree on double-PS.
Two locks: the job must open again by its name (a name dies with the job, and
with the boot), and its first process must carry the boot id and start time on
record. Real jobs and processes throughout.
"""
import asyncio
import json
import subprocess
import sys

import pytest

from backend.services import process_identity
from backend.services import process_manager as pm
from backend.services.game_session import Session
from backend.services.platform import jobs
from backend.tests.fake_jobs import FakeJob


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    f = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", f)
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    return f


@pytest.fixture
def game():
    """A real game left by a previous backend: its job, handle closed."""
    started = jobs.spawn([sys.executable, "-c", "import time; time.sleep(120)"],
                         env=None, stdout=subprocess.DEVNULL)
    started.close()
    yield started
    survivor = jobs.open_job(started.name, started.leader_pid)
    if survivor is not None:
        survivor.terminate()
        survivor.close()


def _entry(game, **identity) -> dict:
    return {"job": game.name, "pid": game.leader_pid, "game_key": "Melee.iso",
            "system_id": "dolphin", "exec_path": "Dolphin.exe", "launch_args": [],
            "rom_path": "", "started_at": 1.0, **identity}


def _adopt(session_file, entry: dict) -> pm.ProcessManager:
    session_file.write_text(json.dumps({"sessions": [entry]}))
    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    return fresh


def _still_running(game) -> bool:
    job = jobs.open_job(game.name, game.leader_pid)
    try:
        return job is not None and job.alive()
    finally:
        if job is not None:
            job.close()


def test_the_game_we_started_is_adopted(session_file, game):
    fresh = _adopt(session_file, _entry(
        game, boot_id=process_identity.boot_id(),
        leader_start=process_identity.start_time(game.leader_pid)))
    assert fresh.is_running


def test_a_pid_from_before_a_reboot_is_not_adopted(session_file, game):
    entry = _entry(game, boot_id="boot-before-the-power-cut",
                   leader_start=process_identity.start_time(game.leader_pid))
    fresh = _adopt(session_file, entry)
    assert not fresh.is_running, "a reboot means the saved game is gone"
    assert not session_file.exists()
    assert _still_running(game), "the unrelated process must be left alone"


def test_a_pid_reused_by_a_newer_process_is_not_adopted(session_file, game):
    entry = _entry(game, boot_id=process_identity.boot_id(), leader_start="1")
    fresh = _adopt(session_file, entry)
    assert not fresh.is_running
    assert _still_running(game)


def test_a_session_file_without_identity_is_discarded(session_file, game):
    """No way to prove the process is ours."""
    fresh = _adopt(session_file, _entry(game))
    assert not fresh.is_running
    assert not session_file.exists()
    assert _still_running(game)


def test_a_job_name_that_no_longer_opens_is_not_adopted(session_file, game):
    """The right pid and start time, but not our job: never adopted."""
    entry = _entry(game, boot_id=process_identity.boot_id(),
                   leader_start=process_identity.start_time(game.leader_pid))
    entry["job"] = "Local\\GameCore-0000000000000000000000000000dead"
    fresh = _adopt(session_file, entry)
    assert not fresh.is_running
    assert _still_running(game)


def test_only_gamecore_jobs_are_opened():
    """A name from a tampered session.json cannot reach another program's job."""
    assert jobs.open_job("Global\\SomeoneElsesJob", 4) is None


def test_the_saved_session_carries_the_identity(session_file, monkeypatch):
    monkeypatch.setattr(process_identity, "boot_id", lambda: "boot-a")
    monkeypatch.setattr(process_identity, "start_time", lambda pid: f"t{pid}")
    m = pm.ProcessManager()
    m._sessions.append(Session(job=FakeJob(4242), adopted=True, game_key="g"))
    m._save_state()
    saved = json.loads(session_file.read_text())["sessions"][0]
    assert saved["boot_id"] == "boot-a"
    assert saved["leader_start"] == "t4242"
    assert saved["job"] == "Local\\GameCore-fake4242"
    assert saved["pid"] == 4242
