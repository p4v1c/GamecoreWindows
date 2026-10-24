"""The standby switch, and the two things it forgot.

Neither was reported. Both were found reading `run()` next to the report, and
both are reachable from Settings → Standby with nothing unusual going on.

  · the idle clock kept running while standby was OFF. The loop skipped the
    tick without touching `_last_input`, so the counter went on accumulating
    against a threshold nobody was measuring. Turn standby off, use the box for
    an afternoon, turn it back on — and it sleeps within one poll, straight
    past the screensaver, because it believes nobody has touched it since
    lunchtime.

  · turning standby off while the box was ASLEEP left it asleep. The loop's
    first line is `if not enabled: continue`, so nothing ever undid what the
    last tick had done. From the web UI on a phone — which is where somebody
    would reach for that switch with the television dark — the box answered
    "standby disabled" and stayed exactly as it was.

`run()`'s body is a function now, so a tick can be asked for directly instead of
waiting fifteen real seconds for the loop to come round.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.services import standby

# Read at import, before conftest's session guard replaces it.
REAL_POWER = standby._power


@pytest.fixture(autouse=True)
def quiet_screen(monkeypatch):
    """The screen and the power plan are not this file's business."""
    async def fake_power(*argv, **kw):
        return True
    monkeypatch.setattr(standby, "_power", fake_power)


@pytest.fixture(autouse=True)
def awake():
    standby._state = "active"
    standby._last_input = time.monotonic()
    yield
    standby._state = "active"


@pytest.fixture
def not_playing(monkeypatch):
    from backend.services import process_manager as pm
    monkeypatch.setattr(type(pm.process_manager), "is_foreground", property(lambda self: False))


def idle_for(minutes: float) -> None:
    standby._last_input = time.monotonic() - minutes * 60


CFG_ON = {"enabled": True, "screensaver_mins": 10, "sleep_mins": 20}
CFG_OFF = {**CFG_ON, "enabled": False}


def tick(cfg):
    asyncio.run(standby._tick(cfg))


# ── the clock must not run while nobody is counting ──────────────────────────

def test_a_tick_with_standby_off_restarts_the_idle_clock(not_playing):
    idle_for(180)
    tick(CFG_OFF)
    assert (time.monotonic() - standby._last_input) < 5


def test_turning_standby_back_on_does_not_sleep_the_box_at_once(not_playing):
    # The whole point. An afternoon with standby off, then the switch goes on.
    idle_for(180)
    for _ in range(3):
        tick(CFG_OFF)
    tick(CFG_ON)
    assert standby.get_state() == "active"


def test_the_screensaver_still_comes_up_on_its_own_schedule(not_playing):
    # And the guard on that fix: the clock restarting must not mean standby
    # never happens.
    idle_for(11)
    tick(CFG_ON)
    assert standby.get_state() == "screensaver"


def test_sleep_still_follows(not_playing):
    idle_for(21)
    tick(CFG_ON)
    assert standby.get_state() == "sleep"


def test_a_running_game_still_holds_everything_off(monkeypatch):
    from backend.services import process_manager as pm
    monkeypatch.setattr(type(pm.process_manager), "is_foreground", property(lambda self: True))
    idle_for(180)
    tick(CFG_ON)
    assert standby.get_state() == "active"
    assert (time.monotonic() - standby._last_input) < 5


# ── the switch must be able to end a standby it is switching off ─────────────

