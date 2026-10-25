"""Rest mode sleeps for real, Modern Standby included (dev-log D62).

The interface is covered, games closed and pads turned off before the sleep is
asked for, since a Modern Standby PC freezes the backend within a second of
its display going off. The wake is the display coming back on, or a press the
backend sees while asleep. Every Windows call is faked.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from backend.services import launch, resume, rest_mode, standby
from backend.services.errors import ServiceError
from backend.services.platform import sleep


@pytest.fixture
def box(monkeypatch):
    seen: list[str] = []

    def note(name):
        async def step(*a, **kw):
            seen.append(name)
        return step

    state = {"answer": (True, sleep.MODERN_STANDBY)}

    async def suspend():
        seen.append("suspend")
        return state["answer"]

    monkeypatch.setattr(rest_mode, "_settle_launch", note("settle"))
    monkeypatch.setattr(rest_mode, "close_everything", AsyncMock(return_value=[]))
    monkeypatch.setattr(rest_mode, "_suspend", suspend)
    monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 3600)
    monkeypatch.setattr(launch, "close_gate", lambda reason: seen.append("gate closed"))
    monkeypatch.setattr(launch, "open_gate", lambda: seen.append("gate open"))
    monkeypatch.setattr(resume.sleep_guard, "before_sleep", note("close and pads off"))
    monkeypatch.setattr(resume.sleep_guard, "after_wake", note("sweep"))
    monkeypatch.setattr(resume.standby, "exit_standby", note("screen on"))
    monkeypatch.setattr(resume.display, "restore_preferred", note("display"))
    monkeypatch.setattr(resume.display_ready, "wait_for_display", note("link"))
    monkeypatch.setattr(resume.standby, "load_config", lambda: {"wake_settle_secs": 0})
    monkeypatch.setattr(resume.gamepad_monitor, "request_reprofile", lambda: None)
    monkeypatch.setattr(resume.bt_wake, "set_wake_permissions", note("bluetooth"))
    monkeypatch.setattr(resume.window_focus, "raise_interface", note("interface in front"))
    monkeypatch.setattr(resume.wake_source, "remember", lambda: None)
    monkeypatch.setattr(resume.wake_source, "report", lambda: None)
    monkeypatch.setattr(resume.ws, "broadcast", AsyncMock(side_effect=lambda ev, data: seen.append(ev)))
    monkeypatch.setattr(resume, "_sleeping", False)
    monkeypatch.setattr(resume, "_slept_at", 0.0)
    monkeypatch.setattr(resume, "_lock", None)
    monkeypatch.setattr(rest_mode, "_resting", False)
    monkeypatch.setattr(rest_mode, "_modern_standby", False)
    monkeypatch.setattr(standby, "_state", "active")
    monkeypatch.setattr(standby, "_last_activity_signal", time.monotonic())
    monkeypatch.setattr(standby, "_last_press", time.monotonic())
    yield seen, state
    rest_mode.woke()


async def _press_and_settle() -> None:
    standby.on_input()
    for _ in range(50):
        await asyncio.sleep(0.01)


async def _rest() -> None:
    await rest_mode.enter()
    if rest_mode._reopen_task:
        rest_mode._reopen_task.cancel()


def test_the_interface_is_covered_and_the_pads_off_before_the_sleep_is_asked(box):
    seen, _ = box
    asyncio.run(_rest())
    assert seen.index("power:sleeping") < seen.index("suspend")
    assert seen.index("close and pads off") < seen.index("suspend")
    assert rest_mode.in_modern_standby() and resume.is_sleeping()


def test_the_display_coming_back_is_the_whole_wake(box):
    seen, _ = box

    async def scenario():
        await _rest()
        await resume.handle(sleep.DISPLAY_ON, sleep.MODERN_STANDBY, resume._event_lock())

    asyncio.run(scenario())
    assert not resume.is_sleeping() and not rest_mode.in_modern_standby()
    assert seen.index("screen on") < seen.index("power:resumed") < seen.index("interface in front")
    assert "gate open" in seen


def test_the_press_that_chose_rest_mode_does_not_wake_the_box(box):
    seen, _ = box

    async def scenario():
        await _rest()
        await _press_and_settle()

    asyncio.run(scenario())
    assert resume.is_sleeping()
    assert "power:resumed" not in seen and "screen on" not in seen


def test_a_press_seen_while_asleep_is_the_wake(box, monkeypatch):
    """A display that never went off sends no display event: the press wakes."""
    seen, _ = box
    monkeypatch.setattr(resume, "PRESS_WAKE_GUARD_SECS", 0)

    async def scenario():
        await _rest()
        await _press_and_settle()
        await _press_and_settle()

    asyncio.run(scenario())
    assert not resume.is_sleeping()
    assert seen.count("power:resumed") == 1


def test_a_modern_standby_rest_has_no_timer_reopening_launches_in_its_sleep(box, monkeypatch):
    seen, _ = box
    monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 0.01)

    async def scenario():
        await rest_mode.enter()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert rest_mode.is_resting() and "gate open" not in seen


def test_an_s3_rest_announces_the_same_way_and_waits_for_windows(box):
    seen, state = box
    state["answer"] = (True, "s3")
    asyncio.run(_rest())
    assert seen.index("power:sleeping") < seen.index("suspend")
    assert not rest_mode.in_modern_standby()


def test_a_refused_sleep_takes_the_announcement_back(box):
    seen, state = box
    state["answer"] = (False, "no sleep state")
    with pytest.raises(ServiceError) as refused:
        asyncio.run(_rest())
    assert refused.value.status == 503
    assert not resume.is_sleeping() and not rest_mode.is_resting()
    assert seen[-1] == "gate open"
