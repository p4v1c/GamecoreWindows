"""A game starting is the end of standby, whoever started it.

Found chasing the report rather than reported: nothing on the launch path
touched standby. `run()` treats a running game as activity — it keeps pushing
`_last_input` forward so the box cannot fall asleep DURING a game — but it never
undoes a standby that had already begun.

Which is reachable. The pad is not the only thing that can start a game: the web
UI on a phone posts the same endpoint. Box asleep, launch from the sofa, and the
emulator comes up behind a screen the backend has switched off through DPMS,
with `_state` still saying "sleep".

That was already wrong. It became worse with the input guard, because the guard
reads the same state: the front end would have swallowed every press, gp:guide
included, and gp:guide is the only way to end a game. A black screen, a running
emulator, and a pad that cannot stop it.

So the launch wakes the box, before the emulator rather than after — the screen
has to be back before there is anything to see on it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.services import standby

@pytest.fixture
def screen_calls(monkeypatch):
    """Swallow xset/cpupower and record what standby tried to run."""
    calls: list[tuple[str, ...]] = []

    async def fake_run_cmd(*argv, **kw):
        calls.append(argv)
        return True

    monkeypatch.setattr(standby, "_run_cmd", fake_run_cmd)
    return calls


@pytest.fixture
def launcher(monkeypatch, screen_calls):
    from fastapi.testclient import TestClient
    from backend.services import systems as systems_service
    from backend import main

    # An emulator that is not there: the launch fails at the last step, which is
    # after everything this file is about. The wake must already have happened.
    ghost = {"id": "ghost", "label": "Ghost", "kind": "emulator",
             "path": r"C:\definitely\not-installed.exe", "args": "", "romsPath": ""}
    monkeypatch.setattr(systems_service, "list_all", lambda: [ghost])
    with TestClient(main.app) as c:
        yield c


@pytest.mark.parametrize("stage", ["screensaver", "sleep"])
def test_launching_a_game_wakes_the_box(launcher, stage):
    standby._state = stage
    launcher.post("/api/games/launch", json={"system_id": "ghost"})
    assert standby.get_state() == "active"


def test_launching_puts_the_screen_back_on(launcher, screen_calls):
    # Not just the flag: `sleep` cut the panel through DPMS, and a state that
    # says "active" over a screen that is still off is the exact disagreement
    # exit_standby() was written to be unconditional about.
    standby._state = "sleep"
    screen_calls.clear()
    launcher.post("/api/games/launch", json={"system_id": "ghost"})
    assert any("dpms" in " ".join(c) and "on" in c for c in screen_calls), screen_calls


def test_a_refused_launch_still_wakes(launcher):
    # This one fails with 503 — the emulator is not installed. The screen must
    # come back regardless, or the player is left staring at a black television
    # wondering whether anything happened.
    standby._state = "sleep"
    r = launcher.post("/api/games/launch", json={"system_id": "ghost"})
    assert r.status_code == 503, r.text
    assert standby.get_state() == "active"


def test_a_launch_for_a_system_that_does_not_exist_changes_nothing(launcher):
    # 404 before anything happens. Waking on a request that was never a launch
    # would let any stray poll keep the box awake for ever.
    standby._state = "sleep"
    r = launcher.post("/api/games/launch", json={"system_id": "no-such-system"})
    assert r.status_code == 404
    assert standby.get_state() == "sleep"


def test_waking_is_idempotent_when_the_box_was_already_awake(launcher):
    standby._state = "active"
    launcher.post("/api/games/launch", json={"system_id": "ghost"})
    assert standby.get_state() == "active"


def test_the_idle_clock_restarts_from_the_launch(launcher):
    # `run()` holds the clock while a game runs, but the game has to get as far
    # as running first. A launch landing seconds before the sleep threshold used
    # to leave the timer where it was.
    standby._state = "active"
    standby._last_input = 0.0
    launcher.post("/api/games/launch", json={"system_id": "ghost"})
    import time
    assert standby._last_input > time.monotonic() - 5


def teardown_function():
    standby._state = "active"
