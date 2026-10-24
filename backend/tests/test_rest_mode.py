"""Rest mode: nothing may be alive when the box goes to sleep.

No process is started and nothing is suspended: the jobs are `FakeJob`s that
record what they are asked, and the suspend is a stub that notes what was
still alive when it was called. That last record is the whole point of the
feature.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.services import launch, process_manager as pm, rest_mode
from backend.services.errors import ServiceError
from backend.tests.fake_jobs import FakeJob


class LingeringJob(FakeJob):
    """A job whose first terminate does not take: it dies on the second."""

    def terminate(self) -> bool:
        if self.calls.count((self.leader_pid, "terminate")) == 0:
            self.calls.append((self.leader_pid, "terminate"))
            return True
        return super().terminate()


@pytest.fixture
def box(monkeypatch, spawner):
    """A manager, its jobs, and every call and suspend."""
    m = pm.ProcessManager()
    monkeypatch.setattr(pm.ws, "broadcast", AsyncMock())
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(m, "_save_state", lambda: None)
    monkeypatch.setattr(m, "_give_back_the_screen", AsyncMock())
    monkeypatch.setattr(m, "_raise_interface", AsyncMock())
    monkeypatch.setattr(rest_mode, "process_manager", m)
    monkeypatch.setattr(pm, "MAX_SESSIONS", 3)   # read off the RAM of the box otherwise
    monkeypatch.setattr(pm, "WATCH_POLL_SECS", 0.01)
    monkeypatch.setattr(rest_mode, "POLL_SECS", 0.01)
    monkeypatch.setattr(rest_mode, "EXIT_WAIT_SECS", 0.2)
    monkeypatch.setattr(rest_mode, "FORCE_WAIT_SECS", 0.1)
    monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 3600)

    jobs: list[FakeJob] = []
    state = {"suspended_with": None, "suspend_ok": True}

    async def suspend():
        state["suspended_with"] = {j.leader_pid for j in jobs if j.alive()}
        return state["suspend_ok"], "" if state["suspend_ok"] else "refused"

    monkeypatch.setattr(rest_mode, "_suspend", suspend)

    async def start(pid, game_key, system_id="duckstation", lingering=False):
        job = (LingeringJob(pid, spawner.calls) if lingering
               else FakeJob(pid, spawner.calls))
        jobs.append(job)
        spawner.queue.append(job)
        await m.launch("emu", "", rom_path=game_key, game_key=game_key,
                       system_id=system_id)
        return job

    yield {"manager": m, "state": state, "start": start, "calls": spawner.calls}
    rest_mode.woke()


def _terminated(box) -> list[int]:
    return [pid for pid, call in box["calls"] if call == "terminate"]


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
        assert set(_terminated(box)) == {101, 202, 303}
    asyncio.run(scenario())


def test_a_frozen_session_is_terminated_as_it_is(box):
    """Terminating a job kills frozen processes too: no thaw, which would let
    the game run a frame or play a sound on its way out."""
    async def scenario():
        await box["start"](101, "crash.cue")
        await box["manager"].background()
        await rest_mode.enter()
        assert [c for p, c in box["calls"] if p == 101] == ["suspend", "terminate"]
    asyncio.run(scenario())


def test_a_job_that_lingers_is_forced_then_the_box_sleeps(box):
    async def scenario():
        await box["start"](101, "game.iso", lingering=True)
        await rest_mode.enter()
        assert _terminated(box).count(101) == 2, "the survivor was not forced"
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
        assert 404 in _terminated(box)
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


def test_the_sleep_goes_through_the_platform_layer_off_the_event_loop(monkeypatch):
    """An S3 sleep returns only after the wake: the loop must stay free meanwhile."""
    import threading
    seen = {}

    def suspend():
        seen["thread"] = threading.current_thread() is not threading.main_thread()
        return False, "refused by policy"

    monkeypatch.setattr(rest_mode.sleep, "suspend", suspend)
    assert asyncio.run(rest_mode._suspend()) == (False, "refused by policy")
    assert seen == {"thread": True}


def test_the_refusal_says_what_the_player_can_do():
    assert "Turn it off" in rest_mode.REFUSED and "Update GameCore" not in rest_mode.REFUSED


def test_the_router_rests_and_reports_wakes(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch.setattr(rest_mode, "enter", AsyncMock(return_value={"ok": True, "forced": 0}))
    client = TestClient(app)
    assert client.post("/api/power/rest").json() == {"ok": True, "forced": 0}
    state = client.get("/api/power/state").json()
    assert set(state) == {"resting", "resumes", "sleeping"}
