"""The Xbox pad mirror: which pads, how their events translate, and how a
player's virtual pad survives the real one dropping. No real uinput here."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from backend.tests.linux_until_port import linux_until_port_module

linux_until_port_module(11)  # imports fcntl: uinput

PACK = Path(__file__).resolve().parents[1]
REPO = PACK.parents[1]
sys.path.insert(0, str(PACK / "files"))
spec = importlib.util.spec_from_file_location("test_lutris_xpad", PACK / "files" / "lutris_xpad.py")
xpad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xpad)

# hid-playstation's DualShock 4: SOUTH..THUMBR, sticks, analog triggers, hat.
DS4_KEYS = "7fdb000000000000 0 0 0 0"
DS4_ABS = "3003f"


def add_node(sys_root, event, number, name, vendor, product, keys="0", abs_="0", phys=""):
    device = sys_root / "devices" / f"input{number}"
    (device / "id").mkdir(parents=True)
    (device / "capabilities").mkdir()
    (device / "name").write_text(name + "\n")
    (device / "phys").write_text(phys + "\n")
    (device / "id" / "vendor").write_text(f"{vendor:04x}\n")
    (device / "id" / "product").write_text(f"{product:04x}\n")
    (device / "capabilities" / "key").write_text(keys + "\n")
    (device / "capabilities" / "abs").write_text(abs_ + "\n")
    node = sys_root / "class" / "input" / event
    node.mkdir(parents=True)
    (node / "device").symlink_to(device)


def test_only_standard_non_xbox_pads_are_mirrored_in_connection_order(tmp_path):
    add_node(tmp_path, "event20", 30, "Wireless Controller", 0x054C, 0x0CE6, DS4_KEYS, DS4_ABS)
    add_node(tmp_path, "event13", 12, "Wireless Controller", 0x054C, 0x09CC, DS4_KEYS, DS4_ABS)
    add_node(tmp_path, "event14", 13, "Wireless Controller Motion Sensors", 0x054C, 0x09CC,
             "0", "3f")
    add_node(tmp_path, "event15", 14, "Xbox Wireless Controller", 0x045E, 0x0B13, DS4_KEYS, DS4_ABS)
    add_node(tmp_path, "event16", 15, "Steam Virtual Gamepad", 0x28DE, 0x11FF, DS4_KEYS, DS4_ABS)
    add_node(tmp_path, "event17", 16, "Microsoft X-Box 360 pad", 0x1234, 0x5678, DS4_KEYS, DS4_ABS,
             phys="gamecore-virtual-pad/1")
    # Right stick on Z/RZ (hid-generic): mirrored wrong, so left to Wine.
    add_node(tmp_path, "event18", 17, "USB Gamepad", 0x0079, 0x0006, DS4_KEYS, "27")

    pads = xpad.find_pads(tmp_path)

    assert [(p.path, p.product) for p in pads] == [
        ("/dev/input/event13", 0x09CC), ("/dev/input/event20", 0x0CE6)]


def test_hidden_pads_are_every_sony_pad_plus_those_plugged_at_launch():
    pads = [xpad.Pad(1, "/dev/input/event3", "Pro Controller", 0x057E, 0x2009),
            xpad.Pad(2, "/dev/input/event4", "Wireless Controller", 0x054C, 0x09CC)]
    ids = xpad.hidden_ids(pads)
    assert ids[-1] == (0x057E, 0x2009)
    assert len(ids) == len(set(ids)) == len(xpad.SONY_PADS) + 1


def test_the_hide_env_keeps_an_ignore_list_already_set():
    env = xpad.hide_env([(0x054C, 0x09CC)], {"SDL_GAMECONTROLLER_IGNORE_DEVICES": "0x28de/0x11ff"})
    assert env == {"SDL_GAMECONTROLLER_IGNORE_DEVICES": "0x28de/0x11ff,0x054c/0x09cc",
                   "PROTON_DISABLE_HIDRAW": "0x054c/0x09cc"}


DS4_RANGES = {a: (0, 255) for a in (*xpad.STICKS, *xpad.TRIGGERS)} | {
    xpad.ABS_HAT0X: (-1, 1), xpad.ABS_HAT0Y: (-1, 1)}


@pytest.mark.parametrize("value, expected", [(0, -32768), (255, 32767), (128, 128), (300, 32767)])
def test_a_ds4_stick_spans_the_whole_xbox_range(value, expected):
    assert xpad.translate(xpad.EV_ABS, xpad.ABS_RY, value, DS4_RANGES) == [
        (xpad.EV_ABS, xpad.ABS_RY, expected)]


def test_triggers_keep_0_to_255_whatever_the_pad_range():
    ranges = {xpad.ABS_Z: (0, 1023)}
    assert xpad.translate(xpad.EV_ABS, xpad.ABS_Z, 1023, ranges) == [(xpad.EV_ABS, xpad.ABS_Z, 255)]
    assert xpad.translate(xpad.EV_ABS, xpad.ABS_Z, 0, ranges) == [(xpad.EV_ABS, xpad.ABS_Z, 0)]


def test_square_is_xbox_x_and_triangle_is_xbox_y():
    """xpad sends X as 0x133 and Y as 0x134; hid-playstation sends square as
    WEST (0x134) and triangle as NORTH (0x133)."""
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_WEST, 1, DS4_RANGES) == [(xpad.EV_KEY, 0x133, 1)]
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_NORTH, 1, DS4_RANGES) == [(xpad.EV_KEY, 0x134, 1)]
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_SOUTH, 0, DS4_RANGES) == [(xpad.EV_KEY, 0x130, 0)]


def test_the_ps_button_never_reaches_the_virtual_pad():
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_MODE, 1, DS4_RANGES) == []
    assert xpad.BTN_MODE in xpad.XBOX_KEYS      # declared: SDL's button numbering


def test_digital_triggers_drive_the_axis_only_when_there_is_no_analog_one():
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_TL2, 1, {}) == [(xpad.EV_ABS, xpad.ABS_Z, 255)]
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_TL2, 1, DS4_RANGES) == []


def test_dpad_buttons_become_the_hat():
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_DPAD_LEFT, 1, {}) == [(xpad.EV_ABS, xpad.ABS_HAT0X, -1)]
    assert xpad.translate(xpad.EV_KEY, xpad.BTN_DPAD_LEFT, 0, {}) == [(xpad.EV_ABS, xpad.ABS_HAT0X, 0)]


def test_motion_and_unknown_axes_are_dropped_and_reports_kept():
    assert xpad.translate(xpad.EV_ABS, 0x28, 5, DS4_RANGES) == []
    assert xpad.translate(xpad.EV_SYN, xpad.SYN_REPORT, 0, {}) == [(xpad.EV_SYN, 0, 0)]


def test_the_pack_and_the_backend_agree_on_the_virtual_pad_mark():
    backend = (REPO / "backend/services/gamepad_devices.py").read_text()
    assert re.search(r'VIRTUAL_PAD_PHYS = "([^"]+)"', backend).group(1) == xpad.VIRTUAL_PHYS


# ── the mirror loop, with fake devices ───────────────────────────────────────

class FakeVirtual:
    made = []

    def __init__(self, player):
        self.player, self.sent, self.closed = player, [], False
        FakeVirtual.made.append(self)

    def emit(self, events):
        self.sent.extend(events)

    def close(self):
        self.closed = True


class FakeSource:
    def __init__(self, pad):
        self.pad, self.fd, self.ranges, self.gone = pad, 0, DS4_RANGES, False

    def read(self):
        if self.gone:
            raise OSError(19, "No such device")
        return [(xpad.EV_KEY, xpad.BTN_SOUTH, 1)]

    def close(self):
        pass


@pytest.fixture
def mirror(monkeypatch):
    plugged = []
    FakeVirtual.made = []
    monkeypatch.setattr(xpad, "VirtualPad", FakeVirtual)
    monkeypatch.setattr(xpad, "Source", FakeSource)
    monkeypatch.setattr(xpad, "find_pads", lambda root: list(plugged))
    m = xpad.Mirror([(0x054C, 0x09CC)], log=lambda line: None)
    return m, plugged


def ds4(n):
    return xpad.Pad(n, f"/dev/input/event{n}", "Wireless Controller", 0x054C, 0x09CC)


def test_a_pad_that_drops_leaves_its_player_neutral_and_gets_it_back(mirror):
    m, plugged = mirror
    plugged[:] = [ds4(1), ds4(2)]
    m._attach()
    m._pump(0)
    assert FakeVirtual.made[0].sent[-1] == (xpad.EV_KEY, xpad.BTN_SOUTH, 1)

    m.players[0][1].gone = True
    m._pump(0)                                  # must not raise
    assert m.players[0][1] is None
    assert (xpad.EV_KEY, xpad.BTN_SOUTH, 0) in FakeVirtual.made[0].sent

    plugged[:] = [ds4(2), ds4(7)]               # it comes back on a new node
    m._attach()
    assert m.players[0][1].pad.path == "/dev/input/event7"
    assert len(FakeVirtual.made) == 2           # same two virtual pads


def test_a_pad_not_hidden_at_launch_is_not_mirrored(mirror):
    m, plugged = mirror
    plugged[:] = [xpad.Pad(1, "/dev/input/event3", "Pro Controller", 0x057E, 0x2009)]
    m._attach()
    assert m.players == []


def test_no_more_than_four_players(mirror):
    m, plugged = mirror
    plugged[:] = [ds4(n) for n in range(1, 7)]
    m._attach()
    assert len(m.players) == xpad.MAX_PLAYERS


def test_stop_destroys_every_virtual_pad(mirror):
    m, plugged = mirror
    plugged[:] = [ds4(1)]
    m._attach()
    m.stop()
    assert FakeVirtual.made[0].closed and m.players == []
