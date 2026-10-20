"""Is the backend on the TV's desktop: the check a launch makes first.

The logic runs against fakes of `platform/console_session.py`; the real WTS
calls run at the end, against the session this suite runs in.
"""
from __future__ import annotations

import getpass

import pytest

from backend.services import session
from backend.services.platform import console_session


@pytest.fixture
def desk(monkeypatch):
    state = {"own": 2, "console": 2, "user": "player"}
    monkeypatch.setattr(console_session, "own_session_id", lambda: state["own"])
    monkeypatch.setattr(console_session, "console_session_id", lambda: state["console"])
    monkeypatch.setattr(console_session, "session_user", lambda sid: state["user"])
    monkeypatch.setattr(console_session, "primary_display", lambda: r"\\.\DISPLAY1")
    return state


def test_the_console_session_launches(desk):
    assert session.launch_blocker() == ""
    assert session.desktop() == {"session": 2, "console_session": 2,
                                 "user": "player", "display": r"\\.\DISPLAY1"}


def test_a_backend_in_another_session_refuses(desk):
    """A service in session 0, or the console taken by another session: the
    game would open where nobody sees it."""
    desk["own"] = 0
    assert "not running on the TV" in session.launch_blocker()


def test_nobody_logged_on_refuses(desk):
    desk["user"] = ""
    assert "Nobody is logged on" in session.launch_blocker()
    desk["user"], desk["console"] = "player", console_session.NO_CONSOLE_SESSION
    assert "Nobody is logged on" in session.launch_blocker()


def test_a_dark_screen_does_not_refuse(desk, monkeypatch):
    """A TV that is off still lists its output; a launch from the phone wakes
    the screen itself."""
    monkeypatch.setattr(console_session, "primary_display", lambda: "")
    assert session.launch_blocker() == ""


# ── the real calls ───────────────────────────────────────────────────────────

def test_this_process_session_belongs_to_the_user_running_it():
    own = console_session.own_session_id()
    assert own != console_session.NO_CONSOLE_SESSION
    assert console_session.session_user(own).lower() == getpass.getuser().lower()


def test_session_zero_has_no_user():
    assert console_session.session_user(0) == ""


def test_the_primary_display_is_named_like_a_display():
    display = console_session.primary_display()
    assert display == "" or display.startswith(r"\\.\DISPLAY")
