"""Rest mode on a Modern Standby PC: screen dark, PC running, a press wakes it (dev-log D56).

Windows announces neither the sleep nor the wake there (the display never goes
off), so Rest mode and the first press do. Every Windows call is faked.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from backend.services import launch, resume, rest_mode, standby
from backend.services.platform import sleep


@pytest.fixture
def box(monkeypatch):
    seen: list[str] = []

    def note(name):
        async def step(*a, **kw):
            seen.append(name)
        return step

    state = {"detail": sleep.DARK_REST}

    async def suspend():
        return True, state["detail"]

    monkeypatch.setattr(rest_mode, "_settle_launch", note("settle"))
    monkeypatch.setattr(rest_mode, "close_everything", AsyncMock(return_value=[]))
    monkeypatch.setattr(rest_mode, "_suspend", suspend)
    monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 3600)
    monkeypatch.setattr(launch, "close_gate", lambda reason: seen.append("gate closed"))
    monkeypatch.setattr(launch, "open_gate", lambda: seen.append("gate open"))
    monkeypatch.setattr(resume.sleep_guard, "before_sleep", note("close"))
    monkeypatch.setattr(resume.sleep_guard, "after_wake", note("sweep"))
    monkeypatch.setattr(resume.standby, "exit_standby", note("screen on"))
    monkeypatch.setattr(resume.display, "restore_preferred", note("display"))
    monkeypatch.setattr(resume.display_ready, "wait_for_display", note("link"))
    monkeypatch.setattr(resume.standby, "load_config", lambda: {"wake_settle_secs": 0})
    monkeypatch.setattr(resume.gamepad_monitor, "request_reprofile", lambda: None)
    monkeypatch.setattr(resume.bt_wake, "set_wake_permissions", note("bluetooth"))
    monkeypatch.setattr(resume.wake_source, "remember", lambda: None)
    monkeypatch.setattr(resume.wake_source, "report", lambda: None)
    monkeypatch.setattr(resume.ws, "broadcast", AsyncMock(side_effect=lambda ev, data: seen.append(ev)))
    monkeypatch.setattr(resume, "_sleeping", False)
    monkeypatch.setattr(resume, "_lock", None)
    monkeypatch.setattr(rest_mode, "_resting", False)
    monkeypatch.setattr(rest_mode, "_dark", False)
    monkeypatch.setattr(standby, "_state", "active")
    monkeypatch.setattr(standby, "_last_activity_signal", time.monotonic())
    monkeypatch.setattr(standby, "_last_press", time.monotonic())
    yield seen, state
    rest_mode.woke()


async def _press_and_settle() -> None:
    standby.on_input()
    for _ in range(50):
        await asyncio.sleep(0.01)


def test_a_dark_rest_covers_the_interface_and_closes_games(box):
    seen, _ = box

    async def scenario():
        await rest_mode.enter()
        if rest_mode._reopen_task:
            rest_mode._reopen_task.cancel()

    asyncio.run(scenario())
    assert rest_mode.is_dark()
    assert resume.is_sleeping()
    assert "power:sleeping" in seen and "close" in seen


def test_a_press_ends_the_dark_rest_with_the_whole_wake(box):
    seen, _ = box

    async def scenario():
        await rest_mode.enter()
        await _press_and_settle()

    asyncio.run(scenario())
    assert not rest_mode.is_dark()
    assert not resume.is_sleeping()
    assert seen.index("screen on") < seen.index("power:resumed")
    assert seen[-1] != "gate closed" and "gate open" in seen


def test_a_second_press_does_not_wake_twice(box):
    seen, _ = box

    async def scenario():
        await rest_mode.enter()
        await _press_and_settle()
        await _press_and_settle()

    asyncio.run(scenario())
    assert seen.count("power:resumed") == 1


def test_an_s3_rest_waits_for_windows_to_announce_the_wake(box):
    seen, state = box
    state["detail"] = "s3"

    async def scenario():
        await rest_mode.enter()
        if rest_mode._reopen_task:
            rest_mode._reopen_task.cancel()

    asyncio.run(scenario())
    assert not rest_mode.is_dark()
    assert "power:sleeping" not in seen


def test_a_dark_rest_has_no_timer_that_would_end_it_under_the_player(box, monkeypatch):
    seen, _ = box
    monkeypatch.setattr(rest_mode, "REOPEN_AFTER_SECS", 0.01)

    async def scenario():
        await rest_mode.enter()
        await asyncio.sleep(0.1)
        assert rest_mode.is_dark()
        await _press_and_settle()

    asyncio.run(scenario())
    assert seen.count("power:resumed") == 1
