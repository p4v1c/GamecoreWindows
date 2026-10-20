"""Suspending a session, and the two locks that decided whether it works.

The feature is "put my game down without losing it". Everything here is about
the two places that made that impossible rather than about freezing itself,
which is the easy part:

  · `/games/launch` refused every launch while `is_running` was true, and a
    frozen game is still running by that definition for as long as it is
    suspended. Backgrounding a game therefore locked the box out of starting
    anything else, which is the one thing the player backgrounded it to do.
  · playtime was wall-clock between launch and exit, so a game left suspended
    overnight billed the player for the night.

Nothing here starts a process: the jobs are `FakeJob`s (fake_jobs.py) and
every suspend, resume and terminate is recorded. `test_platform_jobs.py`
freezes and kills real process trees.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.services import process_manager as pm  # noqa: E402
from backend.services.game_session import Session  # noqa: E402
from backend.tests.fake_jobs import FakeJob, quiet_manager  # noqa: E402


@pytest.fixture
def manager(monkeypatch, spawner):
    return quiet_manager(monkeypatch)


async def _launch(manager, spawner, *, game_key: str, system_id: str,
                  rom_path: str = "rom.iso", pid: int | None = None) -> FakeJob:
    job = spawner.job(pid)
    await manager.launch("fake", "", rom_path=rom_path, game_key=game_key,
                         system_id=system_id)
    return job


# ── what is frozen ───────────────────────────────────────────────────────────

def test_suspending_freezes_the_job_and_resuming_thaws_it(manager, spawner):
    """The job, never one process: a launcher wrapper and the game it starts
    are both in it, and freezing the wrapper alone would leave the game
    running under the interface."""
    async def scenario():
        await _launch(manager, spawner, game_key="g", system_id="pcsx2", pid=1234)
        await manager.background()
        assert spawner.calls == [(1234, "suspend")]
        await manager.foreground()
        assert spawner.calls[-1] == (1234, "resume")

    asyncio.run(scenario())


def test_the_interface_is_raised_before_the_game_freezes(manager, spawner, monkeypatch):
    """A frozen window in front never answers the activation, and Windows then
    refuses to put it behind the interface: the window moves first."""
    async def raise_interface(s):
        spawner.calls.append(("interface", "raise"))
    monkeypatch.setattr(manager, "_raise_interface", raise_interface)

    async def scenario():
        await _launch(manager, spawner, game_key="a", system_id="s1", pid=5)
        await manager.background()
        assert spawner.calls == [("interface", "raise"), (5, "suspend")]

    asyncio.run(scenario())


def test_a_suspend_that_fails_gives_the_screen_back_to_the_game(manager, spawner):
    async def scenario():
        job = await _launch(manager, spawner, game_key="a", system_id="s1")
        job.refuse.add("suspend")
        with pytest.raises(pm.SessionConflict):
            await manager.background()
        manager._give_back_the_screen.assert_awaited_once()
        assert manager.foreground_session.game_key == "a"

    asyncio.run(scenario())


def test_a_failed_resume_puts_the_app_back_in_front(manager, spawner):
    """The app was put behind the interface before its freeze; when the game
    then refuses to wake, the app is thawed and its window comes back."""
    async def scenario():
        game = await _launch(manager, spawner, game_key="a.iso", system_id="s1", pid=5)
        await manager.background()
        await _launch(manager, spawner, game_key="app", system_id="app", pid=6)
        game.refuse.add("resume")
        with pytest.raises(pm.SessionConflict):
            await manager.foreground()
        assert manager.foreground_session.game_key == "app"
        assert spawner.calls[-1] == (6, "resume")
        manager._give_back_the_screen.assert_awaited()

    asyncio.run(scenario())


# ── lock 1: the 409 ──────────────────────────────────────────────────────────

def test_a_suspended_game_refuses_a_different_game(manager, spawner):
    async def scenario():
        await _launch(manager, spawner, game_key="zelda.iso", system_id="dolphin")
        await manager.background()

        assert manager.is_running, "the suspended game is still resident"
        assert not manager.is_foreground, "but nothing is on the screen"

        with pytest.raises(pm.SessionConflict, match="zelda.iso"):
            await _launch(manager, spawner, game_key="mario.iso", system_id="dolphin")
        assert manager.foreground_session is None
        assert [s.game_key for s in manager.background_sessions] == ["zelda.iso"]

    asyncio.run(scenario())


def test_selecting_the_suspended_game_resumes_instead_of_spawning(manager, spawner):
    async def scenario():
        await _launch(manager, spawner, game_key="zelda.iso", system_id="dolphin", pid=11)
        await manager.background()

        resumed = await manager.launch("fake", "", rom_path="rom.iso",
                                       game_key="zelda.iso", system_id="dolphin")
        assert resumed is True
        assert manager.foreground_session.game_key == "zelda.iso"
        assert len(spawner.argv) == 1, "resuming must not spawn a second game"
        assert spawner.calls[-1] == (11, "resume")

    asyncio.run(scenario())


def test_a_second_thing_on_the_screen_is_still_refused(manager, spawner):
    """The half of the old rule that was right: one screen, one session."""
    async def scenario():
        await _launch(manager, spawner, game_key="a", system_id="s1")
        with pytest.raises(pm.SessionConflict):
            await _launch(manager, spawner, game_key="b", system_id="s2")

    asyncio.run(scenario())


def test_the_session_past_the_cap_is_refused_by_name(manager, spawner):
    """The cap is memory, and the refusal says what is holding it.

    Written against `MAX_SESSIONS` rather than a literal: `_resident_cap` reads
    the cap off the PC, so a test that counts to three tests one machine's RAM.
    """
    async def scenario():
        held = [f"held{i}" for i in range(pm.MAX_SESSIONS)]
        for key in held:
            await _launch(manager, spawner, game_key=key, system_id=key)
            await manager.background()

        with pytest.raises(pm.SessionConflict) as e:
            await _launch(manager, spawner, game_key="one-too-many.iso", system_id="sX")
        for key in held:
            assert key in str(e.value), f"{key} is holding a slot and goes unnamed"

    asyncio.run(scenario())


def test_the_cap_follows_the_installed_memory(monkeypatch):
    monkeypatch.setattr(pm.processes, "physical_memory", lambda: 32 * 1024 ** 3)
    assert pm._resident_cap() == 3
    monkeypatch.setattr(pm.processes, "physical_memory", lambda: 8 * 1024 ** 3)
    assert pm._resident_cap() == 2
    monkeypatch.setattr(pm.processes, "physical_memory", lambda: 0)
    assert pm._resident_cap() == 2, "unknown memory is the floor"


def test_the_cap_leaves_room_for_a_game_beside_a_backgrounded_app(manager, spawner):
    """One thing in the background must not fill the box: with a flat two,
    YouTube in the background refused the next game."""
    async def scenario():
        await _launch(manager, spawner, game_key="youtube", system_id="youtube")
        await manager.background()

        await _launch(manager, spawner, game_key="a.iso", system_id="s1")  # must not raise
        assert manager.foreground_session is not None
        assert len(manager.background_sessions) == 1

    asyncio.run(scenario())


def test_suspending_an_app_is_not_refused_while_a_game_is_held(manager, spawner):
    """Moving a session from the screen to the background creates no session,
    so there is no memory argument for refusing it; refusing it trapped the
    player in an app with a game already suspended."""
    async def scenario():
        await _launch(manager, spawner, game_key="a.iso", system_id="s1")
        await manager.background()
        await _launch(manager, spawner, game_key="youtube", system_id="youtube")

        await manager.background()          # must not raise
        assert manager.foreground_session is None
        assert len(manager.background_sessions) == 2

    asyncio.run(scenario())


# ── lock 2's backend half: what the state says ───────────────────────────────

def test_the_state_reports_both_slots_and_stays_readable_by_an_old_client(manager, spawner):
    """`/games/session` has always been "the game in front of me", flat: a
    front end that predates suspension reads a box whose only session is
    suspended as "nothing in front of me", which leaves its pad unblocked."""
    async def scenario():
        await _launch(manager, spawner, game_key="zelda.iso", system_id="dolphin")

        state = manager.session_state()
        assert state["game_key"] == "zelda.iso"
        assert state["state"] == "foreground"
        assert "background" not in state

        await manager.background()
        state = manager.session_state()
        assert "game_key" not in state, (
            "a suspended session must not fill the flat fields — an old client "
            "reads those as 'a game is on my screen' and blocks the pad")
        assert [s["game_key"] for s in state["background"]] == ["zelda.iso"]
        assert state["background"][0]["state"] == "background"

    asyncio.run(scenario())


def test_an_application_session_is_named_as_one(manager, spawner, monkeypatch):
    """A tile with no ROM launches with `game_key == system_id`, the only thing
    that tells a theme to say "Close app" rather than "Close game"."""
    async def scenario():
        await _launch(manager, spawner, game_key="stremio", system_id="stremio", rom_path="")
        assert manager.session_state()["kind"] == "app"

        m2 = quiet_manager(monkeypatch)
        await _launch(m2, spawner, game_key="zelda.iso", system_id="dolphin")
        assert m2.session_state()["kind"] == "game"

    asyncio.run(scenario())


# ── the events ───────────────────────────────────────────────────────────────

def test_both_events_are_numbered_and_carry_the_whole_state(manager, spawner, monkeypatch):
    """A resume moves BOTH slots at once: a client rebuilding its picture from
    "run 3 came forward" alone would drop the session still frozen."""
    async def scenario():
        broadcast = AsyncMock()
        monkeypatch.setattr(pm.ws, "broadcast", broadcast)
        await _launch(manager, spawner, game_key="a.iso", system_id="s1")
        await manager.background()
        await _launch(manager, spawner, game_key="app2", system_id="app2")
        await manager.foreground()          # swaps app2 out, a in

        sent = [(c.args[0], c.args[1]) for c in broadcast.await_args_list]
        names = [e for e, _ in sent]
        assert names.count("game:backgrounded") == 2
        assert names.count("game:foregrounded") == 1

        bg_second = [d for e, d in sent if e == "game:backgrounded"][1]
        fg = [d for e, d in sent if e == "game:foregrounded"][0]
        assert bg_second["game_key"] == "app2"
        assert fg["game_key"] == "a.iso"
        assert isinstance(fg["session"], int)
        assert fg["session"] != bg_second["session"], (
            "two runs, two numbers — a late event about one must not be "
            "mistaken for the other")

        snap = fg["state_snapshot"]
        assert snap["game_key"] == "a.iso"
        assert [s["game_key"] for s in snap["background"]] == ["app2"]

    asyncio.run(scenario())


# ── playtime ─────────────────────────────────────────────────────────────────

def test_suspended_seconds_are_not_billed_as_playtime():
    """A frozen game is not a game being played: a game left in the background
    overnight would otherwise bill the player for the night."""
    s = Session(game_key="g", system_id="s", start_time=1000.0, session_id=1)
    now = 1060.0                              # one minute of actual play
    s.state, s.bg_since = "background", now
    now += 8 * 3600                           # suspended overnight
    assert s.played_secs(now) == 60

    s.bg_total += now - s.bg_since            # resumed, another minute
    s.bg_since, s.state = 0.0, "foreground"
    now += 60
    assert s.played_secs(now) == 120


def test_playtime_never_goes_backwards():
    """A clock that jumped must not subtract from a player's hours."""
    s = Session(game_key="g", system_id="s", start_time=5000.0, session_id=1)
    assert s.played_secs(4000.0) == 0


