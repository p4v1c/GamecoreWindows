"""The failure paths around launching, standby and restarting.

Everything here is about what the box does when something has already gone
wrong: the screen left off by a previous process, an emulator that is not
installed, a game still running after the backend restarted.

Run under pytest:  pytest backend/tests/test_session_robustness.py
Or directly:       python backend/tests/test_session_robustness.py
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.services import process_identity
from backend.services import process_manager as pm

from backend.services import session as xsession
from backend.services import standby


# ── #13 · standby ────────────────────────────────────────────────────────────

@pytest.fixture
def recorded_cmds(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def fake_run_cmd(*argv, **kw):
        calls.append(argv)
        return True

    monkeypatch.setattr(standby, "_run_cmd", fake_run_cmd)
    return calls


def test_exit_standby_is_unconditional(recorded_cmds):
    """The wake that mattered was the one from a state we already believed active.

    _state is in memory, `xset dpms force off` is in the X server, and X does
    not restart with the backend — so after a restart the two disagreed and the
    old early return meant POST /api/standby/exit did nothing at all.
    """
    standby._state = "active"
    asyncio.run(standby.exit_standby())
    assert any("dpms" in " ".join(c) for c in recorded_cmds), recorded_cmds



def _turns_the_screen_on(argv: tuple) -> bool:
    """Whatever tool this box answers to, did we ask for the screen back?

    Asserted as intent rather than as `xset`, because the tool is chosen at
    runtime: a Plasma-on-Wayland box gets kscreen-doctor, because XWayland has
    no DPMS extension and xset there exits 0 having done nothing. Pinning the
    command name made this test pass or fail on which machine ran it.
    """
    return "dpms" in " ".join(argv) and "on" in argv


def test_exit_standby_from_sleep_still_wakes(recorded_cmds):
    standby._state = "sleep"
    asyncio.run(standby.exit_standby())
    assert standby.get_state() == "active"
    assert any(_turns_the_screen_on(c) for c in recorded_cmds), recorded_cmds


def test_resume_after_restart_forces_the_screen_back_on(recorded_cmds):
    standby._state = "sleep"          # what the *previous* process had left behind
    asyncio.run(standby.resume_after_restart())
    assert standby.get_state() == "active"
    assert any(_turns_the_screen_on(c) for c in recorded_cmds), recorded_cmds
    assert any("performance" in " ".join(c) for c in recorded_cmds), recorded_cmds


# ── #15 · the display probe must not block the loop ──────────────────────────

@pytest.mark.linux_until_port(3)
def test_the_display_probe_runs_once_not_per_launch(monkeypatch):
    probes = []

    def slow_probe(uid):
        probes.append(uid)
        return (":0", "/tmp/xauth_test")

    monkeypatch.setattr(xsession, "_probe_display", slow_probe)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    xsession.invalidate_display_cache()

    for _ in range(5):
        env = xsession._display_env()
        assert env["DISPLAY"] == ":0"

    assert len(probes) == 1, f"probed {len(probes)}x — it used to run on every launch and every standby transition"


@pytest.mark.linux_until_port(3)
def test_invalidating_the_cache_makes_the_next_call_probe_again(monkeypatch):
    probes = []
    monkeypatch.setattr(xsession, "_probe_display", lambda uid: probes.append(uid) or (":0", ""))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)

    xsession.invalidate_display_cache()
    xsession._display_env()
    xsession._display_env()
    assert len(probes) == 1

    xsession.invalidate_display_cache()
    xsession._display_env()
    assert len(probes) == 2


@pytest.mark.linux_until_port(3)
def test_display_env_does_not_block_the_event_loop(monkeypatch):
    """A slow probe must not stall unrelated requests — it stalled them 4.7 s."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    monkeypatch.setattr(xsession, "_probe_display", lambda uid: time.sleep(0.5) or (":0", ""))
    xsession.invalidate_display_cache()

    async def scenario():
        ticks = 0

        async def other_traffic():
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0.02)
                ticks += 1

        traffic = asyncio.create_task(other_traffic())
        await pm.display_env()
        await traffic
        return ticks

    # The loop kept serving while the probe ran in its thread.
    assert asyncio.run(scenario()) == 20


# ── #17 · a game that outlives the backend ───────────────────────────────────

@pytest.fixture
def session_file(tmp_path, monkeypatch):
    f = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", f)
    return f


