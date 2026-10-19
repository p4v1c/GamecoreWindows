"""Defects found by an independent audit of the background-session work.

Each test here failed on the code as first written. They are kept because the
faults they name are the expensive kind: a refusal that reaches the player as a
crash, a "close this" that closes something else, and a box that reports a game
as frozen when it is still running.
"""
from __future__ import annotations

import asyncio
import signal
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
           "path": "/usr/bin/definitely-not-installed", "args": "", "romsPath": ""}


class _Proc:
    def __init__(self, pid=4242):
        self.pid, self.returncode = pid, None


@pytest.fixture
def client(monkeypatch):
    from backend import main
    from backend.services import systems as systems_service
    monkeypatch.setattr(systems_service, "list_all", lambda: [_SYSTEM])
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def manager(monkeypatch):
    m = pm.process_manager
    monkeypatch.setattr(m, "_sessions", [])
    monkeypatch.setattr(m, "_save_state", lambda: None)
    monkeypatch.setattr(m, "_raise_interface", AsyncMock())
    monkeypatch.setattr(m, "_give_back_the_screen", AsyncMock())
    monkeypatch.setattr(pm.os, "killpg", lambda pgid, sig: None)
    monkeypatch.setattr(pm.os, "getpgid", lambda pid: pid)
    return m


def _resident(manager, *, game_key, system_id, state="foreground", pid=4242,
              rom_path=""):
    manager._seq += 1
    s = pm.Session(proc=_Proc(pid), game_key=game_key, system_id=system_id,
                   rom_path=rom_path, start_time=0.0, session_id=manager._seq,
                   state=state)
    manager._sessions.append(s)
    return s


# ── 1. the refusal the player actually meets ─────────────────────────────────

def test_a_launch_past_the_cap_is_a_named_409_and_not_a_crash(client, manager):
    """`ProcessManager.launch` refuses a resident session past the cap by
    raising `SessionConflict`. The router only caught FileNotFoundError and
    PermissionError, so that refusal left as an unhandled 500 — the player got a
    crash instead of the sentence naming what to close, and the sentence was
    written specifically so they would know.

    Filled to `MAX_SESSIONS` rather than to two: `_resident_cap` reads the cap
    off the box, so a test that counts to a literal is testing this machine's
    RAM rather than the refusal."""
    # Applications still share the general resident cap; games now have the
    # stricter one-game gate, which is a different refusal tested elsewhere.
    held = [f"held{i}" for i in range(pm.MAX_SESSIONS)]
    for i, key in enumerate(held):
        _resident(manager, game_key=key, system_id=key,
                  state="background", pid=11 * (i + 1))

    r = client.post("/api/games/launch",
                    json={"system_id": "testpack", "game_key": "new.iso"})
    assert r.status_code == 409, f"got {r.status_code}, not a refusal"
    for key in held:
        assert key in r.json()["detail"], f"{key} is holding a slot and goes unnamed"


# ── 2. "close it" must close the thing that was asked for ────────────────────

def test_a_failed_suspend_never_destroys_a_suspended_game(manager, monkeypatch):
    """Double Home used to fall back to `kill()` when suspending failed.

    `kill()` with no argument means "the one on the screen, else the suspended
    one" — which is right for a session bar and catastrophic here. The way a
    suspend fails is that the foreground has *just exited*, so by the time the
    fallback runs there is no foreground left and `kill()` reaches past it to
    destroy a suspended game the player never pointed at.

    A failed suspend is not consent to end anything.
    """
    from backend.services import gamepad_monitor as gm
    from backend import ws as ws_module

    held = _resident(manager, game_key="precious.iso", system_id="rpcs3",
                     state="background", pid=11)
    _resident(manager, game_key="playing.iso", system_id="pcsx2", pid=22)

    # Suspending fails the way it actually fails: the signal does not land.
    monkeypatch.setattr(manager, "_signal", lambda session, sig: False)
    monkeypatch.setattr(ws_module, "broadcast", AsyncMock())
    monkeypatch.setattr(gm, "_last_guide_press", 0.0)
    clock = {"t": 100.0}
    monkeypatch.setattr(gm.time, "monotonic", lambda: clock["t"])

    killed = AsyncMock()
    monkeypatch.setattr(manager, "kill", killed)

    async def scenario():
        await gm._on_guide_pressed()
        clock["t"] += (gm.DEBOUNCE + gm.DOUBLE_PRESS_WINDOW) / 2
        await gm._on_guide_pressed()

    asyncio.run(scenario())

    killed.assert_not_awaited()
    assert held in manager._sessions, "the suspended game was destroyed"


