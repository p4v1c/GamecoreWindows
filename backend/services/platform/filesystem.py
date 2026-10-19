"""Directory links on NTFS: junctions, because symlinks need admin.

A Windows symlink needs the SeCreateSymbolicLink privilege (an elevated
process, or Developer Mode); the backend runs as the console user and has
neither (`WinError 1314`). A directory junction needs no privilege, works on
every local NTFS volume and is followed by every program, emulators included.

Limits a caller must know: a junction targets a local absolute directory that
exists when the link is made (no relative target, no network share).
"""
from __future__ import annotations

import _winapi
import os
from pathlib import Path

# What `os.readlink` puts in front of a junction's target.
_NT_PREFIX = "\\\\?\\"


def link_dir(link: Path, target: Path) -> None:
    """Make `link` a junction to the existing directory `target`.

    Raises FileExistsError when `link` is taken, FileNotFoundError when
    `target` is missing: a junction cannot dangle at birth.
    """
    if os.path.lexists(link):
        raise FileExistsError(f"{link} already exists")
    target = Path(target).absolute()
    if not target.is_dir():
        raise FileNotFoundError(f"{target} is not a directory")
    _winapi.CreateJunction(str(target), str(link))


def is_link(path: Path) -> bool:
    """True for a junction or a symlink, even when its target is gone.

    `Path.is_symlink()` is False for a junction, so a check written for Linux
    would take our own link for a real folder and move it aside.
    """
    path = Path(path)
    return path.is_symlink() or path.is_junction()


def link_target(path: Path) -> Path:
    """Where the junction or symlink `path` points, without the `\\\\?\\` prefix."""
    raw = os.readlink(path)
    if raw.startswith(_NT_PREFIX):
        raw = raw[len(_NT_PREFIX):]
    return Path(raw)
