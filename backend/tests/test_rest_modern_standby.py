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
    # A wake right after the display went off is input settling (D65): tested below.
    monkeypatch.setattr(rest_mode, "EARLY_WAKE_SECS", 0.0)
    monkeypatch.setattr(rest_mode, "_resleeps", 0)
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


# ── Input settling (dev-log D65) ─────────────────────────────────────────────
# Measured on the port's PC: Kernel-Power 506 (SC_MONITORPOWER), then 507
# "Input Touchpad" 0.97 s later; the backend took that display as the wake.


def test_a_display_back_on_right_after_the_rest_is_put_out_again(box, monkeypatch):
    seen, _ = box
    monkeypatch.setattr(rest_mode, "EARLY_WAKE_SECS", 60.0)

    async def scenario():
        await _rest()
        await resume.handle(sleep.DISPLAY_ON, sleep.MODERN_STANDBY, resume._event_lock())

    asyncio.run(scenario())
    assert seen.count("suspend") == 2
    assert resume.is_sleeping() and rest_mode.in_modern_standby()
    assert "power:resumed" not in seen and "interface in front" not in seen


def test_the_display_coming_back_again_and_again_is_a_real_wake(box, monkeypatch):
    seen, _ = box
    monkeypatch.setattr(rest_mode, "EARLY_WAKE_SECS", 60.0)

    async def scenario():
        await _rest()
        for _ in range(rest_mode.MAX_RESLEEPS + 1):
            await resume.handle(sleep.DISPLAY_ON, sleep.MODERN_STANDBY, resume._event_lock())

    asyncio.run(scenario())
    assert seen.count("suspend") == 1 + rest_mode.MAX_RESLEEPS
    assert seen.count("power:resumed") == 1 and not resume.is_sleeping()


def test_a_press_while_the_display_settles_is_not_a_wake(box, monkeypatch):
    seen, _ = box
    monkeypatch.setattr(resume, "PRESS_WAKE_GUARD_SECS", 0)
    monkeypatch.setattr(rest_mode, "EARLY_WAKE_SECS", 60.0)

    async def scenario():
        await _rest()
        await _press_and_settle()

    asyncio.run(scenario())
    assert resume.is_sleeping() and "power:resumed" not in seen


def test_no_pad_input_tells_windows_somebody_is_there_during_a_rest(box, monkeypatch):
    """Display required lights the display Rest put out, which ends the standby."""
    signals: list[str] = []
    monkeypatch.setattr(standby, "_signal_user_activity",
                        AsyncMock(side_effect=lambda: signals.append("signal")))
    monkeypatch.setattr(standby, "_last_activity_signal", 0.0)

    async def scenario():
        await _rest()
        await _press_and_settle()

    asyncio.run(scenario())
    assert signals == []


def test_the_standby_watcher_holds_nothing_during_a_rest(box, monkeypatch):
    entered: list[str] = []
    monkeypatch.setattr(standby, "_enter", AsyncMock(side_effect=entered.append))
    monkeypatch.setattr(standby, "_last_input", 0.0)

    async def scenario():
        await _rest()
        await standby._tick({"enabled": True, "sleep_mins": 1, "screensaver_mins": 1})

    asyncio.run(scenario())
    assert entered == []


def _idle_clock(monkeypatch, readings: list[float]) -> list[float]:
    """`_seconds_since_input` answers `readings` in turn, then the last one."""
    asked: list[float] = []

    def read():
        asked.append(readings[0] if len(readings) == 1 else readings.pop(0))
        return asked[-1]

    monkeypatch.setattr(rest_mode, "_seconds_since_input", read)
    monkeypatch.setattr(rest_mode, "POLL_SECS", 0.001)
    monkeypatch.setattr(rest_mode, "INPUT_QUIET_SECS", 0.01)
    return asked


def test_the_display_goes_off_only_once_input_is_still(monkeypatch):
    asked = _idle_clock(monkeypatch, [0.0, 0.005, 0.02])
    asyncio.run(rest_mode._wait_input_quiet())
    assert asked == [0.0, 0.005, 0.02]


def test_a_hand_left_on_the_touchpad_does_not_hold_the_rest_for_ever(monkeypatch):
    asked = _idle_clock(monkeypatch, [0.0])
    monkeypatch.setattr(rest_mode, "INPUT_QUIET_MAX_SECS", 0.05)
    asyncio.run(rest_mode._wait_input_quiet())
    assert asked and set(asked) == {0.0}


def test_pads_count_as_input_and_windows_unreadable_does_not_block(monkeypatch):
    def refused():
        raise OSError("no input info")

    monkeypatch.setattr(rest_mode.power, "seconds_since_input", refused)
    monkeypatch.setattr(standby, "_last_press", time.monotonic())
    assert rest_mode._seconds_since_input() < 1.0
    monkeypatch.setattr(standby, "_last_press", time.monotonic() - 100)
    assert rest_mode._seconds_since_input() == rest_mode.INPUT_QUIET_SECS