# ── killing ──────────────────────────────────────────────────────────────────

def test_closing_a_suspended_session_terminates_its_frozen_job(manager, spawner):
    """No thaw first: terminating a job kills frozen processes as they are,
    and a thawed game would get a frame or a sound out before dying."""
    async def scenario():
        await _launch(manager, spawner, game_key="a.iso", system_id="s1", pid=77)
        await manager.background()
        spawner.calls.clear()
        await manager.kill()
        assert spawner.calls == [(77, "terminate")]

    asyncio.run(scenario())


def test_a_bare_kill_closes_the_suspended_one_when_the_screen_is_empty(manager, spawner):
    """What "close it" means from a session bar with nothing else running."""
    async def scenario():
        job = await _launch(manager, spawner, game_key="a.iso", system_id="s1", pid=77)
        await manager.background()
        await manager.kill()
        assert spawner.calls[-1] == (77, "terminate")
        assert not job.alive()

    asyncio.run(scenario())


# ── the reap, which the slot cap made load-bearing ───────────────────────────

def test_a_game_that_has_exited_does_not_hold_a_slot(manager, spawner):
    """A slot still holding an exited game would refuse the next launch by
    naming games that are already gone, before their watchers ran."""
    async def scenario():
        first = await _launch(manager, spawner, game_key="app1", system_id="app1")
        await manager.background()
        second = await _launch(manager, spawner, game_key="app2", system_id="app2")
        first.exit()
        second.exit()                 # neither watcher has had a turn yet

        await _launch(manager, spawner, game_key="c.iso", system_id="s3")
        assert manager.foreground_session.game_key == "c.iso"
        assert manager.background_sessions == []

    asyncio.run(scenario())


