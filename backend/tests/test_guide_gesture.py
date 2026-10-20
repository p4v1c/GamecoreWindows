"""The two gestures that get a player out of a game, and the debounce under them.

`gp:guide` is the one binding a theme may not take, because it is the only way
back to the interface from inside a running emulator. That makes the pairing
logic in front of it load-bearing in a way its size does not suggest: every
defect below is a player either trapped in a game or thrown out of one they
were still playing.

Nothing here talks to evdev or to a real pad. What is driven is the clock and
the event values, which is all these two gestures actually read.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import gamepad_monitor as gm    # noqa: E402
from backend.services.gamepad_devices import EV_ABS  # noqa: E402
from backend.services import process_manager as pm    # noqa: E402
from backend.services.game_session import Session    # noqa: E402
from backend.tests.fake_jobs import FakeJob           # noqa: E402

@pytest.fixture
def manager(monkeypatch):
    """The real manager, with fake jobs and its disk untouched."""
    m = pm.process_manager
    monkeypatch.setattr(m, "_sessions", [])
    monkeypatch.setattr(m, "_save_state", lambda: None)
    monkeypatch.setattr(m, "_raise_interface", AsyncMock())
    monkeypatch.setattr(m, "_give_back_the_screen", AsyncMock())
    return m


def _resident(manager, *, game_key, system_id, state="foreground", pid=4242):
    manager._seq += 1
    s = Session(job=FakeJob(pid), game_key=game_key, system_id=system_id,
                rom_path="", start_time=0.0, session_id=manager._seq,
                state=state)
    manager._sessions.append(s)
    return s


@pytest.fixture
def guide(monkeypatch):
    """A clock we drive, a broadcast we can read, and a cleared pairing.

    Both pieces of module state are patched, so nothing this test does to them
    survives into the next one — `monkeypatch` restores them, and a leftover
    "armed" would make an unrelated single press act.
    """
    from backend import ws as ws_module

    broadcast = AsyncMock()
    monkeypatch.setattr(ws_module, "broadcast", broadcast)
    monkeypatch.setattr(gm, "_last_guide_press", None)
    monkeypatch.setattr(gm, "_guide_armed", False)
    monkeypatch.setattr(gm, "_guide_from_game", False)

    clock = {"t": 100.0}
    monkeypatch.setattr(gm.time, "monotonic", lambda: clock["t"])

    class Guide:
        broadcasts = broadcast

        def press(self):
            asyncio.run(gm._on_guide_pressed())

        def wait(self, seconds):
            clock["t"] += seconds

        @property
        def actions(self):
            return [c.args[1] for c in broadcast.await_args_list
                    if c.args[0] == "gp:guide"]

    return Guide()


# ── the pairing ──────────────────────────────────────────────────────────────

def test_one_press_does_nothing_at_all(manager, guide):
    """A single press must never reach the game. It is two accidental taps
    away from the gesture, and it used to be a kill."""
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()

    assert guide.actions == []
    assert manager.foreground_session is not None


def test_two_presses_inside_the_window_suspend_the_game(manager, guide):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()
    guide.wait(0.4)
    guide.press()

    assert manager.foreground_session is None
    assert [s.game_key for s in manager.background_sessions] == ["zelda.iso"]
    assert guide.actions == [{"action": "backgrounded", "gesture": "guide"}]


def test_two_presses_too_far_apart_are_two_first_presses(manager, guide):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()
    guide.wait(gm.DOUBLE_PRESS_WINDOW + 0.5)
    guide.press()

    assert guide.actions == []
    assert manager.foreground_session is not None


def test_the_third_press_completes_a_pair_with_the_second(manager, guide):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()
    guide.wait(gm.DOUBLE_PRESS_WINDOW + 0.5)   # too late: arms again
    guide.press()
    guide.wait(0.3)                            # in time for the second
    guide.press()

    assert guide.actions == [{"action": "backgrounded", "gesture": "guide"}]


# ── the debounce ─────────────────────────────────────────────────────────────

def test_one_press_reported_twice_is_still_one_press(manager, guide):
    """A pad exposing the button as both BTN_MODE and KEY_HOMEPAGE reports
    every press twice, microseconds apart. Two reports of one press must not
    be the gesture."""
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()
    guide.wait(gm.DEBOUNCE / 2)
    guide.press()

    assert guide.actions == []
    assert manager.foreground_session is not None


def test_the_duplicate_of_the_acting_press_does_not_arm_the_next_one(manager, guide):
    """The defect this pairing was rewritten for.

    Firing used to clear the clock the debounce is measured against, so the
    pad's *second* report of the press that just acted looked like a brand-new
    first press. On a pad that reports twice, a double press suspended the game
    and left the next single press armed to act on its own — which is how a
    game gets suspended, or resumed, by one tap nobody meant as a gesture.
    """
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()
    guide.wait(0.4)
    guide.press()                      # acts
    guide.wait(gm.DEBOUNCE / 2)
    guide.press()                      # the duplicate of it
    assert len(guide.actions) == 1

    # Now one real, lone press. It must arm and do nothing.
    guide.wait(0.4)
    guide.press()
    assert len(guide.actions) == 1


def test_the_pairing_does_not_depend_on_where_monotonic_starts(manager, guide,
                                                               monkeypatch):
    """`time.monotonic()` counts from an arbitrary point, so a box whose clock
    is young is not a strange machine — it is a box that has just booted, which
    is exactly when somebody launches the first game. The sentinel for "no
    press pending" must not be a number the clock can equal."""
    monkeypatch.setattr(gm.time, "monotonic", lambda: 0.01)
    _resident(manager, game_key="zelda.iso", system_id="dolphin")

    guide.press()
    assert guide.actions == []
    assert manager.foreground_session is not None


