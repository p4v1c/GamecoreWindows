"""A press during Rest mode lights the screen at once.

On a Modern Standby PC Rest mode's sleep is the screen off with the PC
running (dev-log D34), and the wake is a pad press lighting it. Outside Rest
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