# ── across a backend restart ─────────────────────────────────────────────────

def _reopen(monkeypatch, jobs_by_name: dict[str, FakeJob]) -> None:
    monkeypatch.setattr(pm.jobs, "open_job", lambda name, pid: jobs_by_name.get(name))
    monkeypatch.setattr(pm.process_identity, "matches", lambda entry, pid: True)


def test_a_suspended_session_survives_a_backend_restart(tmp_path, monkeypatch):
    """A suspension outlives the backend that made it, and a frozen game cannot
    exit on its own: it is the session that most needs finding again."""
    session_file = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", session_file)
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    job = FakeJob(4242)
    _reopen(monkeypatch, {job.name: job})
    session_file.write_text(json.dumps({"sessions": [{
        "job": job.name, "pid": 4242, "game_key": "zelda.iso",
        "system_id": "dolphin", "exec_path": "dolphin.exe", "launch_args": [],
        "rom_path": "", "started_at": 1000.0, "state": "background",
        "bg_since": 1060.0, "bg_total": 0.0}]}))

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())

    assert fresh.foreground_session is None, "it must not come back on screen"
    held = fresh.background_sessions
    assert [s.game_key for s in held] == ["zelda.iso"]
    assert held[0].state == "background"
    assert fresh.is_running and not fresh.is_foreground
    assert job.calls == [], "already frozen: nothing to send"


