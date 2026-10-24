"""Sleeping and waking: Windows' power events, and what a wake owes the player.

`platform/sleep.watch` is faked: the test fires the events from a thread, as
Windows' power thread does. The steps are recorded. Nothing sleeps or wakes.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest

from backend.services import resume
from backend.services.platform import sleep

S3, MS = sleep.S3, sleep.MODERN_STANDBY


@pytest.mark.parametrize("event, model, resting, sleeping, expected", [
    (sleep.SUSPEND, S3, False, False, resume.SLEEP),
    (sleep.SUSPEND, S3, False, True, None),          # already swept
    (sleep.RESUME, S3, False, True, resume.WAKE),
    (sleep.RESUME, S3, False, False, None),          # a wake with no sleep before it
    (sleep.DISPLAY_OFF, S3, True, False, None),       # S3 sleeps through SUSPEND only
    (sleep.DISPLAY_OFF, MS, True, False, resume.SLEEP),
    (sleep.DISPLAY_OFF, MS, False, False, None),      # standby's own screen-off
    (sleep.DISPLAY_ON, MS, False, True, resume.WAKE),
    (sleep.DISPLAY_ON, MS, False, False, None),       # the registration's first report
    (sleep.SUSPEND, MS, True, True, None),            # Modern Standby sending both
])
def test_what_each_power_event_means(event, model, resting, sleeping, expected):
    assert resume.classify(event, model=model, resting=resting, sleeping=sleeping) == expected


@pytest.fixture
def steps(monkeypatch):
    """Every step of a sleep and a wake, recorded in order."""
    seen: list[str] = []

    def note(name, result=None):
        async def step(*a, **kw):
            seen.append(name)
            return result
        return step

    monkeypatch.setattr(resume.rest_mode, "woke", lambda: seen.append("gate"))
    monkeypatch.setattr(resume.rest_mode, "is_resting", lambda: False)
    monkeypatch.setattr(resume.sleep_guard, "after_wake", note("sweep"))
    monkeypatch.setattr(resume.sleep_guard, "before_sleep", note("close"))
    monkeypatch.setattr(resume.standby, "exit_standby", note("screen"))
    monkeypatch.setattr(resume.display, "restore_preferred", note("display"))
    monkeypatch.setattr(resume.display_ready, "wait_for_display", note("link"))
    monkeypatch.setattr(resume.standby, "load_config", lambda: {"wake_settle_secs": 0})
    monkeypatch.setattr(resume.gamepad_monitor, "request_reprofile",
                        lambda: seen.append("pads"))
    monkeypatch.setattr(resume.bt_wake, "set_wake_permissions", note("bluetooth"))
    monkeypatch.setattr(resume.wake_source, "remember", lambda: seen.append("record"))
    monkeypatch.setattr(resume.wake_source, "report", lambda: seen.append("source"))
    broadcast = AsyncMock(side_effect=lambda ev, data: seen.append(ev))
    monkeypatch.setattr(resume.ws, "broadcast", broadcast)
    monkeypatch.setattr(resume, "_resumes", 0)
    monkeypatch.setattr(resume, "_sleeping", False)
    return seen


def test_a_wake_reopens_lights_announces_and_redetects(steps):
    asyncio.run(resume.on_resume())
    # The UI hears of the wake only once the link is up and the mode is back.
    assert steps == ["source", "sweep", "gate", "screen", "link", "display", "power:resumed",
                     "pads", "bluetooth", "record"]
    assert resume.resume_count() == 1


def test_one_failed_step_does_not_cost_the_others(steps, monkeypatch):
    async def broken():
        raise RuntimeError("ChangeDisplaySettingsEx refused")
    monkeypatch.setattr(resume.display, "restore_preferred", broken)
    asyncio.run(resume.on_resume())
    assert "power:resumed" in steps and "bluetooth" in steps and "pads" in steps


def test_a_sleep_covers_the_interface_before_closing_the_games(steps):
    asyncio.run(resume.going_to_sleep())
    assert steps == ["power:sleeping", "close"]
    assert resume.is_sleeping()


@pytest.fixture
def power_thread(monkeypatch):
    """A fake `sleep.watch`: `fire(event)` calls the handler from another thread
    and returns how long the handler held that thread."""
    box: dict = {"stopped": False}

    def watch(handler):
        box["handler"] = handler
        return lambda: box.update(stopped=True)

    def fire(event):
        held = {}

        def call():
            start = time.monotonic()
            box["handler"](event)
            held["secs"] = time.monotonic() - start
        thread = threading.Thread(target=call)
        thread.start()
        return thread, held

    monkeypatch.setattr(resume.sleep, "watch", watch)
    box["fire"] = fire
    return box


def _run_until(power_thread, monkeypatch, model, script):
    """Start resume.run() with `model`, then play `script(fire)` from a thread."""
    async def scenario():
        task = asyncio.create_task(resume.run())
        while "handler" not in power_thread:
            await asyncio.sleep(0.01)
        await asyncio.to_thread(script, power_thread["fire"])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    monkeypatch.setattr(resume.sleep, "power_model", lambda: model)
    asyncio.run(scenario())


def test_an_s3_sleep_holds_windows_until_the_sweep_is_done(steps, power_thread, monkeypatch):
    async def slow_close(*a, **kw):
        await asyncio.sleep(0.3)
        steps.append("close")
    monkeypatch.setattr(resume.sleep_guard, "before_sleep", slow_close)

    def script(fire):
        thread, held = fire(sleep.SUSPEND)
        thread.join(5)
        assert held["secs"] >= 0.25, "Windows would have slept before the sweep"
        assert steps[-1] == "close"
        thread, _ = fire(sleep.RESUME)
        thread.join(5)
        for _ in range(200):
            if resume.resume_count():
                break
            time.sleep(0.01)

    _run_until(power_thread, monkeypatch, S3, script)
    assert resume.resume_count() == 1
    assert steps.count("power:resumed") == 1
    assert steps[:4] == ["record", "power:sleeping", "close", "source"]
    assert power_thread["stopped"], "the registration outlived the backend"


def test_a_sweep_longer_than_windows_waits_lets_the_power_thread_go(steps, power_thread,
                                                                   monkeypatch):
    monkeypatch.setattr(resume, "SUSPEND_HOLD_SECS", 0.1)

    async def stuck(*a, **kw):
        await asyncio.sleep(1)
    monkeypatch.setattr(resume.sleep_guard, "before_sleep", stuck)

    def script(fire):
        thread, held = fire(sleep.SUSPEND)
        thread.join(5)
        assert held["secs"] < 0.5

    _run_until(power_thread, monkeypatch, S3, script)


def test_modern_standby_sleeps_on_the_screen_going_off_during_rest_mode(
        steps, power_thread, monkeypatch):
    resting = {"on": False}
    monkeypatch.setattr(resume.rest_mode, "is_resting", lambda: resting["on"])

    def script(fire):
        for event in (sleep.DISPLAY_ON, sleep.DISPLAY_OFF):   # first report, standby
            fire(event)[0].join(5)
        time.sleep(0.1)
        assert "close" not in steps, "standby's screen-off is not a sleep"
        resting["on"] = True
        fire(sleep.DISPLAY_OFF)[0].join(5)
        for _ in range(200):
            if "close" in steps:
                break
            time.sleep(0.01)
        fire(sleep.DISPLAY_ON)[0].join(5)
        for _ in range(200):
            if resume.resume_count():
                break
            time.sleep(0.01)

    _run_until(power_thread, monkeypatch, MS, script)
    assert steps.count("close") == 1 and steps.count("power:resumed") == 1


def test_a_refused_registration_is_a_warning_not_a_crash(monkeypatch, caplog):
    def refused(handler):
        raise OSError(5, "access denied")
    monkeypatch.setattr(resume.sleep, "watch", refused)
    asyncio.run(asyncio.wait_for(resume.run(), 5))
    assert "will not be noticed" in caplog.text


def test_the_wake_count_and_sleeping_flag_move_only_when_the_screen_is_back(steps, monkeypatch):
    seen = {}

    async def link(*a, **kw):
        seen["count_during_wait"] = resume.resume_count()
        seen["sleeping_during_wait"] = resume.is_sleeping()

    monkeypatch.setattr(resume.display_ready, "wait_for_display", link)

    async def scenario():
        await resume.announce_sleep()
        assert resume.is_sleeping()
        await resume.on_resume()

    asyncio.run(scenario())
    assert seen == {"count_during_wait": 0, "sleeping_during_wait": True}
    assert resume.resume_count() == 1 and not resume.is_sleeping()


def test_the_settle_delay_is_waited_before_the_announcement(steps, monkeypatch):
    monkeypatch.setattr(resume.standby, "load_config", lambda: {"wake_settle_secs": 0.2})
    stamps = {}
    real = resume.ws.broadcast

    async def stamp(ev, data):
        stamps[ev] = time.monotonic()
        await real(ev, data)

    monkeypatch.setattr(resume.ws, "broadcast", stamp)

    async def link(*a, **kw):
        stamps["link"] = time.monotonic()
    monkeypatch.setattr(resume.display_ready, "wait_for_display", link)
    asyncio.run(resume.on_resume())
    assert stamps["power:resumed"] - stamps["link"] >= 0.19
