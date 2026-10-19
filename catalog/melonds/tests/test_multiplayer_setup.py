"""melonDS local multiplayer, backend half: when it starts, what it writes.

One pad must change nothing at all; two to four write one instance per pad
and wrap melonDS in the launcher.
"""
from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load("tested_melonds_mp_setup", HERE.parent / "multiplayer" / "setup.py")
generator = _load("tested_melonds_generator", HERE.parent / "generator.py")

DS4 = {"vendor": "054c", "product": "09cc", "name": "Wireless Controller"}
XBOX = {"vendor": "045e", "product": "02fd", "name": "Xbox Wireless Controller"}
TOML = """[Instance0]
JoystickID = 0
SaveFilePath = ""

[Instance0.Joystick]
A = 0
B = 1
L = 9
R = 10
Start = 6
Select = 4
Up = 257
Down = 260
Left = 264
Right = 258

[Instance0.Keyboard]
HK_FullscreenToggle = -1

[Instance0.Window0]
Enabled = true
"""


def _opts(tmp_path: Path) -> dict:
    target = tmp_path / "melonDS.toml"
    target.write_text(TOML)
    return {"target": target, "home": tmp_path, "snap_dir": tmp_path / "snaps",
            "app_id": "net.kuribo64.melonDS"}


def _fake_synth(pad):
    # An Xbox pad on SDL: shoulders b4/b5, Start b7, Select b6.
    return {"L": 4, "R": 5, "Start": 7, "Select": 6}, "SDL live"


def _launch(opts, players, rom="/roms/Game.nds"):
    return setup.launch_command(
        rom_path=rom, exec_path="flatpak", exec_args="run net.kuribo64.melonDS -f",
        players=players, opts=opts)


def _launch_and_prepare(opts, players):
    """The backend's hook, then the launcher's part, as on the box."""
    result = _launch(opts, players)
    return result, setup.prepare(opts["home"] / setup.JOB_FILE, synth=_fake_synth,
                                 set_keys=generator.set_joystick_keys)


def test_one_pad_writes_nothing_and_keeps_the_command(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: 1 / 0)
    opts = _opts(tmp_path)
    assert _launch(opts, [{"player": 1, "key": "aa:bb:cc:dd:ee:01", **DS4}]) is None
    assert opts["target"].read_text() == TOML


def test_no_rom_is_never_multiplayer(tmp_path):
    players = [{"player": n, "key": f"k{n}", **DS4} for n in (1, 2)]
    assert _launch(_opts(tmp_path), players, rom="") is None


def test_two_pads_wrap_melonds_without_fullscreen(tmp_path, monkeypatch):
    players = [{"player": n, "key": f"k{n}", **DS4} for n in (1, 2)]
    opts = _opts(tmp_path)
    exe, args = _launch(opts, players)
    argv = shlex.split(args)
    assert exe == sys.executable
    assert argv[:5] == [str(setup.LAUNCHER), "--players", "2",
                        f"--prepare={tmp_path / setup.JOB_FILE}", "--"]
    assert argv[5:] == ["flatpak", "run", setup.A11Y_FLAG, "net.kuribo64.melonDS"]


def test_the_backend_hook_never_probes_sdl(tmp_path, monkeypatch):
    """SDL probes cost ~1 s per pad model; three pads overran the launch's 3 s
    budget and melonDS started solo, in fullscreen."""
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: 1 / 0)
    monkeypatch.setattr(setup, "write_instances", lambda *a: 1 / 0)
    opts = _opts(tmp_path)
    players = [{"player": 1, "key": "k1", **DS4}, {"player": 2, "key": "k2", **XBOX}]
    assert _launch(opts, players) is not None
    assert opts["target"].read_text() == TOML


def test_more_than_four_pads_open_four_instances(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: [])
    players = [{"player": n, "key": f"k{n}", **DS4} for n in range(1, 6)]
    _exe, args = _launch(_opts(tmp_path), players)
    assert shlex.split(args)[2] == "4"


