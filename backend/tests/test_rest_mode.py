"""Rest mode: nothing may be alive when the box goes to sleep.

No process is started and nothing is suspended: children are fakes, signals
are recorded, `sudo systemctl suspend` is a stub that notes what was still
alive when it was called. That last record is the whole point of the feature.
"""
from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock

import pytest

from backend.services import launch, process_manager as pm, rest_mode
from backend.services.errors import ServiceError

pytestmark = pytest.mark.usefixtures("posix_boundary")


class FakeProcess:
    """A child whose group dies when SIGKILL reaches it, unless told to linger."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None
        self.done = asyncio.Event()

    async def wait(self):
        await self.done.wait()
        return self.returncode


@pytest.fixture
def box(monkeypatch):
    """A manager, its live process groups, and every signal and suspend."""
    m = pm.ProcessManager()
    monkeypatch.setattr(pm, "display_env", AsyncMock(return_value={}))
    monkeypatch.setattr(pm.ws, "broadcast", AsyncMock())
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(m, "_save_state", lambda: None)
    monkeypatch.setattr(m, "_give_back_the_screen", AsyncMock())
    monkeypatch.setattr(m, "_raise_interface", AsyncMock())
    monkeypatch.setattr(rest_mode, "process_manager", m)
    monkeypatch.setattr(pm, "MAX_SESSIONS", 3)   # read off the RAM of the box otherwise
    monkeypatch.setattr(rest_mode, "POLL_SECS", 0.01)
    monkeypatch.setattr(rest_mode, "EXIT_WAIT_SECS", 0.2)
    monkeypatch.setattr(rest_mode, "FORCE_WAIT_SECS", 0.1)
    monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 3600)

    state = {"alive": set(), "stubborn": set(), "signals": [], "procs": {},
             "suspended_with": None, "suspend_ok": True}

    def killpg(pgid, sig):
        state["signals"].append((pgid, sig))
        if sig == signal.SIGKILL and pgid in state["alive"]:
            if pgid in state["stubborn"]:
                state["stubborn"].discard(pgid)      # dies on the second SIGKILL
                return
            state["alive"].discard(pgid)
            proc = state["procs"].get(pgid)
            if proc is not None:
                proc.returncode = -9
                proc.done.set()

    # One `os` module for both: the backend's own group is getpgid(0) == 0.
    monkeypatch.setattr(pm.os, "killpg", killpg)
    monkeypatch.setattr(pm.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(pm, "_pgid_alive", lambda pgid: pgid in state["alive"])

    async def suspend():
        state["suspended_with"] = set(state["alive"])
        return state["suspend_ok"], "" if state["suspend_ok"] else "sudo: a password is required"

    monkeypatch.setattr(rest_mode, "_suspend", suspend)

    async def start(pid, game_key, system_id="duckstation"):
        proc = FakeProcess(pid)
        state["procs"][pid] = proc
        state["alive"].add(pid)
        monkeypatch.setattr(pm.asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=proc))
        await m.launch("emu", "", rom_path=game_key, game_key=game_key,
                       system_id=system_id)
        return proc

    yield {"manager": m, "state": state, "start": start}
    rest_mode.woke()


def test_every_session_is_gone_before_the_box_sleeps(box):
    """A game on screen and a game frozen behind an app: all three go."""
    async def scenario():
        await box["start"](101, "crash.cue")
        await box["manager"].background()
        await box["start"](202, "youtube", system_id="youtube")
        await box["manager"].background()
        await box["start"](303, "stremio", system_id="stremio")

        result = await rest_mode.enter()

        assert result["ok"] is True
        assert box["state"]["suspended_with"] == set(), "slept with a session alive"
        killed = {pgid for pgid, sig in box["state"]["signals"] if sig == signal.SIGKILL}
        assert killed == {101, 202, 303}
    asyncio.run(scenario())


def test_a_frozen_session_is_thawed_before_it_is_killed(box):
    """A stopped process cannot run its exit path; process_manager.kill thaws it."""
    async def scenario():
        await box["start"](101, "crash.cue")
        await box["manager"].background()
        await rest_mode.enter()
        sigs = [sig for pgid, sig in box["state"]["signals"] if pgid == 101]
        assert sigs.index(signal.SIGCONT) < sigs.index(signal.SIGKILL)
    asyncio.run(scenario())


def test_a_group_that_lingers_is_forced_then_the_box_sleeps(box):
    async def scenario():
        await box["start"](101, "game.iso")
        box["state"]["stubborn"].add(101)
        await rest_mode.enter()
        kills = [p for p, s in box["state"]["signals"] if s == signal.SIGKILL]
        assert kills.count(101) == 2, "the survivor was not forced"
        assert box["state"]["suspended_with"] == set()
    asyncio.run(scenario())


def test_nothing_running_still_sleeps(box):
    async def scenario():
        result = await rest_mode.enter()
        assert result == {"ok": True, "forced": 0}
        assert box["state"]["suspended_with"] == set()
    asyncio.run(scenario())


def test_launches_are_refused_until_the_wake(box):
    async def scenario():
        await rest_mode.enter()
        assert rest_mode.is_resting()
        with pytest.raises(launch.LaunchRefused) as refused:
            await launch.launch({"id": "nes"}, "nes", "", "")
        assert refused.value.status == 409
        rest_mode.woke()
        assert not rest_mode.is_resting()
        assert launch._gate_reason == ""
    asyncio.run(scenario())


def test_a_launch_in_flight_is_waited_for_and_then_killed(box, monkeypatch):
    """Pressed launch, then the power menu: the game it starts must not survive."""
    async def scenario():
        monkeypatch.setattr(launch, "_launching", True)

        async def finish_launch():
            await asyncio.sleep(0.05)
            await box["start"](404, "late.iso")
            monkeypatch.setattr(launch, "_launching", False)

        task = asyncio.create_task(finish_launch())
        await rest_mode.enter()
        await task
        assert box["state"]["suspended_with"] == set()
        assert (404, signal.SIGKILL) in box["state"]["signals"]
    asyncio.run(scenario())


def test_a_refused_suspend_reports_and_reopens_launches(box):
    async def scenario():
        box["state"]["suspend_ok"] = False
        with pytest.raises(ServiceError) as err:
            await rest_mode.enter()
        assert err.value.status == 503
        assert not rest_mode.is_resting()
        assert launch._gate_reason == ""
    asyncio.run(scenario())


def test_a_second_press_while_going_to_sleep_is_refused(box, monkeypatch):
    async def scenario():
        monkeypatch.setattr(rest_mode, "_resting", True)
        with pytest.raises(ServiceError) as err:
            await rest_mode.enter()
        assert err.value.status == 409
    asyncio.run(scenario())


def test_launches_reopen_if_no_wake_is_ever_reported(box, monkeypatch):
    async def scenario():
        monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 0.05)
        await rest_mode.enter()
        await asyncio.sleep(0.15)
        assert not rest_mode.is_resting()
    asyncio.run(scenario())


def test_the_suspend_command_is_the_one_sudoers_grants():
    assert rest_mode.SUSPEND_CMD == ("sudo", "-n", "systemctl", "suspend")


def test_the_router_rests_and_reports_wakes(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch.setattr(rest_mode, "enter", AsyncMock(return_value={"ok": True, "forced": 0}))
    client = TestClient(app)
    assert client.post("/api/power/rest").json() == {"ok": True, "forced": 0}
    state = client.get("/api/power/state").json()
    assert set(state) == {"resting", "resumes", "sleeping"}
