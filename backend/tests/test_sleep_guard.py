"""Every sleep closes the games: the sweep Windows' suspend event waits for.

The sweep is recorded; nothing sleeps.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.services import launch, rest_mode, resume, sleep_guard
from backend.services.game_session import Session
from backend.tests.fake_jobs import FakeJob


@pytest.fixture
def guard(monkeypatch):
    calls: list[tuple] = []

    async def close_everything(exit_wait=None, force_wait=None):
        calls.append(("close", exit_wait, force_wait))
        return []

    monkeypatch.setattr(sleep_guard.rest_mode, "close_everything", close_everything)
    monkeypatch.setattr(sleep_guard, "_swept_for_sleep", False)
    yield calls
    launch.open_gate()


def test_a_sleep_closes_everything_and_refuses_launches(guard):
    asyncio.run(sleep_guard.before_sleep())
    assert guard == [("close", sleep_guard.SLEEP_EXIT_WAIT_SECS,
                      sleep_guard.SLEEP_FORCE_WAIT_SECS)]
    assert launch._gate_reason, "a launch could start a game after the sweep"


def test_the_sweep_fits_in_what_windows_waits_for_the_suspend_handler():
    """Windows sleeps about 2 s after the suspend event, answered or not."""
    worst = sleep_guard.SLEEP_EXIT_WAIT_SECS + sleep_guard.SLEEP_FORCE_WAIT_SECS
    assert worst < sleep_guard.SLEEP_BUDGET_SECS < resume.SUSPEND_HOLD_SECS < 2


def test_a_slow_sweep_is_cut_at_the_budget_and_finished_after_the_wake(guard, monkeypatch):
    async def slow(exit_wait=None, force_wait=None):
        guard.append(("close",))
        await asyncio.sleep(10)

    monkeypatch.setattr(sleep_guard.rest_mode, "close_everything", slow)
    monkeypatch.setattr(sleep_guard, "SLEEP_BUDGET_SECS", 0.05)

    async def scenario():
        await asyncio.wait_for(sleep_guard.before_sleep(), 1)
        monkeypatch.setattr(sleep_guard.rest_mode, "close_everything",
                            lambda *a: guard.append(("again",)) or asyncio.sleep(0, result=[]))
        await sleep_guard.after_wake()
    asyncio.run(scenario())
    assert guard[-1] == ("again",)


def test_a_failing_sweep_never_stops_the_pads_step(guard, monkeypatch):
    seen = []

    async def broken(*a):
        raise RuntimeError("boom")

    async def pads_off():
        seen.append("pads")
        return 0

    monkeypatch.setattr(sleep_guard.rest_mode, "close_everything", broken)
    monkeypatch.setattr(sleep_guard.bt_wake, "turn_off_pads", pads_off)
    asyncio.run(sleep_guard.before_sleep())
    assert seen == ["pads"]


def test_the_wake_sweeps_again_only_after_a_sleep(guard):
    async def scenario():
        await sleep_guard.after_wake()            # a start-up, not a wake
        assert guard == []
        await sleep_guard.before_sleep()
        await sleep_guard.after_wake()
        assert len(guard) == 2
    asyncio.run(scenario())


def test_the_rest_button_and_the_suspend_event_do_not_double_kill(monkeypatch):
    """Rest mode sweeps, then sleeps; the suspend event's sweep finds nothing left."""
    killed: list[int] = []

    resident = [Session(job=FakeJob(7), game_key="g", system_id="s", session_id=7)]

    class PM:
        foreground_session = None

        @property
        def background_sessions(self):
            return list(resident)

        async def kill(self, sid):
            killed.append(sid)
            for session in resident:
                session.job.terminate()
            resident.clear()

    monkeypatch.setattr(rest_mode, "process_manager", PM())

    async def suspend():
        await sleep_guard.before_sleep()     # what Windows' suspend event triggers
        return True, ""
    monkeypatch.setattr(rest_mode, "_suspend", suspend)

    async def scenario():
        await rest_mode.enter()
        assert killed == [7]
        rest_mode.woke()
    asyncio.run(scenario())
