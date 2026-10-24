"""The failure paths around launching, standby and restarting.

Everything here is about what the box does when something has already gone
wrong: the screen left off by a previous process, an emulator that is not
installed, a game still running after the backend restarted.

The game that outlives the backend is a real process tree in a real job:
the restart is a closed handle, the adoption a job opened again by its name.
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psutil
import pytest

from backend.services import process_identity
from backend.services import process_manager as pm
from backend.services import standby
from backend.services.platform import jobs


# ── #13 · standby ────────────────────────────────────────────────────────────

@pytest.fixture
def recorded_cmds(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []

    async def fake_power(*argv, **kw):
        calls.append(argv)
        return True

    monkeypatch.setattr(standby, "_power", fake_power)
    monkeypatch.setattr(standby, "_PLAN_NOTE", tmp_path / "power-plan-handoff.json")
    return calls


def test_exit_standby_is_unconditional(recorded_cmds):
    """The wake that mattered was the one from a state we already believed active.

    _state is in memory and the screen's power is not: after a restart the two
    disagreed and an early return meant POST /api/standby/exit did nothing.
    """
    standby._state = "active"
    asyncio.run(standby.exit_standby())
    assert ("screen_on",) in recorded_cmds, recorded_cmds



def _turns_the_screen_on(argv: tuple) -> bool:
    return argv == ("screen_on",)


def test_exit_standby_from_sleep_still_wakes(recorded_cmds):
    standby._state = "sleep"
    asyncio.run(standby.exit_standby())
    assert standby.get_state() == "active"
    assert any(_turns_the_screen_on(c) for c in recorded_cmds), recorded_cmds


def test_resume_after_restart_forces_the_screen_back_on(recorded_cmds):
    standby._state = "sleep"          # what the *previous* process had left behind
    asyncio.run(standby.resume_after_restart())
    assert standby.get_state() == "active"
    assert any(_turns_the_screen_on(c) for c in recorded_cmds), recorded_cmds
    # No plan was noted by a previous sleep: nothing to restore, nothing forced.
    assert not [c for c in recorded_cmds if c[0] == "set_active_scheme"], recorded_cmds


# ── #17 · a game that outlives the backend ───────────────────────────────────

@pytest.fixture
def session_file(tmp_path, monkeypatch):
    f = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", f)
    return f


SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]
ROM = r"E:\ROMS\gamecube\Melee.iso"


def _gone(pid: int) -> bool:
    try:
        psutil.Process(pid).wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        return False
    return True


@pytest.fixture
def left_behind():
    """A game a previous backend started: its job, whose handle that backend
    has closed on its way out. Killed at the end whatever happens."""
    started = jobs.spawn(SLEEPER, env=None, stdout=subprocess.DEVNULL)
    pid, name = started.leader_pid, started.name
    started.close()
    yield pid, name
    survivor = jobs.open_job(name, pid)
    if survivor is not None:
        survivor.terminate()
        survivor.close()


def _row(pid: int, name: str, **extra) -> dict:
    """What `_save_state` records, identity included."""
    return {"sessions": [{
        "job": name, "pid": pid, "game_key": "Melee.iso", "system_id": "dolphin",
        "exec_path": r"C:\Emulators\Dolphin\Dolphin.exe",
        "rom_path": ROM, "launch_args": [],
        "started_at": time.time(), "boot_id": process_identity.boot_id(),
        "leader_start": process_identity.start_time(pid), **extra}]}


def test_a_restarted_backend_finds_the_running_game(session_file, left_behind):
    session_file.write_text(json.dumps(_row(*left_behind)))

    fresh = pm.ProcessManager()          # what starting the backend gives you
    assert not fresh.is_running, "nothing adopted yet"

    asyncio.run(fresh.adopt_orphan())
    assert fresh.is_running, "the emulator is still on screen — say so"
    # rom_path is part of the session: storage_monitor asks whether the disk
    # just unplugged is the one the running game came from.
    game = fresh.current_game
    # An adoption is a run, and runs are numbered.
    assert game.pop("session") > 0
    assert game == {"game_key": "Melee.iso", "system_id": "dolphin",
                    "rom_path": ROM}


def test_the_adopted_game_can_still_be_killed(session_file, left_behind):
    """The whole point: double-PS could no longer close an orphaned emulator."""
    pid, name = left_behind
    session_file.write_text(json.dumps(_row(pid, name)))

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    asyncio.run(fresh.kill())

    assert _gone(pid), "the emulator must actually die"
    assert not fresh.is_running
    assert not session_file.exists(), "the session file goes with it"


def test_a_stale_session_file_is_discarded(session_file):
    """The game ended while no backend ran: its job, and its name, are gone."""
    started = jobs.spawn(SLEEPER, env=None, stdout=subprocess.DEVNULL)
    pid, name = started.leader_pid, started.name
    row = _row(pid, name)
    started.terminate()
    started.close()
    assert _gone(pid)
    session_file.write_text(json.dumps(row))

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    assert not fresh.is_running, "that job is long gone"
    assert not session_file.exists()


def test_a_corrupt_session_file_is_ignored(session_file):
    session_file.write_text("{ not json")
    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    assert not fresh.is_running


def test_no_session_file_is_the_normal_case(session_file):
    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    assert not fresh.is_running


# ── #16 · an emulator that is not installed ──────────────────────────────────

@pytest.fixture
def client_with_ghost_system(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.services import systems as systems_service
    from backend import main

    ghost = {"id": "ghost", "label": "Ghost", "kind": "emulator",
             "path": "/usr/bin/definitely-not-installed", "args": "", "romsPath": ""}
    monkeypatch.setattr(systems_service, "list_all", lambda: [ghost])
    with TestClient(main.app) as c:
        yield c


def test_missing_emulator_returns_503_and_says_which(client_with_ghost_system):
    r = client_with_ghost_system.post("/api/games/launch", json={"system_id": "ghost"})
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "ghost" in detail or "not-installed" in detail, detail


def test_a_failed_launch_leaves_the_box_able_to_try_again(client_with_ghost_system):
    for _ in range(3):
        r = client_with_ghost_system.post("/api/games/launch", json={"system_id": "ghost"})
        assert r.status_code == 503, r.text
    assert not pm.process_manager.is_running, "_launching must have been released"