def test_what_is_written_can_be_read_back(tmp_path, monkeypatch, spawner):
    """Every slot round-trips, so a restart finds the box as it was left."""
    session_file = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", session_file)
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(pm.ws, "broadcast", AsyncMock())
    monkeypatch.setattr(pm.process_identity, "boot_id", lambda: "boot-a")
    monkeypatch.setattr(pm.process_identity, "start_time", lambda pid: f"t{pid}")

    async def scenario():
        m = pm.ProcessManager()
        monkeypatch.setattr(m, "_raise_interface", AsyncMock())
        launched = {}
        for pid, key in ((11, "app1"), (22, "app2")):
            job = spawner.job(pid)
            launched[job.name] = job
            await m.launch("fake", "", rom_path="r", game_key=key, system_id=key)
            await m.background()

        rows = json.loads(session_file.read_text())["sessions"]
        assert {r["job"] for r in rows} == set(launched)
        assert {r["leader_start"] for r in rows} == {"t11", "t22"}
        monkeypatch.setattr(pm.jobs, "open_job", lambda name, pid: launched.get(name))
        fresh = pm.ProcessManager()
        await fresh.adopt_orphan()
        assert sorted(s.game_key for s in fresh.background_sessions) == ["app1", "app2"]

    asyncio.run(scenario())


# ── the interface's own window ───────────────────────────────────────────────

def test_the_shell_executable_is_one_the_focus_helper_knows():
    """Raising the interface over a frozen game means finding its window by
    the process image, which electron-builder names after `productName`.
    Renaming it breaks nothing loudly: `hide()` logs and the player is left
    looking at a frozen picture, so the link is pinned here."""
    from backend.services import window_focus

    build = json.loads(
        (Path(__file__).resolve().parents[2] / "electron" / "package.json")
        .read_text(encoding="utf-8"))["build"]
    exe = f"{build['productName']}.exe".lower()
    known = {name.lower() for name in window_focus.SHELL_EXECUTABLES}
    assert exe in known, (
        f"the packaged interface is {exe!r}, which is not in "
        f"window_focus.SHELL_EXECUTABLES {window_focus.SHELL_EXECUTABLES}")