# ── 3. a disk pulled from under a suspended game ─────────────────────────────

def test_a_suspended_game_is_still_on_the_disk_that_was_pulled(manager):
    """`storage_monitor` asks which game is on a volume that just vanished. It
    asked `current_game`, which answers None for a suspended session — so a
    frozen emulator whose ROM lived on the pulled disk was invisible to the one
    warning written for exactly that."""
    from backend.services import storage_monitor

    _resident(manager, game_key="held.iso", system_id="rpcs3",
              state="background", pid=11, rom_path="/media/usb/held.iso")

    resident = storage_monitor._resident_games()
    assert any(g.get("rom_path") == "/media/usb/held.iso" for g in resident), (
        "the suspended game on the pulled disk was not seen")


# ── 4. recovery must not invent a freeze, nor lose a handle ──────────────────

def test_recovery_never_reports_a_freeze_it_did_not_perform(tmp_path,
                                                            monkeypatch):
    """A session file naming two foregrounds — a crash mid-swap — used to be
    resolved by writing `state = "background"` on the second one and nothing
    else. No signal was sent. The box then showed a session bar for a game that
    was still running at full speed behind the interface, and offered to
    "resume" something that had never stopped."""
    session_file = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", session_file)
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(pm, "_pgid_alive", lambda pgid: True)
    monkeypatch.setattr(pm.process_identity, "matches", lambda entry, pgid: True)

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(pm.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))

    session_file.write_text(
        '{"sessions": ['
        '{"pgid": 11, "game_key": "a.iso", "system_id": "s1", "exec_path": "x",'
        ' "launch_args": [], "rom_path": "", "started_at": 1.0, "state": "foreground"},'
        '{"pgid": 22, "game_key": "b.iso", "system_id": "s2", "exec_path": "x",'
        ' "launch_args": [], "rom_path": "", "started_at": 2.0, "state": "foreground"}]}')

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())

    held = fresh.background_sessions
    assert len(held) == 1, "one of the two had to be put behind the other"
    assert (held[0].pgid, signal.SIGSTOP) in sent, (
        "it was reported as suspended without ever being sent SIGSTOP")


def test_recovery_keeps_a_session_it_cannot_fit_rather_than_leaking_it(
        tmp_path, monkeypatch):
    """The resident cap governs NEW launches. Applying it to RECOVERY threw away
    the pgid, which is the only handle that can ever close that process — so an
    over-full session file turned into an emulator holding its memory until the
    box was restarted."""
    session_file = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", session_file)
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(pm, "_pgid_alive", lambda pgid: True)
    monkeypatch.setattr(pm.process_identity, "matches", lambda entry, pgid: True)
    monkeypatch.setattr(pm.os, "killpg", lambda pgid, sig: None)

    entries = ",".join(
        f'{{"pgid": {p}, "game_key": "g{p}.iso", "system_id": "s{p}",'
        f' "exec_path": "x", "launch_args": [], "rom_path": "",'
        f' "started_at": 1.0, "state": "background"}}' for p in (11, 22, 33))
    session_file.write_text('{"sessions": [' + entries + ']}')

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())

    recovered = {s.pgid for s in fresh._sessions}
    assert recovered == {11, 22, 33}, (
        f"a live process was dropped and can no longer be closed: {recovered}")


# ── 5. a session that died with nobody watching it ───────────────────────────

def test_an_adopted_session_that_dies_is_announced_not_just_forgotten(
        manager, monkeypatch):
    """An adopted session has no watcher — we are not its parent, so nothing is
    awaiting it. `_reap()` noticing it is gone fixed what a NEW client is told
    and left every connected one showing a session bar for a game that no longer
    exists, with a Resume button that would 409 for ever."""
    broadcast = AsyncMock()
    monkeypatch.setattr(pm.ws, "broadcast", broadcast)
    manager._sessions.append(pm.Session(orphan_pgid=4242, state="background",
                                        game_key="gone.iso", system_id="rpcs3",
                                        session_id=7))
    monkeypatch.setattr(pm, "_pgid_alive", lambda pgid: False)

    async def scenario():
        assert not manager.is_running       # the reap happens here
        await asyncio.sleep(0)              # let the announcement task run

    asyncio.run(scenario())

    finished = [c.args[1] for c in broadcast.await_args_list
                if c.args[0] == "game:finished"]
    assert finished and finished[0]["game_key"] == "gone.iso", (
        "the bar was left offering a game that is gone")