def test_each_player_gets_its_sdl_index_and_players_2_on_their_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: [(0, "/dev/hidraw3"),
                                                                (1, "/dev/input/event7")])
    monkeypatch.setattr(setup, "device_ids", lambda path: {
        "/dev/hidraw3": {"/dev/hidraw3", "11:22:33:44:55:66"},
        "/dev/input/event7": {"/dev/input/event7", "/sys/devices/x/hid1"},
        "/dev/input/event5": {"/dev/input/event5", "/sys/devices/x/hid1"},
    }.get(path, {path}))
    opts = _opts(tmp_path)
    # Player 1 is the USB Xbox (keyed by its event node), player 2 the BT DS4.
    players = [{"player": 1, "key": "/dev/input/event5", **XBOX},
               {"player": 2, "key": "11:22:33:44:55:66", **DS4}]
    assert _launch_and_prepare(opts, players)[1] == {1: 1, 2: 0}
    text = opts["target"].read_text()
    from backend.services.configgen.helpers.ini import section
    assert "JoystickID = 1" in section(text, "Instance0")
    assert "JoystickID = 0" in section(text, "Instance1")
    p2 = section(text, "Instance1.Joystick")
    assert "L = 4" in p2 and "Start = 7" in p2 and "A = 0" in p2
    # Player 1's bindings belong to the connect-time profiling, untouched here.
    assert section(text, "Instance0.Joystick") == section(TOML, "Instance0.Joystick")


def test_a_captured_mapping_beats_the_synthesis(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: [])
    opts = _opts(tmp_path)
    snap = opts["snap_dir"] / "melonds" / "045e_02fd.snap"
    snap.parent.mkdir(parents=True)
    snap.write_text("[Instance0.Joystick]\nA = 1\nB = 0\nL = 6\nR = 7\n")
    _launch_and_prepare(opts, [{"player": 1, "key": "k1", **DS4}, {"player": 2, "key": "k2", **XBOX}])
    from backend.services.configgen.helpers.ini import section
    assert section(opts["target"].read_text(), "Instance1.Joystick") == "A = 1\nB = 0\nL = 6\nR = 7\n"


def test_a_slot_gap_does_not_open_an_empty_window(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: [])
    players = [{"player": 1, "key": "k1", **DS4}, {"player": 3, "key": "k3", **DS4}]
    _exe, args = _launch(_opts(tmp_path), players)
    assert shlex.split(args)[2] == "2"


def test_solo_after_multiplayer_gives_instance_1_its_settings_back(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: [(0, "b"), (1, "a")])
    opts = _opts(tmp_path)
    _launch_and_prepare(opts, [{"player": 1, "key": "a", **DS4}, {"player": 2, "key": "b", **DS4}])
    from backend.services.configgen.helpers.ini import section
    text = opts["target"].read_text()
    assert "JoystickID = 1" in section(text, "Instance0")
    assert f"HK_FullscreenToggle = {setup.FULLSCREEN_KEY}" in section(text, "Instance0.Keyboard")
    assert f"HK_FullscreenToggle = {setup.FULLSCREEN_KEY}" in section(text, "Instance1.Keyboard")
    assert "ScreenSizing = 4" in section(text, "Instance0.Window0")
    assert "Enabled = true" in section(text, "Instance0.Window1")
    assert "ScreenSizing = 5" in section(text, "Instance1.Window1")
    _launch(opts, [{"player": 1, "key": "a", **DS4}])
    text = opts["target"].read_text()
    for header in ("Instance0", "Instance0.Keyboard", "Instance0.Joystick"):
        assert section(text, header).strip() == section(TOML, header).strip()
    # Solo never opens the second window, though the seed had no Window1 at all.
    assert "Enabled = false" in section(text, "Instance0.Window1")
    assert "ScreenSizing = 0" in section(text, "Instance0.Window0")
    assert not (tmp_path / setup.STATE_FILE).exists()