def _spawn_sleeper() -> subprocess.Popen:
    """A process in its own group, standing in for an emulator."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _identity(pid: int) -> dict:
    """What `_save_state` records beside the pgid to recognise it later."""
    return {"boot_id": process_identity.boot_id(),
            "leader_start": process_identity.start_time(pid)}


@pytest.mark.linux_until_port(3)
def test_a_restarted_backend_finds_the_running_game(session_file):
    proc = _spawn_sleeper()
    try:
        session_file.write_text(json.dumps({
            "pgid": os.getpgid(proc.pid), "game_key": "Melee.iso",
            "system_id": "dolphin", "exec_path": "/usr/bin/dolphin-emu",
            "rom_path": "/run/media/gc/ROMS/gamecube/Melee.iso",
            "launch_args": [], "started_at": time.time(),
            **_identity(proc.pid),
        }))

        fresh = pm.ProcessManager()          # what starting the backend gives you
        assert not fresh.is_running, "nothing adopted yet"

        asyncio.run(fresh.adopt_orphan())
        assert fresh.is_running, "the emulator is still on screen — say so"
        # rom_path is part of the session because storage_monitor asks whether
        # the disk that has just been unplugged is the one the running game
        # came from. An adopted game that forgot its path would be the one case
        # where a disk pulled mid-session said nothing — precisely after a
        # backend restart, when the player is least likely to expect it.
        game = fresh.current_game
        # An adoption is a run, and runs are numbered: the UI tells this game's
        # finish from that of a game it has already moved on from by the number.
        assert game.pop("session") > 0
        assert game == {
            "game_key": "Melee.iso", "system_id": "dolphin",
            "rom_path": "/run/media/gc/ROMS/gamecube/Melee.iso"}
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        proc.wait(timeout=5)


@pytest.mark.linux_until_port(3)
def test_the_adopted_game_can_still_be_killed(session_file):
    """The whole point: double-PS could no longer close an orphaned emulator."""
    proc = _spawn_sleeper()
    pgid = os.getpgid(proc.pid)
    session_file.write_text(json.dumps({
        "pgid": pgid, "game_key": "Melee.iso", "system_id": "dolphin",
        "exec_path": "/usr/bin/dolphin-emu", "launch_args": [], "started_at": time.time(),
        **_identity(pgid),
    }))

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    asyncio.run(fresh.kill())

    assert proc.wait(timeout=5) is not None, "the emulator must actually die"
    assert not fresh.is_running
    assert not session_file.exists(), "the session file goes with it"


@pytest.mark.linux_until_port(3)
def test_a_stale_session_file_is_discarded(session_file):
    proc = _spawn_sleeper()
    pgid = os.getpgid(proc.pid)
    os.killpg(pgid, signal.SIGKILL)
    proc.wait(timeout=5)

    session_file.write_text(json.dumps({
        "pgid": pgid, "game_key": "Melee.iso", "system_id": "dolphin",
        "exec_path": "", "launch_args": [], "started_at": time.time(),
    }))

    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    assert not fresh.is_running, "that pgid is long gone"
    assert not session_file.exists()


def test_a_corrupt_session_file_is_ignored(session_file):
    session_file.write_text("{ not json")
    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    assert not fresh.is_running


def test_no_session_file_is_the_normal_case(session_file):
    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    assert not fresh.is_running


# ── #16 · an emulator that is not installed ──────────────────────────────────

@pytest.fixture
def client_with_ghost_system(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.services import systems as systems_service
    from backend import main

    ghost = {"id": "ghost", "label": "Ghost", "kind": "emulator",
             "path": "/usr/bin/definitely-not-installed", "args": "", "romsPath": ""}
    monkeypatch.setattr(systems_service, "list_all", lambda: [ghost])
    with TestClient(main.app) as c:
        yield c


@pytest.mark.linux_until_port(3)
def test_missing_emulator_returns_503_and_says_which(client_with_ghost_system):
    r = client_with_ghost_system.post("/api/games/launch", json={"system_id": "ghost"})
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "ghost" in detail or "not-installed" in detail, detail


@pytest.mark.linux_until_port(3)
def test_a_failed_launch_leaves_the_box_able_to_try_again(client_with_ghost_system):
    for _ in range(3):
        r = client_with_ghost_system.post("/api/games/launch", json={"system_id": "ghost"})
        assert r.status_code == 503, r.text
    assert not pm.process_manager.is_running, "_launching must have been released"


if __name__ == "__main__":
    print("Run this one under pytest — it uses tmp_path and monkeypatch fixtures:")
    print("    pytest backend/tests/test_session_robustness.py")
    sys.exit(0)


# ── A failed probe is not a probed failure ───────────────────────────────────
# The boot race, measured on the reference box: systemd starts the backend at
# 14:56:19, the lifespan reaches the X probe at 14:56:20, and the only display
# that answers there appears at 14:56:24.3. Latching that 4.3-second-early
# failure left the box unable to launch anything until the service was
# restarted by hand.

@pytest.mark.linux_until_port(3)
def test_a_failed_probe_is_retried_not_latched(monkeypatch):
    """X not being up yet means 'ask again', never 'there is no display'."""
    attempts = []

    def probe(uid):
        attempts.append(uid)
        # Fails while X is still starting, then succeeds — the real boot.
        return None if len(attempts) < 3 else (":1", "/run/user/1000/xauth_ok")

    monkeypatch.setattr(xsession, "_probe_display", probe)
    monkeypatch.setattr(xsession, "_PROBE_RETRY_SECS", 0)  # don't sleep through the backoff
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    xsession.invalidate_display_cache()

    # While X is down the caller still gets an env — the old static fallback.
    assert xsession._display_env()["DISPLAY"] == ":0"
    assert xsession._display_env()["DISPLAY"] == ":0"

    # Once X answers, the real display is picked up without a restart.
    env = xsession._display_env()
    assert env["DISPLAY"] == ":1", "the backend had to be restarted by hand to get here"
    assert env["XAUTHORITY"] == "/run/user/1000/xauth_ok"
    assert len(attempts) == 3


@pytest.mark.linux_until_port(3)
def test_a_successful_probe_is_still_latched(monkeypatch):
    """The retry must not cost a probe per call once X has answered."""
    attempts = []
    monkeypatch.setattr(xsession, "_probe_display",
                        lambda uid: attempts.append(uid) or (":1", ""))
    monkeypatch.setattr(xsession, "_PROBE_RETRY_SECS", 0)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    xsession.invalidate_display_cache()

    for _ in range(5):
        assert xsession._display_env()["DISPLAY"] == ":1"
    assert len(attempts) == 1


@pytest.mark.linux_until_port(3)
def test_a_headless_box_does_not_probe_on_every_call(monkeypatch):
    """No X at all must not mean xdpyinfo's 5 s timeout on every standby tick."""
    attempts = []
    monkeypatch.setattr(xsession, "_probe_display", lambda uid: attempts.append(uid))
    monkeypatch.setattr(xsession, "_PROBE_RETRY_SECS", 300)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    xsession.invalidate_display_cache()

    for _ in range(10):
        xsession._display_env()
    assert len(attempts) == 1, "the backoff must hold between retries"