def test_switching_standby_off_wakes_a_sleeping_box(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend import main

    monkeypatch.setattr(standby, "CONFIG_FILE", tmp_path / "standby.json")
    with TestClient(main.app) as c:
        # After the client starts, not before: the lifespan calls
        # resume_after_restart(), which sets the state to active by design. A
        # test that arranged the state first would be testing that.
        standby._state = "sleep"
        r = c.post("/api/standby/config", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert standby.get_state() == "active"


def test_switching_standby_on_leaves_the_box_where_it_is(monkeypatch, tmp_path):
    # Only switching OFF wakes. Turning it on is not a reason to light the
    # screen, and a box that woke on any config write would never settle while
    # somebody was adjusting the timings.
    from fastapi.testclient import TestClient
    from backend import main

    monkeypatch.setattr(standby, "CONFIG_FILE", tmp_path / "standby.json")
    with TestClient(main.app) as c:
        standby._state = "screensaver"
        r = c.post("/api/standby/config", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert standby.get_state() == "screensaver"


def test_changing_only_the_timings_does_not_wake(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend import main

    monkeypatch.setattr(standby, "CONFIG_FILE", tmp_path / "standby.json")
    with TestClient(main.app) as c:
        standby._state = "screensaver"
        r = c.post("/api/standby/config", json={"screensaver_mins": 5})
    assert r.status_code == 200, r.text
    assert standby.get_state() == "screensaver"


# ── a game must hold the WHOLE box awake, not just our half ──────────────────
#
# `is_running` was doing its job and the television went dark anyway. The guard
# above only holds GAMECORE's standby off; the box also runs a desktop power
# manager with a timer of its own, which knows nothing about games and cannot
# see a gamepad at all — the kernel tags a pad's buttons ID_INPUT_JOYSTICK and
# libinput does not handle joysticks.
#
# So pad input is not enough to keep it quiet either: a cutscene, a pause menu
# or a long turn in a strategy game is fifteen minutes of perfect silence to it.
# Which is why this is said from the tick, on the clock, rather than only from
# on_input() when somebody happens to press something.

@pytest.fixture
def playing(monkeypatch):
    from backend.services import process_manager as pm
    monkeypatch.setattr(type(pm.process_manager), "is_foreground", property(lambda self: True))


@pytest.fixture
def session_calls(quiet_screen, monkeypatch):
    """Same silence as quiet_screen, but keeping the receipts."""
    calls: list[tuple] = []

    async def recording(*argv, **kw):
        calls.append(argv)
        return True

    monkeypatch.setattr(standby, "_power", recording)
    return calls


def signals(calls) -> list[tuple]:
    return [c for c in calls if c and c[0] == "signal_activity"]


def test_a_tick_during_a_game_tells_the_session_somebody_is_playing(playing, session_calls):
    idle_for(180)
    tick(CFG_ON)
    assert len(signals(session_calls)) == 1


def test_it_is_said_again_on_every_tick(playing, session_calls):
    # The point of saying it on a clock: the timer it has to keep pushing back
    # is minutes long, and nothing else will push it.
    for _ in range(4):
        tick(CFG_ON)
    assert len(signals(session_calls)) == 4


def test_a_tick_with_no_game_says_nothing(not_playing, session_calls):
    # The failure the other way round: a box that can never sleep because it
    # keeps announcing an activity nobody is producing.
    tick(CFG_ON)
    assert signals(session_calls) == []


def test_a_game_still_holds_our_own_standby_off(playing, session_calls):
    idle_for(180)
    tick(CFG_ON)
    assert standby.get_state() == "active"


# ── "Never" has to mean never ────────────────────────────────────────────────
#
# The settings screen offers it: SLEEP_MINS ends in 0, shown as "Never". It
# never reached the watcher. save_config clamped screen-off up to the
# screensaver stage — reasonable for any real number, fatal for 0, because
# max(screensaver_mins, 0) IS screensaver_mins.
#
# So the most cautious option on the page produced the most aggressive setting
# available, and silently removed the screensaver as well: _tick tests
# sleep_mins first, so both stages fired on the same minute and only the black
# one was ever seen. On this box, at 4 minutes.

@pytest.fixture
def stored(monkeypatch, tmp_path):
    monkeypatch.setattr(standby, "CONFIG_FILE", tmp_path / "standby.json")
    return standby


def test_never_survives_being_saved(stored):
    saved = stored.save_config({"screensaver_mins": 4, "sleep_mins": 0})
    assert saved["sleep_mins"] == 0
    assert saved["screensaver_mins"] == 4


def test_never_is_still_never_when_read_back(stored):
    stored.save_config({"screensaver_mins": 4, "sleep_mins": 0})
    assert stored.load_config()["sleep_mins"] == 0


def test_a_real_delay_is_still_pushed_past_the_screensaver(stored):
    # The clamp the fix must not break: screen-off cannot precede the slideshow.
    saved = stored.save_config({"screensaver_mins": 10, "sleep_mins": 3})
    assert saved["sleep_mins"] == 10


def test_a_negative_delay_reads_as_never(stored):
    assert stored.save_config({"screensaver_mins": 4, "sleep_mins": -5})["sleep_mins"] == 0


CFG_NEVER = {"enabled": True, "screensaver_mins": 4, "sleep_mins": 0}


def test_never_does_not_black_the_screen_however_long_the_box_sits(not_playing):
    idle_for(600)
    tick(CFG_NEVER)
    assert standby.get_state() == "screensaver"


def test_never_still_lets_the_screensaver_through(not_playing):
    idle_for(5)
    tick(CFG_NEVER)
    assert standby.get_state() == "screensaver"


def test_never_is_not_zero_minutes(not_playing):
    # The literal reading of the old code: idle >= 0 is true on the first tick,
    # so "never" would have meant "immediately".
    idle_for(0)
    tick(CFG_NEVER)
    assert standby.get_state() == "active"


# ── the screen and the plan go through `_power`, one call each ───────────────

def test_the_screen_is_turned_on_and_off_through_windows(session_calls):
    asyncio.run(standby._screen(True))
    asyncio.run(standby._screen(False))
    assert session_calls == [("screen_on",), ("screen_off",)]


@pytest.fixture
def plans(monkeypatch, tmp_path):
    """A fake power plan: `active` is what Windows reports, `calls` what was asked."""
    state = {"active": "381b4222-f694-41f0-9685-ff5bb260df2e", "calls": [], "saver": True}

    async def power(action, *args):
        state["calls"].append((action, *args))
        if action == "active_scheme":
            return state["active"]
        if action == "set_active_scheme":
            if args[0] == standby.platform_power.POWER_SAVER and not state["saver"]:
                return None             # no Power saver plan on this PC
            state["active"] = args[0]
        return True

    monkeypatch.setattr(standby, "_power", power)
    monkeypatch.setattr(standby, "_PLAN_NOTE", tmp_path / "power-plan-handoff.json")
    return state


def test_the_sleep_stage_switches_to_power_saver_and_back(plans):
    balanced = plans["active"]
    asyncio.run(standby._governor("powersave"))
    assert plans["active"] == standby.platform_power.POWER_SAVER
    asyncio.run(standby._governor("performance"))
    assert plans["active"] == balanced


def test_a_pc_without_power_saver_keeps_its_plan(plans):
    plans["saver"] = False
    balanced = plans["active"]
    asyncio.run(standby._governor("powersave"))
    asyncio.run(standby._governor("performance"))
    assert plans["active"] == balanced
    assert ("set_active_scheme", balanced) not in plans["calls"]


def test_a_wake_with_no_sleep_before_it_changes_no_plan(plans):
    asyncio.run(standby._governor("performance"))
    assert plans["calls"] == []


def test_a_restarted_backend_still_finds_the_plan_to_restore(plans):
    """The plan is Windows' state: it outlives the process that changed it."""
    balanced = plans["active"]
    asyncio.run(standby._governor("powersave"))
    # A new process: nothing in memory, the note on disk.
    asyncio.run(standby.resume_after_restart())
    assert plans["active"] == balanced
    assert not standby._PLAN_NOTE.exists()


def test_a_call_windows_refuses_is_logged_not_raised(monkeypatch):
    def refuse(*args):
        raise OSError("PowerSetActiveScheme refused")
    monkeypatch.setattr(standby.platform_power, "set_active_scheme", refuse)
    monkeypatch.setattr(standby, "_power", REAL_POWER)
    assert asyncio.run(standby._power("set_active_scheme", "x")) is None
