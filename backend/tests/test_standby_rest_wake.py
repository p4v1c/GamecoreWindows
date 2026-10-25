"""A press during Rest mode lights the screen at once.

A Rest mode sleep that Windows did not take leaves the PC running with its
screen off, and the wake is a pad press lighting it. Outside Rest
mode a press lights a dark screen only after `_WAKE_AFTER_SILENCE` (60 s): a
player pressing again 20 s after choosing Rest mode stayed in the dark.
"""
import time

import pytest

from backend.services import resume, rest_mode, standby


@pytest.fixture
def presses(monkeypatch):
    lit: list[bool] = []

    async def screen(on):
        lit.append(on)

    def spawn(coro):
        # Run the screen call inline; anything else is not this test's business.
        if coro.cr_code is screen.__code__:
            lit.append(coro.cr_frame.f_locals["on"])
        coro.close()

    monkeypatch.setattr(standby, "_screen", screen)
    monkeypatch.setattr(standby, "_spawn", spawn)
    monkeypatch.setattr(standby, "_state", "active")
    monkeypatch.setattr(standby, "_last_activity_signal", time.monotonic())
    monkeypatch.setattr(standby, "_last_press", time.monotonic() - 20)
    monkeypatch.setattr(rest_mode, "_resting", False)
    monkeypatch.setattr(resume, "_sleeping", False)
    monkeypatch.setattr(resume, "_slept_at", 0.0)
    return lit


def test_a_press_soon_after_another_does_not_relight_outside_rest_mode(presses):
    standby.on_input()
    assert presses == []


def test_a_press_during_rest_mode_lights_the_screen_at_once(presses, monkeypatch):
    monkeypatch.setattr(rest_mode, "_resting", True)
    standby.on_input()
    assert presses == [True]


def test_a_press_while_asleep_lights_the_screen_once_rest_mode_has_reopened(presses,
                                                                            monkeypatch):
    """Rest mode reopens launches after 60 s; the sleep still waits for its wake."""
    monkeypatch.setattr(resume, "_sleeping", True)
    standby.on_input()
    assert presses == [True]


def test_the_press_that_chose_rest_mode_does_not_light_the_screen(presses, monkeypatch):
    monkeypatch.setattr(rest_mode, "_resting", True)
    monkeypatch.setattr(resume, "_sleeping", True)
    monkeypatch.setattr(resume, "_slept_at", time.monotonic())
    standby.on_input()
    assert presses == []
