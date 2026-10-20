"""The enforcer that makes an app with no fullscreen flag borderless fullscreen.

It existed, it was correct, and for months nothing called it: the tile's
`fullscreen` block was dropped when an app moved to a wrapper script, and
`enforce()` returns silently when the block is missing. Silence is right for
every other tile, which is why the config half is tested here.

The window half runs against fakes of `window_control`; the real Win32 calls
are checked in test_platform_window_control.py.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import fullscreen_enforcer as fe  # noqa: E402
from backend.tests.fake_jobs import FakeJob  # noqa: E402


class Screen:
    """One window, which covers its monitor after `takes` attempts."""

    def __init__(self, monkeypatch, *, window=77, takes=1):
        self.window, self.takes = window, takes
        self.attempts, self.raised = 0, []
        monkeypatch.setattr(fe, "RETRY_SECS", 0)
        monkeypatch.setattr(fe.window_focus, "wait_for_window",
                            lambda job, timeout: self.window)
        monkeypatch.setattr(fe.window_control, "covers_its_monitor",
                            lambda hwnd: self.attempts >= self.takes)
        monkeypatch.setattr(fe.window_control, "make_borderless_fullscreen", self._make)
        monkeypatch.setattr(fe.window_control, "bring_to_front", self.raised.append)

    def _make(self, hwnd):
        self.attempts += 1
        return True


# ── the window ───────────────────────────────────────────────────────────────

def test_the_window_is_made_borderless_then_brought_to_the_front(monkeypatch):
    screen = Screen(monkeypatch)
    assert fe._enforce_sync("stremio", FakeJob(), 5) is True
    assert screen.attempts == 1
    assert screen.raised == [77]


def test_an_app_that_resizes_itself_is_fullscreened_again(monkeypatch):
    """An app that sizes its window once its content loads undoes the first try."""
    screen = Screen(monkeypatch, takes=3)
    assert fe._enforce_sync("stremio", FakeJob(), 5) is True
    assert screen.attempts == 3


def test_a_window_that_never_covers_the_screen_is_given_up_on(monkeypatch):
    screen = Screen(monkeypatch, takes=99)
    assert fe._enforce_sync("stremio", FakeJob(), 5) is False
    assert screen.attempts == fe.ATTEMPTS
    assert screen.raised == []


def test_no_window_is_a_warning_not_an_error(monkeypatch):
    screen = Screen(monkeypatch, window=0)
    assert fe._enforce_sync("stremio", FakeJob(), 5) is False
    assert screen.attempts == 0


# ── the config games.py hands over ───────────────────────────────────────────

def _run(cfg, monkeypatch, job=None):
    """Call enforce() with the blocking half stubbed, and report its arguments."""
    seen = {}

    def fake_sync(system_id, job, timeout_s):
        seen.update(system_id=system_id, job=job, timeout_s=timeout_s)
        return True

    monkeypatch.setattr(fe, "_enforce_sync", fake_sync)
    asyncio.run(fe.enforce("stremio", cfg, job if job is not None else FakeJob()))
    return seen


def test_the_block_a_pack_ships_reaches_the_enforcer(monkeypatch):
    """`wm_class` is the Linux key: still accepted, no longer needed (the
    window is the job's own)."""
    job = FakeJob()
    seen = _run({"wm_class": ["stremio", "Stremio"], "timeout_s": 60}, monkeypatch, job)
    assert seen == {"system_id": "stremio", "job": job, "timeout_s": 60.0}


def test_a_missing_or_broken_timeout_falls_back_rather_than_raising(monkeypatch):
    assert _run({"wm_class": ["stremio"]}, monkeypatch)["timeout_s"] == fe.DEFAULT_TIMEOUT_SECS
    assert _run({"timeout_s": "soon"}, monkeypatch)["timeout_s"] == fe.DEFAULT_TIMEOUT_SECS


def test_an_empty_block_does_nothing_at_all(monkeypatch):
    """The silence that hid the regression for months. It is correct, and it
    is why test_catalog_tiles.py asserts the block exists at the source."""
    assert _run({}, monkeypatch) == {}


def test_a_failure_in_the_window_half_never_reaches_the_launch(monkeypatch):
    def broken(*a):
        raise OSError("the window went away")
    monkeypatch.setattr(fe, "_enforce_sync", broken)
    assert asyncio.run(fe.enforce("stremio", {"timeout_s": 1}, FakeJob())) is False