def test_autoconfig_off_launches_multiplayer_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "sdl_joysticks", lambda app_id: 1 / 0)
    players = [{"player": n, "key": f"k{n}", **DS4} for n in (1, 2)]
    assert _launch(None, players) is not None


@pytest.mark.linux_until_port(7)
def test_device_ids_join_hidraw_and_event_nodes_of_one_pad(tmp_path):
    hid = tmp_path / "devices/0005:054C:09CC.0003"
    hid.mkdir(parents=True)
    (hid / "uevent").write_text("HID_ID=0005:0000054C:000009CC\nHID_UNIQ=A4:AE:12:00:00:01\n")
    (tmp_path / "class/hidraw/hidraw1").mkdir(parents=True)
    (tmp_path / "class/hidraw/hidraw1/device").symlink_to(hid)
    node = tmp_path / "devices/0005:054C:09CC.0003/input/input9"
    node.mkdir(parents=True)
    (node / "uniq").write_text("a4:ae:12:00:00:01\n")
    (node / "device").symlink_to(hid)
    (tmp_path / "class/input/event4").mkdir(parents=True)
    (tmp_path / "class/input/event4/device").symlink_to(node)

    raw = setup.device_ids("/dev/hidraw1", tmp_path)
    ev = setup.device_ids("/dev/input/event4", tmp_path)
    assert str(hid) in raw and str(hid) in ev
    assert "a4:ae:12:00:00:01" in raw and "a4:ae:12:00:00:01" in ev


def test_a_pad_sdl_does_not_list_keeps_its_joystick_id():
    players = [{"player": 1, "key": "a"}, {"player": 2, "key": "gone"}]
    assert setup.assign_joysticks(players, [(0, "a")], ids_of=lambda p: {p}) == {1: 0}


def test_a_new_player_gets_a_blank_save_not_a_copy_of_player_one(tmp_path):
    """melonDS loads player 1's .sav when .sav.2 is missing: P2 played the owner's game."""
    rom = tmp_path / "Pokemon - Platinum Version (Europe).nds"
    rom.write_bytes(b"rom")
    (tmp_path / "Pokemon - Platinum Version (Europe).sav").write_bytes(b"\x12" * 512)
    made = setup.blank_player_saves(str(rom), 3)
    assert [p.name for p in made] == ["Pokemon - Platinum Version (Europe).sav.2",
                                      "Pokemon - Platinum Version (Europe).sav.3"]
    assert made[0].read_bytes() == b"\xff" * 512
    assert (tmp_path / "Pokemon - Platinum Version (Europe).sav").read_bytes() == b"\x12" * 512


def test_an_existing_player_save_is_never_overwritten(tmp_path):
    rom = tmp_path / "Game.nds"
    rom.write_bytes(b"rom")
    (tmp_path / "Game.sav").write_bytes(b"\x12" * 64)
    (tmp_path / "Game.sav.2").write_bytes(b"\x34" * 64)
    assert setup.blank_player_saves(str(rom), 2) == []
    assert (tmp_path / "Game.sav.2").read_bytes() == b"\x34" * 64


def test_no_player_one_save_leaves_melonds_to_start_everyone_fresh(tmp_path):
    rom = tmp_path / "Game.nds"
    rom.write_bytes(b"rom")
    assert setup.blank_player_saves(str(rom), 4) == []
    assert not list(tmp_path.glob("Game.sav*"))


def test_a_player_save_folder_set_in_the_config_is_used(tmp_path):
    rom = tmp_path / "roms" / "Game.nds"
    rom.parent.mkdir()
    rom.write_bytes(b"rom")
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "Game.sav").write_bytes(b"\x12" * 8)
    config = f'[Instance1]\nSaveFilePath = "{saves}"\n'
    assert [p.parent for p in setup.blank_player_saves(str(rom), 2, config)] == [saves]


def test_archives_are_left_to_melonds(tmp_path):
    assert setup.blank_player_saves(str(tmp_path / "Game.zip"), 2) == []
