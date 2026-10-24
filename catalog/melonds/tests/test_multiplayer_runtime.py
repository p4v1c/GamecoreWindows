"""melonDS local multiplayer, runtime half: window layout, menu matching, the
AT-SPI wire format, and the launcher's promise never to outlive melonDS's
start-up by waiting on a dead process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from backend.tests.linux_until_port import linux_until_port_module

linux_until_port_module(11)  # launcher imports fcntl: evdev and uinput

HERE = Path(__file__).resolve().parent
MP = HERE.parent / "multiplayer"
sys.path.insert(0, str(MP))

import atspi      # noqa: E402
import launcher   # noqa: E402
import windows    # noqa: E402


def test_columns_touch_and_fill_the_height():
    for players in (2, 3, 4):
        cols = windows.columns(players, 1920, 1080)
        assert len(cols) == players
        for (x1, _y1, w1, _h1), (x2, _y2, _w2, _h2) in zip(cols, cols[1:]):
            assert x1 + w1 == x2                      # no gap between players
        assert all(y == 0 and h == 1080 for _x, y, _w, h in cols)
        assert cols[0][0] >= 0 and cols[-1][0] + cols[-1][2] <= 1920


def test_four_players_use_the_whole_width():
    cols = windows.columns(4, 1920, 1080)
    assert cols[0][0] == 0 and cols[-1][0] + cols[-1][2] == 1920


def test_two_players_keep_the_native_shape_and_are_centred():
    (x1, _y, w, _h), (x2, _, _, _) = windows.columns(2, 1920, 1080)
    assert w == 720
    assert x1 == 1920 - (x2 + w)


def test_top_screen_window_above_the_touch_screen_window():
    col = (480, 0, 480, 1080)
    assert windows.screen_rect(col, 1) == (480, 0, 480, 540)
    assert windows.screen_rect(col, 2) == (480, 540, 480, 540)


def test_player_and_window_come_from_the_title():
    assert windows.title_ids("[p3:w2] [59/60] melonDS 1.1") == (3, 2)
    assert windows.title_ids("[p3] melonDS 1.1") == (3, 1)
    assert windows.title_ids("[w2] melonDS 1.1") == (1, 2)
    assert windows.title_ids("melonDS 1.1") == (1, 1)
    assert windows.title_ids("Firefox") is None


def test_recent_entry_one_matches_even_shortened_and_with_ampersands():
    match = launcher.recent_rom_matcher("/userdata/emu/melonds/Mario & Luigi (Europe).nds")
    assert match("1.  /userdata/emu/melonds/Mario  Luigi (Europe).nds")
    assert match("1.  /userdata...melonds/Mario  Luigi (Europe).nds")
    assert not match("2.  /userdata/emu/melonds/Mario  Luigi (Europe).nds")
    assert not match("1.  /opt/x/Other.nds")


def test_recent_entry_of_a_zipped_rom_names_the_member():
    match = launcher.recent_rom_matcher("/userdata/emu/melonds/Game (Europe).zip")
    assert match("1.  /userdata/emu/melonds/Game (Europe).zip|Game (Europe).nds")
    assert not match("1.  /userdata/emu/melonds/Game (Europe) (Rev 1).zip|Game.nds")


def test_gvariant_replies_parse():
    assert atspi.parse_gvariant("(<'melonDS'>,)") == ("melonDS",)
    assert atspi.parse_gvariant('(<"Toys\'R\'Us">,)') == ("Toys'R'Us",)
    assert atspi.parse_gvariant("(true,)") == (True,)
    kids = atspi.parse_gvariant("([(':1.44', objectpath '/a/1'), (':1.44', '/a/2')],)")
    assert kids == ([(":1.44", "/a/1"), (":1.44", "/a/2")],)


def test_launcher_returns_melonds_exit_code_without_waiting_out_the_timeout(tmp_path):
    """A melonDS that dies at start-up must end the session at once."""
    env = {**os.environ, "HOME": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, str(MP / "launcher.py"), "--players", "2", "--",
         sys.executable, "-c", "import sys; sys.exit(3)", "/roms/Game.nds"],
        env=env, timeout=launcher.APP_TIMEOUT / 2)
    assert done.returncode == 3
    assert "multiplayer setup stopped" in (tmp_path / ".cache/gamecore/melonds-multiplayer.log").read_text()


def test_launcher_logs_where_gamecore_says(tmp_path):
    """GameCore passes its logs directory to what it launches."""
    logs = tmp_path / "logs" / "packs" / "melonds"
    env = {**os.environ, "HOME": str(tmp_path), "GAMECORE_LOG_DIR": str(logs)}
    subprocess.run(
        [sys.executable, str(MP / "launcher.py"), "--players", "2", "--",
         sys.executable, "-c", "import sys; sys.exit(3)", "/roms/Game.nds"],
        env=env, timeout=launcher.APP_TIMEOUT / 2)
    assert "multiplayer setup stopped" in (logs / "melonds-multiplayer.log").read_text()


def test_an_x_error_does_not_end_the_launcher():
    """Xlib's default handler exits; a launcher that exits ends the session."""
    assert windows._ignore_x_error(None, None) == 0



def test_launcher_writes_the_instances_before_starting_melonds(tmp_path):
    """setup.prepare runs in the launcher, from GameCore's own tree."""
    config = tmp_path / "melonDS.toml"
    config.write_text("[Instance0]\nJoystickID = 0\n")
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"target": str(config), "state": str(tmp_path / "solo.json"),
                               "snap_dir": str(tmp_path), "app_id": "", "players": []}))
    log_file = tmp_path / ".cache/gamecore/melonds-multiplayer.log"
    started = "import sys; sys.exit(0 if 'instances written' in open(sys.argv[1]).read() else 4)"
    done = subprocess.run(
        [sys.executable, str(MP / "launcher.py"), "--players", "2", f"--prepare={job}",
         "--", sys.executable, "-c", started, str(log_file)],
        env={**os.environ, "HOME": str(tmp_path)}, timeout=launcher.APP_TIMEOUT / 2)
    assert done.returncode == 0
