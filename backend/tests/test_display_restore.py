"""After a wake, the confirmed display mode comes back if the TV handed another.

`platform/display.py` is replaced in every test (`fake_screen`): nothing here may
change a real screen.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.services import display
from backend.tests.fake_display import OUTPUT


@pytest.fixture(autouse=True)
def _prefs(monkeypatch, tmp_path):
    monkeypatch.setattr(display, "config_dir", lambda: tmp_path)


def _prefer(tmp_path, width, height, rate, output=OUTPUT):
    (tmp_path / display.PREFERENCE_FILE).write_text(
        '{"width": %d, "height": %d, "rate": %s, "output": "%s"}'
        % (width, height, rate, output.replace("\\", "\\\\")))


def test_the_confirmed_mode_is_put_back(fake_screen, tmp_path):
    fake_screen.current = {"width": 1280, "height": 720, "rate": 60.0}
    _prefer(tmp_path, 1920, 1080, 60.0)
    assert asyncio.run(display.restore_preferred()) is True
    assert fake_screen.changes() == [(1920, 1080, 60.0)]


def test_the_mode_already_on_screen_is_left_alone(fake_screen, tmp_path):
    _prefer(tmp_path, 1920, 1080, 60.0)
    assert asyncio.run(display.restore_preferred()) is False
    assert fake_screen.applied == []


def test_no_confirmed_mode_means_nothing_to_restore(fake_screen):
    assert asyncio.run(display.restore_preferred()) is False
    assert fake_screen.applied == []


def test_another_output_or_an_unadvertised_mode_is_not_forced(fake_screen, tmp_path):
    fake_screen.current = {"width": 1280, "height": 720, "rate": 60.0}
    _prefer(tmp_path, 1920, 1080, 60.0, output="\\\\.\\DISPLAY2")
    assert asyncio.run(display.restore_preferred()) is False
    _prefer(tmp_path, 3840, 2160, 60.0)
    assert asyncio.run(display.restore_preferred()) is False
    assert fake_screen.applied == []


def test_nothing_is_restored_under_a_resident_game(fake_screen, tmp_path):
    fake_screen.current = {"width": 1280, "height": 720, "rate": 60.0}
    fake_screen.playing = True
    _prefer(tmp_path, 1920, 1080, 60.0)
    assert asyncio.run(display.restore_preferred()) is False
