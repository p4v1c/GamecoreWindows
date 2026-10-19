"""The HTTP contract for suspending a session, and the double-Home gesture.

`/api/games/session` is the shape every theme reads, so its backward
compatibility is load-bearing rather than polite: a front end from before this
feature reads the flat fields as "the game on my screen", and if a suspended
session filled them in, that front end would block its pad on every screen over
a game nobody can see. Which is the exact fault the reconnection guard in
hooks/useWebSocket.ts was written for, reintroduced through a different door.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import process_manager as pm    # noqa: E402

pytestmark = pytest.mark.usefixtures("posix_boundary")

_SYSTEM = {"id": "testpack", "label": "Test System", "kind": "emulator",
           "path": "/usr/bin/definitely-not-installed", "args": "",
           "romsPath": ""}


@pytest.fixture
def client(monkeypatch):
    from backend import main
    from backend.services import systems as systems_service
    monkeypatch.setattr(systems_service, "list_all", lambda: [_SYSTEM])
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def manager(monkeypatch):
    """The real manager, with its signals recorded and its disk untouched."""
    m = pm.process_manager
    monkeypatch.setattr(m, "_sessions", [])
    monkeypatch.setattr(m, "_save_state", lambda: None)
    monkeypatch.setattr(m, "_raise_interface", AsyncMock())
    monkeypatch.setattr(m, "_give_back_the_screen", AsyncMock())
    monkeypatch.setattr(pm.os, "killpg", lambda pgid, sig: None)
    monkeypatch.setattr(pm.os, "getpgid", lambda pid: pid)
    return m


class _Proc:
    def __init__(self, pid=4242):
        self.pid, self.returncode = pid, None


def _resident(manager, *, game_key, system_id, state="foreground", pid=4242):
    manager._seq += 1
    s = pm.Session(proc=_Proc(pid), game_key=game_key, system_id=system_id,
                   rom_path="", start_time=0.0, session_id=manager._seq,
                   state=state)
    manager._sessions.append(s)
    return s


# ── the shape ────────────────────────────────────────────────────────────────

def test_an_empty_box_still_answers_with_an_object(client, manager):
    assert client.get("/api/games/session").json() == {}


def test_the_session_on_the_screen_keeps_the_flat_shape(client, manager):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    body = client.get("/api/games/session").json()
    assert body["game_key"] == "zelda.iso"
    assert body["system_id"] == "dolphin"
    assert body["state"] == "foreground"
    assert isinstance(body["session"], int)


def test_a_suspended_session_leaves_the_flat_fields_empty(client, manager):
    """The compatibility that matters. An older front end reads `game_key` as
    "a game owns my screen" and blocks the pad on it — so a suspended session
    must not put anything there, or backgrounding a game would freeze the
    interface for exactly the client that cannot know why."""
    _resident(manager, game_key="zelda.iso", system_id="dolphin",
              state="background")
    body = client.get("/api/games/session").json()
    assert "game_key" not in body
    assert [s["game_key"] for s in body["background"]] == ["zelda.iso"]
    assert body["background"][0]["state"] == "background"


def test_both_at_once_are_told_apart(client, manager):
    _resident(manager, game_key="zelda.iso", system_id="dolphin",
              state="background", pid=11)
    _resident(manager, game_key="stremio", system_id="stremio", pid=22)
    body = client.get("/api/games/session").json()
    assert body["game_key"] == "stremio"
    assert body["kind"] == "app", "a tile with no ROM launches as its own id"
    assert [s["game_key"] for s in body["background"]] == ["zelda.iso"]


# ── the endpoints ────────────────────────────────────────────────────────────

def test_backgrounding_and_resuming_move_the_session(client, manager):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")

    body = client.post("/api/games/background").json()
    assert body["ok"] is True
    assert "game_key" not in body
    assert [s["game_key"] for s in body["background"]] == ["zelda.iso"]

    body = client.post("/api/games/foreground").json()
    assert body["game_key"] == "zelda.iso"
    assert "background" not in body


def test_backgrounding_nothing_is_a_409_that_says_so(client, manager):
    r = client.post("/api/games/background")
    assert r.status_code == 409
    assert "Nothing is running" in r.json()["detail"]


def test_resuming_nothing_is_a_409(client, manager):
    r = client.post("/api/games/foreground")
    assert r.status_code == 409


def test_a_suspended_game_refuses_a_different_launch_by_name(client, manager):
    _resident(manager, game_key="zelda.iso", system_id="dolphin",
              state="background")
    r = client.post("/api/games/launch",
                    json={"system_id": "testpack", "game_key": "mario.iso"})
    assert r.status_code == 409
    assert "zelda.iso" in r.json()["detail"]
    assert "Close it" in r.json()["detail"]


def test_launching_the_suspended_game_resumes_it(client, manager):
    held = _resident(manager, game_key="zelda.iso", system_id="testpack",
                     state="background")
    r = client.post("/api/games/launch",
                    json={"system_id": "testpack", "game_key": "zelda.iso"})
    assert r.status_code == 200
    assert r.json()["resumed"] is True
    assert r.json()["session"] == held.session_id
    assert manager.foreground_session is held


def test_something_on_the_screen_still_refuses_a_launch(client, manager):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    r = client.post("/api/games/launch", json={"system_id": "testpack"})
    assert r.status_code == 409


def test_kill_still_works_with_no_body_at_all(client, manager):
    """Every caller that predates the second slot posts nothing."""
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    assert client.post("/api/games/kill").json() == {"ok": True}


def test_kill_can_name_the_suspended_run(client, manager):
    held = _resident(manager, game_key="zelda.iso", system_id="dolphin",
                     state="background", pid=11)
    _resident(manager, game_key="stremio", system_id="stremio", pid=22)
    assert client.post("/api/games/kill",
                       json={"session": held.session_id}).json() == {"ok": True}


# ── the gesture ──────────────────────────────────────────────────────────────

def test_double_home_suspends_the_game_instead_of_killing_it(manager,
                                                             monkeypatch):
    """It used to kill, and that is a destructive default two accidental
    presses away. Nothing is lost by pressing it twice by mistake now."""
    import asyncio
    from backend.services import gamepad_monitor as gm

    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    killed = AsyncMock()
    monkeypatch.setattr(manager, "kill", killed)
    from backend import ws as ws_module
    broadcast = AsyncMock()
    monkeypatch.setattr(ws_module, "broadcast", broadcast)
    monkeypatch.setattr(gm, "_last_guide_press", 0.0)
    # A clock we drive: the two presses have to be far enough apart to clear
    # the debounce and close enough to land inside the double-press window.
    clock = {"t": 100.0}
    monkeypatch.setattr(gm.time, "monotonic", lambda: clock["t"])

    async def scenario():
        # First press arms, second press inside the window acts.
        await gm._on_guide_pressed()
        clock["t"] += (gm.DEBOUNCE + gm.DOUBLE_PRESS_WINDOW) / 2
        await gm._on_guide_pressed()

    asyncio.run(scenario())

    killed.assert_not_awaited()
    assert manager.foreground_session is None
    assert [s.game_key for s in manager.background_sessions] == ["zelda.iso"]
    guide = [c for c in broadcast.await_args_list if c.args[0] == "gp:guide"]
    assert guide and guide[0].args[1]["action"] == "backgrounded"
