"""Who is in front after a suspend, a resume or a launch.

Against fakes of `platform/window_control.py`: which windows are raised, in
which order, and what happens when there is nothing to raise. The Win32 calls
themselves are checked on real windows in test_platform_window_control.py.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.services import window_focus as wf
from backend.tests.fake_jobs import FakeJob


@pytest.fixture
def screen(monkeypatch):
    """Windows by owner pid, and every window brought to the front."""
    state = {"windows": {}, "shells": [], "raised": []}

    def top_level_windows(pids):
        return [w for w, pid in state["windows"].items() if pid in pids]

    def bring_to_front(hwnd):
        state["raised"].append(hwnd)
        return True

    monkeypatch.setattr(wf.window_control, "top_level_windows", top_level_windows)
    monkeypatch.setattr(wf.window_control, "windows_of_executables",
                        lambda names: list(state["shells"]))
    monkeypatch.setattr(wf.window_control, "bring_to_front", bring_to_front)
    monkeypatch.setattr(wf, "POLL_SECS", 0)
    return state


def test_several_game_windows_are_all_raised_and_the_topmost_last(screen):
    """One window per screen and player: all come back, the one that was on
    top before the suspend gets the focus."""
    screen["windows"] = {"p1w1": 10, "p1w2": 10, "p2w1": 10, "kiosk": 99}
    assert wf._activate_sync("melonds", FakeJob(10))
    assert screen["raised"] == ["p2w1", "p1w2", "p1w1"]


def test_a_resumed_game_with_no_window_reports_it(screen):
    screen["windows"] = {"kiosk": 99}
    assert not wf._activate_sync("pcsx2", FakeJob(10))
    assert screen["raised"] == []


def test_the_interface_is_raised_over_a_frozen_game(screen):
    """The largest window of the interface's process: the kiosk, not the HUD."""
    screen["shells"] = ["kiosk", "hud"]
    assert wf._raise_interface_sync("pcsx2")
    assert screen["raised"] == ["kiosk"]


def test_no_interface_window_is_said_rather_than_guessed(screen):
    assert not wf._raise_interface_sync("pcsx2")
    assert screen["raised"] == []


def test_a_new_game_window_is_brought_forward_once_it_opens(screen, monkeypatch):
    job = FakeJob(10)
    looks = {"n": 0}

    def top_level_windows(pids):
        looks["n"] += 1
        return ["game"] if looks["n"] >= 3 else []   # opens on the third look

    monkeypatch.setattr(wf.window_control, "top_level_windows", top_level_windows)
    assert asyncio.run(wf.present("pcsx2", job, timeout=5))
    assert screen["raised"] == ["game"]


def test_a_game_that_exits_before_its_window_stops_the_wait(screen):
    job = FakeJob(10)
    job.exit()
    assert wf.wait_for_window(job, timeout=60) == 0


def test_a_failure_while_raising_never_reaches_the_caller(monkeypatch):
    def broken(*a):
        raise OSError("access denied")
    monkeypatch.setattr(wf, "_activate_sync", broken)
    monkeypatch.setattr(wf, "_raise_interface_sync", broken)
    assert asyncio.run(wf.activate("pcsx2", FakeJob())) is False
    assert asyncio.run(wf.hide("pcsx2")) is False
    assert asyncio.run(wf.raise_interface()) is False
