r"""A launcher's `args` string to argv, on Windows paths.

POSIX `shlex.split` read a backslash as an escape: `C:\Games\x.iso` reached
the emulator as `C:Gamesx.iso`. The catalogue's shell quoting must still work.
"""
from __future__ import annotations

from backend.services.command_line import launch_argv, split_args


def test_backslashes_are_kept():
    assert split_args(r"-L C:\Emulators\cores\core.dll") == ["-L", r"C:\Emulators\cores\core.dll"]


def test_quotes_group_a_path_with_spaces_and_are_removed():
    assert split_args(r'--config "C:\Program Files\Emu\emu.ini" -f') == [
        "--config", r"C:\Program Files\Emu\emu.ini", "-f"]
    assert split_args(r"'C:\Program Files\GameCore/lib/x.exe' --flag") == [
        r"C:\Program Files\GameCore/lib/x.exe", "--flag"]


def test_a_trailing_backslash_does_not_swallow_the_quote():
    assert split_args(r'"C:\ROMs\" -f') == ["C:\\ROMs\\", "-f"]


def test_a_hash_is_an_argument_not_a_comment():
    assert split_args("--title #1") == ["--title", "#1"]


def test_empty_args_give_no_argument():
    assert split_args("") == []
    assert launch_argv(r"C:\Emu\emu.exe", "") == [r"C:\Emu\emu.exe"]


def test_the_rom_is_one_argument_whatever_it_contains():
    rom = r"E:\ROMS\gamecube\Zelda - Wind Waker (Europe) [!].iso"
    assert launch_argv(r"C:\Emu\Dolphin.exe", "-b -e", rom) == [
        r"C:\Emu\Dolphin.exe", "-b", "-e", rom]
