"""The L3 layout daemon leaves a local multiplayer melonDS alone: the
launcher marks the process, the daemon reads the mark back."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MP = HERE.parent / "multiplayer"
sys.path.insert(0, str(HERE.parent / "files"))

import melonds_config  # noqa: E402


def _environ(root: Path, pid: int, *items: bytes) -> None:
    (root / str(pid)).mkdir()
    (root / str(pid) / "environ").write_bytes(b"\0".join(items) + b"\0")


def test_l3_is_ignored_only_while_several_players_share_melonds(tmp_path):
    _environ(tmp_path, 10, b"HOME=/home/x", b"GAMECORE_MELONDS_PLAYERS=3")
    _environ(tmp_path, 11, b"HOME=/home/x")
    _environ(tmp_path, 12, b"GAMECORE_MELONDS_PLAYERS=1")
    assert melonds_config.in_multiplayer(10, str(tmp_path))
    assert not melonds_config.in_multiplayer(11, str(tmp_path))      # solo launch
    assert not melonds_config.in_multiplayer(12, str(tmp_path))
    assert not melonds_config.in_multiplayer(99, str(tmp_path))      # already gone


@pytest.mark.linux_until_port(7)
def test_the_launcher_marks_melonds_with_the_player_count(tmp_path):
    check = "import os, sys; sys.exit(0 if os.environ.get('GAMECORE_MELONDS_PLAYERS') == '2' else 5)"
    done = subprocess.run(
        [sys.executable, str(MP / "launcher.py"), "--players", "2", "--",
         sys.executable, "-c", check, "/roms/Game.nds"],
        env={**os.environ, "HOME": str(tmp_path)}, timeout=20)
    assert done.returncode == 0