# ── what it does when it fires ───────────────────────────────────────────────

def test_nothing_is_killed_when_the_suspend_fails(manager, guide, monkeypatch):
    """A failed suspend is not consent to end anything, and the fallback that
    reached for `kill()` destroyed a *suspended* game the player never pointed
    at — because the way a suspend fails is that the foreground has just gone."""
    held = _resident(manager, game_key="precious.iso", system_id="rpcs3",
                     state="background", pid=11)
    _resident(manager, game_key="playing.iso", system_id="pcsx2", pid=22)
    monkeypatch.setattr(manager, "_suspend", lambda session: False)
    killed = AsyncMock()
    monkeypatch.setattr(manager, "kill", killed)

    guide.press()
    guide.wait(0.4)
    guide.press()

    killed.assert_not_awaited()
    assert held in manager._sessions
    assert guide.actions == [{"action": "failed", "gesture": "guide"}]


def test_with_nothing_running_it_only_says_so(manager, guide):
    guide.press()
    guide.wait(0.4)
    guide.press()

    assert guide.actions == [{"action": "home", "gesture": "guide"}]


def test_a_pair_begun_in_a_game_never_opens_the_session_menu(manager, guide):
    """The browser sees the same two presses and can suspend the game before
    the second one reaches this monitor. Answering `home` then opened the
    session menu on the press that asked to leave the game."""
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    guide.press()
    guide.wait(0.2)
    asyncio.run(manager.background())      # the browser's request lands first
    guide.press()

    assert guide.actions == [{"action": "backgrounded", "gesture": "guide"}]


def test_a_pair_begun_in_the_interface_still_opens_it(manager, guide):
    _resident(manager, game_key="zelda.iso", system_id="dolphin", state="background")
    guide.press()
    guide.wait(0.2)
    guide.press()

    assert guide.actions == [{"action": "home", "gesture": "guide"}]


# ── Start+Select, for the pads with no guide button at all ───────────────────

BTN_SOUTH = 0x130       # A / Cross — a button, but not one of these two


def _key(code: int, value: int):
    return types.SimpleNamespace(type=gm.EV_KEY, code=code, value=value)


