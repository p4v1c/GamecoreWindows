"""A launcher's `args` string, split into the argv a game is started with.

The catalogue writes `args` with shell quoting (`'@GAMECORE_PATH@/x.exe' -f`),
and a root expanded into it may hold a space. POSIX `shlex.split` honours the
quotes but reads a backslash as an escape, so `C:\\Games\\x.iso` became
`C:Gamesx.iso`. Here quotes group and are removed, and a backslash is a
character like any other: the Windows path separator.
"""
from __future__ import annotations

import shlex


def split_args(text: str) -> list[str]:
    """Split `text` on whitespace; '…' and "…" group words; backslashes kept."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    return list(lexer)


def launch_argv(exec_path: str, exec_args: str, rom_path: str = "") -> list[str]:
    """The full argv: the executable, the split args, then the ROM as one word."""
    argv = [exec_path, *split_args(exec_args or "")]
    if rom_path:
        argv.append(rom_path)
    return argv
