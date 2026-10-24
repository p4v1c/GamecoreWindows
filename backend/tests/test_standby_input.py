"""What the box counts as somebody being there.

Reported from the sofa: standby "is not very responsive, it looks like I have
to press the PS4 touchpad to wake the box". That is not a
feeling, it is the rule the code had: `_watch_device` counted `EV_KEY` down and
nothing else.

The d-pad is not EV_KEY: the pad layer (`platform/gamepads.py`) reports it as
a hat, `ABS_HAT0X` / `ABS_HAT0Y`, and the sticks are `ABS_*` too. So the two
most natural ways to say "I am here" would be invisible:

  · pressing a direction did not wake the box, and
  · worse, it did not reset the idle timer either — browse a menu with the stick
    alone and the screensaver comes up while you are using it.

The fix cannot be "count every ABS event": a resting stick reports constantly,
and a box that treats jitter as activity never sleeps at all. So movement is
what counts, measured against the axis's own travel — which is why the decision
is a function that can be asked directly, instead of a condition buried in an
async read loop nobody can drive from a test.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.services import gamepad_monitor as gm
from backend.services.input_activity import ActivityFilter


EV_KEY, EV_ABS, EV_SYN = 1, 3, 0
BTN_SOUTH = 0x130
ABS_X, ABS_Y = 0x00, 0x01
ABS_Z, ABS_RZ = 0x02, 0x05          # the triggers on a DualShock 4
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11   # the d-pad


class FakeAbsInfo:
    def __init__(self, minimum, maximum):
        self.min, self.max = minimum, maximum


class FakePad:
    """Just enough of a device for the classifier: axis ranges."""

    RANGES = {
        ABS_X: (0, 255), ABS_Y: (0, 255),        # sticks, resting at 128
        ABS_Z: (0, 255), ABS_RZ: (0, 255),       # triggers, resting at 0
        ABS_HAT0X: (-1, 1), ABS_HAT0Y: (-1, 1),  # d-pad, three positions
    }

    name = "Wireless Controller"

    def absinfo(self, code):
        if code not in self.RANGES:
            raise KeyError(code)
        return FakeAbsInfo(*self.RANGES[code])


def ev(type_, code, value):
    return types.SimpleNamespace(type=type_, code=code, value=value)


@pytest.fixture
def watch():
    return ActivityFilter(FakePad())


# ── buttons, which already worked and must go on working ─────────────────────

def test_a_button_press_is_activity(watch):
    assert watch.is_activity(ev(EV_KEY, BTN_SOUTH, 1))


def test_releasing_a_button_is_not(watch):
    # The press already counted. Counting the release too would double every
    # press and, more to the point, a pad left with a button under a cushion
    # would report forever.
    assert not watch.is_activity(ev(EV_KEY, BTN_SOUTH, 0))


def test_an_event_that_is_neither_key_nor_axis_is_not(watch):
    assert not watch.is_activity(ev(EV_SYN, 0, 0))


# ── the d-pad, which is a hat and was invisible ──────────────────────────────

@pytest.mark.parametrize("code", [ABS_HAT0X, ABS_HAT0Y])
@pytest.mark.parametrize("value", [-1, 1])
def test_the_dpad_is_activity(watch, code, value):
    # The reported symptom, exactly: a direction on a DualShock 4 is a hat, so
    # it woke nothing and reset nothing.
    assert watch.is_activity(ev(EV_ABS, code, value))


def test_letting_the_dpad_go_is_not(watch):
    watch.is_activity(ev(EV_ABS, ABS_HAT0X, 1))
    assert not watch.is_activity(ev(EV_ABS, ABS_HAT0X, 0))


# ── the sticks, where counting everything would be worse than counting none ──

def test_a_stick_at_rest_is_not_activity(watch):
    # The whole reason this is not simply "any ABS event". A resting stick
    # reports a trickle of noise around its centre, and a box that treated that
    # as somebody being there would never sleep — which is a worse bug than the
    # one being fixed, and the one an obvious fix would have introduced.
    watch.is_activity(ev(EV_ABS, ABS_X, 128))
    for jitter in (127, 129, 128, 130, 126, 131, 125):
        assert not watch.is_activity(ev(EV_ABS, ABS_X, jitter))


def test_pushing_a_stick_is_activity(watch):
    watch.is_activity(ev(EV_ABS, ABS_X, 128))
    assert watch.is_activity(ev(EV_ABS, ABS_X, 255))


def test_letting_a_pushed_stick_spring_back_is_activity(watch):
    # Still the player's hand. Measured as travel rather than position, so the
    # way back counts the same as the way out.
    watch.is_activity(ev(EV_ABS, ABS_X, 128))
    watch.is_activity(ev(EV_ABS, ABS_X, 255))
    assert watch.is_activity(ev(EV_ABS, ABS_X, 128))


def test_a_slow_drift_across_the_whole_axis_is_not(watch):
    # A worn stick can wander a long way without anyone touching it, one step
    # at a time. Travel is measured against the last reported value, so a drift
    # never adds up to a press however far it goes.
    watch.is_activity(ev(EV_ABS, ABS_X, 128))
    for v in range(128, 256, 4):
        assert not watch.is_activity(ev(EV_ABS, ABS_X, v))


def test_pulling_a_trigger_is_activity(watch):
    # Triggers rest at their minimum, not their centre — which is why the test
    # is here: a rule written as "far from the middle" would call a resting
    # trigger a held one, and the box would never sleep with a pad plugged in.
    watch.is_activity(ev(EV_ABS, ABS_Z, 0))
    assert watch.is_activity(ev(EV_ABS, ABS_Z, 255))


def test_a_resting_trigger_is_not(watch):
    watch.is_activity(ev(EV_ABS, ABS_Z, 0))
    for v in (0, 1, 0, 2, 0):
        assert not watch.is_activity(ev(EV_ABS, ABS_Z, v))


def test_the_first_reading_of_an_axis_only_takes_a_bearing(watch):
    # A pad announces every axis when it connects. Treating that burst as
    # activity would wake the box each time a Bluetooth pad re-paired itself in
    # the night, which is the same reason the frontend ignores gp:connected.
    assert not watch.is_activity(ev(EV_ABS, ABS_X, 255))
    assert not watch.is_activity(ev(EV_ABS, ABS_RZ, 255))


def test_an_axis_the_pad_cannot_describe_is_ignored(watch):
    # No absinfo, no way to know what "a long way" means on that axis. Silence
    # is the safe answer: the alternative is a box that never sleeps because
    # some device reports an axis nobody can measure.
    assert not watch.is_activity(ev(EV_ABS, 0x28, 4000))
    assert not watch.is_activity(ev(EV_ABS, 0x28, 0))


def test_each_axis_is_judged_on_its_own(watch):
    watch.is_activity(ev(EV_ABS, ABS_X, 128))
    watch.is_activity(ev(EV_ABS, ABS_Y, 128))
    assert watch.is_activity(ev(EV_ABS, ABS_Y, 255))
    assert not watch.is_activity(ev(EV_ABS, ABS_X, 130))


# ── and the wiring, because a correct rule the loop never asks is not a fix ───
#
# The loop reads the pad layer, in SDL's ranges: sticks -32768..32767 resting
# at 0, triggers 0..32767 resting at 0.

def pad(type_, code, value):
    from backend.services.platform.gamepads import PadEvent
    return PadEvent(type_, code, value, "pad")


@pytest.fixture
def watched(monkeypatch):
    """Run `_watch_device` over a script and report what standby was told."""
    import asyncio

    from backend.services import standby
    from backend.services.platform import gamepads as platform_gamepads

    def run(events):
        seen = {"n": 0}

        def subscribe(device):
            queue = asyncio.Queue()
            for event in [*events, platform_gamepads.GONE]:
                queue.put_nowait(event)
            return queue

        monkeypatch.setattr(platform_gamepads, "subscribe", subscribe)
        monkeypatch.setattr(platform_gamepads, "unsubscribe", lambda device, queue: None)
        monkeypatch.setattr(standby, "on_input", lambda: seen.__setitem__("n", seen["n"] + 1))
        asyncio.run(gm._watch_device("sdl0"))
        return seen["n"]

    return run


def test_the_loop_reports_a_dpad_press_as_activity(watched):
    assert watched([pad(EV_ABS, ABS_HAT0X, 1)]) == 1


def test_the_loop_still_reports_a_button(watched):
    assert watched([pad(EV_KEY, BTN_SOUTH, 1)]) == 1


def test_the_loop_reports_a_stick_being_pushed(watched):
    assert watched([pad(EV_ABS, ABS_X, 0), pad(EV_ABS, ABS_X, 32767)]) == 1


def test_the_loop_stays_quiet_for_a_pad_sitting_on_the_sofa(watched):
    # The failure an over-eager fix produces: a box that never sleeps.
    noise = [pad(EV_ABS, ABS_X, 0), pad(EV_ABS, ABS_Y, 0), pad(EV_ABS, ABS_Z, 0)]
    noise += [pad(EV_ABS, ABS_X, (i % 3) * 200 - 200) for i in range(30)]
    noise += [pad(EV_KEY, BTN_SOUTH, 0)]
    assert watched(noise) == 0


def test_the_raw_layer_is_the_wizards_not_standbys(watched):
    from backend.services.platform.gamepads import PadEvent
    assert watched([PadEvent(EV_KEY, 0, 1, "raw")]) == 0


# ── and what the box does with what it counted ──────────────────────────────
#
# Counting the press was only ever half of it: Windows' own idle timer cannot
# see a pad, so on_input() also tells Windows somebody is there. These tests
# pin the two properties that make that safe: it is said on a press, and it is
# NOT said eighty times a second while somebody swings a stick.

import asyncio  # noqa: E402

from backend.services import standby  # noqa: E402


@pytest.fixture
def bus(monkeypatch):
    """Record what standby would run, with the throttle wound back to zero."""
    calls: list[tuple] = []

    async def fake_power(*argv, **kw):
        calls.append(argv)
        return True

    monkeypatch.setattr(standby, "_power", fake_power)
    monkeypatch.setattr(standby, "_last_activity_signal", 0.0)
    monkeypatch.setattr(standby, "_state", "active")
    return calls


def press(n: int = 1) -> None:
    """n button presses, with the fire-and-forget tasks allowed to finish."""
    async def scenario():
        for _ in range(n):
            standby.on_input()
        await asyncio.sleep(0.01)
    asyncio.run(scenario())


def signals(calls) -> list[tuple]:
    return [c for c in calls if c and c[0] == "signal_activity"]


def screen_on(calls) -> list[tuple]:
    return [c for c in calls if c == ("screen_on",)]


def test_a_button_tells_windows_somebody_is_there(bus):
    press()
    assert signals(bus) == [("signal_activity",)]


def test_it_is_said_even_when_gamecore_believes_it_is_awake(bus):
    # The whole point. _state is "active" here — GameCore is not in standby and
    # has nothing of its own to exit. The screen is out anyway, because someone
    # else turned it off. A wake that only fires when GameCore thinks it is
    # asleep is the exact bug exit_standby() already had to be cured of.
    assert standby.get_state() == "active"
    press()
    assert len(signals(bus)) == 1


def test_a_burst_of_input_is_not_a_burst_of_calls(bus):
    # A stick swung hard produces a retained event per sample. Without the
    # throttle this is one Windows call per frame, for the whole game.
    press(50)
    assert len(signals(bus)) == 1


def test_it_is_said_again_once_the_gap_has_passed(bus):
    press()
    monkeypatched_past = standby._last_activity_signal - standby._ACTIVITY_SIGNAL_GAP - 1
    standby._last_activity_signal = monkeypatched_past
    press()
    assert len(signals(bus)) == 2


# ── and a press has to be able to bring the screen BACK ──────────────────────
#
# Restarting Windows' idle timer does not light a screen the timer already
# turned off. So the activity signal alone would keep the box from going dark
# and leave somebody in front of a dark one with no way back.

def test_the_first_press_after_a_long_silence_turns_the_screen_back_on(bus, monkeypatch):
    monkeypatch.setattr(standby, "_last_press", 0.0)      # nobody has touched it
    press()
    assert screen_on(bus), bus


def test_pressing_through_a_game_does_not_reassert_it_every_time(bus, monkeypatch):
    # The reason this is gated on a silence rather than done on every press:
    # while somebody is playing, input never stops long enough for the screen
    # to have gone out, and a screen-on per press would be pure cost.
    monkeypatch.setattr(standby, "_last_press", 0.0)
    press()
    bus.clear()
    press(20)
    assert not screen_on(bus), bus


def test_a_press_says_both_things_when_it_comes_back_to_a_dark_room(bus, monkeypatch):
    # The pair, together: keep the timer down AND recover the screen.
    monkeypatch.setattr(standby, "_last_press", 0.0)
    press()
    assert signals(bus), "the idle timer was not reset"
    assert screen_on(bus), "the screen was not turned on"
