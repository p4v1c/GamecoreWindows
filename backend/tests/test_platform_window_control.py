"""Real windows of another process: found by job, made borderless, brought forward.

The "game" is a small Tk window in a real job, like a game the backend starts;
this test process plays the backend, a background process that does not own
the foreground. Needs the interactive desktop the suite runs on (the PC and
the CI runner both have one).
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from backend.services.platform import jobs, window_control

GAME = ("import tkinter as tk\n"
        "root = tk.Tk()\n"
        "root.title('GameCore test window')\n"
        "root.geometry('320x200+40+40')\n"
        "root.mainloop()\n")


def _first_window(job: jobs.GameJob, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = window_control.top_level_windows(set(job.pids()))
        if windows and window_control._responds(windows[0]):
            return windows[0]
        time.sleep(0.1)
    return 0


@pytest.fixture
def game():
    job = jobs.spawn([sys.executable, "-c", GAME], env=None, stdout=subprocess.DEVNULL)
    hwnd = _first_window(job)
    yield job, hwnd
    job.terminate()
    job.close()


def test_the_window_is_found_by_the_processes_of_its_job(game):
    job, hwnd = game
    assert hwnd, "the game's window was not found"
    assert window_control.top_level_windows({4}) == [], "System owns no app window"


def test_a_framed_window_does_not_cover_its_monitor(game):
    _job, hwnd = game
    assert not window_control.covers_its_monitor(hwnd)


def test_borderless_fullscreen_covers_the_monitor_without_a_frame(game):
    _job, hwnd = game
    assert window_control.make_borderless_fullscreen(hwnd)
    deadline = time.monotonic() + 5
    while not window_control.covers_its_monitor(hwnd) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert window_control.covers_its_monitor(hwnd)


def test_a_background_process_brings_the_window_to_the_front(game):
    _job, hwnd = game
    assert window_control.bring_to_front(hwnd)
    assert window_control.is_foreground(hwnd)


@pytest.fixture
def other():
    """A second window: the interface, in these tests."""
    job = jobs.spawn([sys.executable, "-c", GAME], env=None, stdout=subprocess.DEVNULL)
    hwnd = _first_window(job)
    yield hwnd
    job.terminate()
    job.close()


def test_the_window_raised_before_the_freeze_stays_in_front(game, other):
    """What suspending a game does: the interface first, then the freeze."""
    job, hwnd = game
    assert window_control.bring_to_front(hwnd)
    assert window_control.bring_to_front(other)
    assert job.suspend()
    try:
        time.sleep(0.3)
        assert window_control.is_foreground(other)
    finally:
        job.resume()


def test_raising_over_a_frozen_window_never_hangs(game, other):
    """The other order: attached to a frozen thread, the call waited for as
    long as the game stayed frozen. Whatever Windows decides, it returns."""
    job, hwnd = game
    assert window_control.bring_to_front(hwnd)
    assert job.suspend()
    try:
        started = time.monotonic()
        window_control.bring_to_front(other)
        assert time.monotonic() - started < 2, "blocked on the frozen game"
    finally:
        job.resume()


def test_the_interface_is_found_by_its_executable(game):
    """`windows_of_executables` is how the interface's own window is found."""
    _job, hwnd = game
    names = window_control.windows_of_executables(("python.exe",))
    assert hwnd in names
    assert window_control.windows_of_executables(("no-such-program.exe",)) == []


def test_a_window_that_is_gone_is_refused_not_raised():
    assert not window_control.bring_to_front(0x7FFFFFF0)
    assert not window_control.make_borderless_fullscreen(0x7FFFFFF0)