async def _settle():
    """Let the chord's timer task run to completion."""
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.fixture
def chord(monkeypatch):
    """A ChordWatcher whose hold is instant, so a test is not a wait."""
    monkeypatch.setattr(gm, "CHORD_HOLD_S", 0)
    fired: list[int] = []

    async def fire():
        fired.append(1)

    return types.SimpleNamespace(watcher=lambda: gm.ChordWatcher(fire), fired=fired)


def test_holding_start_and_select_together_fires_once(chord):
    async def scenario():
        w = chord.watcher()
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        w.feed(_key(gm.BTN_SELECT, gm.KEY_DOWN))
        await _settle()
        w.cancel()

    asyncio.run(scenario())
    assert chord.fired == [1]


def test_either_button_on_its_own_is_not_the_gesture(chord):
    """Start is the menu and Select is the power menu. Both are buttons a
    player presses on purpose, and neither may be an escape by itself."""
    async def scenario():
        w = chord.watcher()
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        await _settle()
        w.feed(_key(gm.BTN_START, gm.KEY_RELEASE))
        w.feed(_key(gm.BTN_SELECT, gm.KEY_DOWN))
        await _settle()
        w.cancel()

    asyncio.run(scenario())
    assert chord.fired == []


def test_letting_go_before_the_hold_is_up_cancels_it(monkeypatch):
    """The whole point of a hold: a player who taps both together while
    playing has not asked to leave the game."""
    monkeypatch.setattr(gm, "CHORD_HOLD_S", 5)
    fired: list[int] = []

    async def fire():
        fired.append(1)

    async def scenario():
        w = gm.ChordWatcher(fire)
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        w.feed(_key(gm.BTN_SELECT, gm.KEY_DOWN))
        await _settle()
        w.feed(_key(gm.BTN_SELECT, gm.KEY_RELEASE))
        await _settle()
        w.cancel()

    asyncio.run(scenario())
    assert fired == []


def test_an_autorepeat_is_not_a_second_press(chord):
    """evdev sends value 2 while a key is held. It changes nothing about what
    is down, and taking it for a press would re-arm the chord for ever."""
    async def scenario():
        w = chord.watcher()
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        w.feed(_key(gm.BTN_SELECT, gm.KEY_DOWN))
        await _settle()
        for _ in range(5):
            w.feed(_key(gm.BTN_START, 2))
            w.feed(_key(gm.BTN_SELECT, 2))
        await _settle()
        w.cancel()

    asyncio.run(scenario())
    assert chord.fired == [1]


def test_holding_it_again_after_a_release_fires_again(chord):
    async def scenario():
        w = chord.watcher()
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        w.feed(_key(gm.BTN_SELECT, gm.KEY_DOWN))
        await _settle()
        w.feed(_key(gm.BTN_START, gm.KEY_RELEASE))
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        await _settle()
        w.cancel()

    asyncio.run(scenario())
    assert chord.fired == [1, 1]


def test_other_buttons_are_not_part_of_the_chord(chord):
    async def scenario():
        w = chord.watcher()
        w.feed(_key(gm.BTN_START, gm.KEY_DOWN))
        w.feed(_key(BTN_SOUTH, gm.KEY_DOWN))
        w.feed(_key(EV_ABS, gm.KEY_DOWN))
        await _settle()
        w.cancel()

    asyncio.run(scenario())
    assert chord.fired == []


def test_the_chord_suspends_a_running_game(manager, guide):
    _resident(manager, game_key="zelda.iso", system_id="dolphin")
    asyncio.run(gm._suspend_to_interface("start+select", foreground_only=True))

    assert manager.foreground_session is None
    assert guide.actions == [{"action": "backgrounded", "gesture": "start+select"}]


def test_the_chord_does_nothing_when_no_game_holds_the_screen(manager, guide):
    """Start and Select are ordinary interface buttons. Holding them in a menu
    must not quietly send anybody home."""
    asyncio.run(gm._suspend_to_interface("start+select", foreground_only=True))

    assert guide.actions == []
