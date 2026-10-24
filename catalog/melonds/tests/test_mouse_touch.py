"""One mouse per player: which device is a mouse, who owns it, where its
arrow may go, and what its button turns into. The X and uinput halves need
the real session; they were checked on the box (docs, 10-catalog)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from backend.tests.linux_until_port import linux_until_port_module

linux_until_port_module(11)  # inputdev imports fcntl: evdev and uinput

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "multiplayer"))

import inputdev       # noqa: E402
import mouse_touch    # noqa: E402

EV_REL, EV_KEY, EV_SYN = inputdev.EV_REL, inputdev.EV_KEY, inputdev.EV_SYN
REL_X, REL_Y, BTN_LEFT = inputdev.REL_X, inputdev.REL_Y, inputdev.BTN_LEFT


def _move(dx, dy):
    return [(EV_REL, REL_X, dx), (EV_REL, REL_Y, dy), (EV_SYN, 0, 0)]


def _button(value):
    return [(EV_KEY, BTN_LEFT, value), (EV_SYN, 0, 0)]


def test_the_arrow_stays_inside_its_players_column():
    p = mouse_touch.Pointer((480, 0, 480, 1080), gain=1.0)
    assert p.position == (720, 540)                    # starts in the middle
    p.feed(_move(-5000, -5000))
    assert p.position == (480, 0)
    p.feed(_move(5000, 5000))
    assert p.position == (959, 1079)                   # never on the next column


def test_the_button_becomes_a_touch_that_follows_the_drag():
    p = mouse_touch.Pointer((0, 0, 480, 1080), gain=1.0)
    assert p.feed(_button(1)) == [("down", 240, 540)]
    assert p.feed(_move(10, 0)) == [("move", 250, 540), ("drag", 250, 540)]
    assert p.feed(_button(0)) == [("up",)]
    assert p.feed(_move(5, 5)) == [("move", 255, 545)]   # no drag once released


def test_an_old_40hz_mouse_and_a_125hz_one_travel_alike():
    """Same hand movement, fewer bigger reports: same distance (flat gain)."""
    old, new = (mouse_touch.Pointer((0, 0, 1920, 1080), gain=1.0) for _ in range(2))
    for _ in range(8):
        old.feed(_move(25, 0))
    for _ in range(25):
        new.feed(_move(8, 0))
    assert old.position == new.position


def test_motion_before_the_button_in_one_frame_lands_first():
    p = mouse_touch.Pointer((0, 0, 480, 1080), gain=1.0)
    actions = p.feed([(EV_REL, REL_X, 20), (EV_KEY, BTN_LEFT, 1), (EV_SYN, 0, 0)])
    assert actions == [("move", 260, 540), ("down", 260, 540)]


def test_key_repeat_does_not_touch_again():
    p = mouse_touch.Pointer((0, 0, 480, 1080))
    p.feed(_button(1))
    assert p.feed([(EV_KEY, BTN_LEFT, 2), (EV_SYN, 0, 0)]) == []


def test_mice_get_players_in_the_order_they_are_used():
    a = mouse_touch.Assigner(players=2)
    assert a.note("/dev/input/event9", _move(10, 10)) is None      # a bump, not use
    assert a.note("/dev/input/event4", _button(1)) == 1             # a click is use
    assert not a.routed
    assert a.note("/dev/input/event9", _move(30, 0)) == 2
    assert a.routed
    assert a.note("/dev/input/event7", _move(500, 500)) is None     # no seat left


def test_a_pointer_that_never_moves_never_takes_a_player():
    """A keyboard's built-in pointer node: present, silent, never player 1."""
    a = mouse_touch.Assigner(players=2)
    a.note("/dev/input/event4", [])                                  # the keyboard node
    assert a.note("/dev/input/event13", _move(60, 0)) == 1
    assert not a.routed                                              # one mouse: plain X


def test_an_unplugged_mouse_frees_its_player_for_the_next_one():
    a = mouse_touch.Assigner(players=2)
    a.note("m1", _button(1))
    a.note("m2", _button(1))
    assert a.release("m1") == 1
    assert a.note("m3", _button(1)) == 1


def _input_node(root: Path, event: str, number: int, name: str, rel: str, key: str) -> None:
    dev = root / f"devices/virtual/input/input{number}"
    (dev / "capabilities").mkdir(parents=True)
    (dev / "name").write_text(name + "\n")
    (dev / "capabilities/rel").write_text(rel + "\n")
    (dev / "capabilities/key").write_text(key + "\n")
    node = root / "class/input" / event
    node.mkdir(parents=True)
    os.symlink(dev, node / "device")


def test_find_mice_keeps_mice_oldest_first_and_skips_keyboards_and_our_own(tmp_path):
    left = "1f0000 0 0 0 0"                            # BTN_LEFT..BTN_EXTRA
    keyboard = "1f0000 0 0 0 0 ffffffff"               # + letter keys (KEY_A = 30)
    _input_node(tmp_path, "event9", 21, "USB Mouse", "103", left)
    _input_node(tmp_path, "event4", 4, "Keyboard Mouse", "1943", left)
    _input_node(tmp_path, "event5", 5, "Keyboard", "1040", keyboard)
    _input_node(tmp_path, "event6", 30, "gc-touch-p2", "3", left)
    _input_node(tmp_path, "event7", 7, "Power Button", "0", "0")
    found = inputdev.find_mice(tmp_path)
    assert [(n, path, name) for n, path, name in found] == [
        (4, "/dev/input/event4", "Keyboard Mouse"), (21, "/dev/input/event9", "USB Mouse")]


def test_capability_bitmaps_are_read_as_64_bit_words():
    assert inputdev._bits("3") == {0, 1}
    assert inputdev._bits("1f0000 0 0 0 0") == set(range(272, 277))  # BTN_LEFT = 0x110


class _FakeArrows:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def park_system_pointer(self, width, height):
        self.calls += 1
        return self.answers.pop(0)


class _FakeX11:
    def screen_size(self):
        return 1920, 1080


def test_x_pointer_goes_back_to_the_corner_after_a_touch_never_during_it():
    """Each touch moves X's own arrow to the touch point; it stayed there."""
    router = mouse_touch.MouseRouter(players=2)
    router.x11, router.arrows = _FakeX11(), _FakeArrows([False, True])
    router.pointers = {1: mouse_touch.Pointer((0, 0, 960, 1080))}
    router._park_due = True
    router.pointers[1].pressed = True
    router._park_pointer()
    assert router.arrows.calls == 0                   # mid-drag: left under the arrow
    router.pointers[1].pressed = False
    router._park_pointer()
    assert router._park_due                           # warped, not confirmed yet
    router._park_pointer()
    assert not router._park_due and router.arrows.calls == 2


def test_the_arrow_never_reaches_the_top_screen():
    """The top screen takes no touch: each arrow stays on its own bottom screen."""
    area = mouse_touch.touch_area(2, 2, (1920, 1080))
    assert area == (960, 540, 720, 540)                # player 2's touch window
    p = mouse_touch.Pointer(area, gain=1.0)
    assert p.position == (1320, 810)                   # starts mid touch screen
    p.feed(_move(-5000, -5000))
    assert p.position == (960, 540)                    # clamped, not wrapped
    p.feed(_move(5000, 5000))
    assert p.position == (1679, 1079)
