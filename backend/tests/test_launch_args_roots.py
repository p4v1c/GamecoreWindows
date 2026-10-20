"""What a relative path in a launcher resolves against.

The measurement that produced this file (issue #36): one tile shipped
`args = "lib/<emu>/<emu>.exe --fullscreen=true"` — a bare relative path, and the
only one in the catalogue. Nothing resolved it. `_expand` in services/systems.py
rewrites `@…@` tokens and returns anything else untouched; `resolve_args` only
substitutes `@APPID@`; `process_manager.launch` hands the string to
`command_line.split_args` and starts the game with no `cwd=`. So the child
inherited the backend's working directory, which is `WorkingDirectory=$GAMECORE_PATH`
in the systemd unit arch.sh writes.

It worked. It worked because three independent places happened to agree —
the unit's WorkingDirectory, `providers.py`'s `ctx.gamecore_path / spec["dest"]`
where the installer puts the binary, and the string in the pack — and because
nothing said so anywhere. The failure that would have followed is the worst
shape available: the roots split (P12), someone reads `uninstall.sh`, which
lists `lib` beside `emu|config|assets` as something to preserve, concludes it is
player data, moves it to the data root, and the tile launches wine against a
path that no longer exists. Wine reports it, the backend does not, and the
screen shows a game that started and did nothing.

The same row already carries a second relative path, `romsPath`, which IS
resolved — `paths.resolve_data_path`, against the DATA root, with a test of its
own. Two relative paths, one row, two different roots, one of them by accident.

These tests pin the token down instead. They fail on the pre-fix catalogue.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import systems as systems_router  # noqa: E402
from backend.services.catalog import load_catalog  # noqa: E402
from backend.services.command_line import split_args  # noqa: E402

CATALOG = ROOT / "catalog"
LOCAL = ROOT / "config" / "catalog.d"


@pytest.fixture(scope="module")
def packs():
    return load_catalog(CATALOG, LOCAL)


def _path_like(token: str) -> bool:
    """A launcher argument that names a place on this filesystem.

    A URL is not one, and `--kiosk https://…` is a real argument in this
    catalogue — reading it as a path is how this check would start failing on
    tiles that are perfectly correct.
    """
    return "/" in token and "://" not in token


def test_no_launcher_argument_is_a_bare_relative_path(packs):
    """Absolute, or rooted on a token. Never bare.

    A bare relative path is not wrong so much as unanswerable: it resolves
    against whatever directory the backend was started in, which is a property
    of the systemd unit and not of the catalogue. `@GAMECORE_PATH@` says which
    root, in the file that knows.
    """
    offenders = []
    checked = 0
    for pack in packs.values():
        args = (pack.data.get("launch") or {}).get("args")
        if not args:
            continue
        for token in shlex.split(args):
            if not _path_like(token):
                continue
            checked += 1
            if not (token.startswith("/") or token.startswith("@")):
                offenders.append(f"{pack.id}: {token!r}")

    # Without this the test would pass by examining nothing — which is exactly
    # how two earlier tests in this repository went green while asserting on an
    # empty set.
    assert checked >= 3, (
        f"only {checked} path-like launcher argument(s) examined — the catalogue "
        f"changed shape and this test has stopped looking at anything")
    assert not offenders, (
        "these launcher arguments resolve against the backend's working "
        "directory rather than a named root: " + ", ".join(offenders))


def test_the_two_root_tokens_resolve_to_the_roots_they_name(monkeypatch):
    r"""With the roots genuinely split, and Windows-shaped: backslashes that
    POSIX `shlex.split` used to eat (`C:\Program Files` became `C:Program Files`).

    Both roots are from-imports in services/systems.py, bound at import time:
    `paths.use_roots()` does not reach them, so they are patched there.
    """
    code, data = Path(r"C:\Program Files\GameCore"), Path(r"D:\GameCoreData")
    monkeypatch.setattr(systems_router, "GAMECORE_ROOT", code)
    monkeypatch.setattr(systems_router, "GAMECORE_DATA", data)

    rows = systems_router._expand([{
        "id": "probe",
        "path": r"C:\Emulators\probe.exe",
        "args": "'@GAMECORE_PATH@/lib/probe/probe.exe' @GAMECORE_DATA@/somewhere",
    }])
    tokens = split_args(rows[0]["args"])

    assert tokens[0] == r"C:\Program Files\GameCore/lib/probe/probe.exe"
    assert tokens[1] == r"D:\GameCoreData/somewhere"


def test_the_code_token_survives_a_root_that_contains_a_space(monkeypatch):
    """Why the token is quoted in the pack and not left bare: the args are
    split before they reach the emulator, so an unquoted token expanding to a
    root with a space in it becomes two arguments."""
    monkeypatch.setattr(systems_router, "GAMECORE_ROOT", Path(r"C:\Program Files\GameCore"))
    monkeypatch.setattr(systems_router, "GAMECORE_DATA", Path(r"C:\Program Files\GameCore"))

    quoted = systems_router._expand(
        [{"args": "'@GAMECORE_PATH@/lib/probe/probe.exe' --flag"}])[0]["args"]
    assert split_args(quoted) == [r"C:\Program Files\GameCore/lib/probe/probe.exe", "--flag"]

    bare = systems_router._expand(
        [{"args": "@GAMECORE_PATH@/lib/probe/probe.exe --flag"}])[0]["args"]
    assert split_args(bare) == [r"C:\Program", r"Files\GameCore/lib/probe/probe.exe", "--flag"]


def test_a_bare_relative_argument_is_resolved_by_nobody(monkeypatch):
    """The measurement itself, kept executable.

    This is the property that made the bare path work by accident, and the
    reason the fix had to be a token rather than a resolver: `_expand` is not a
    path resolver and must not become one — an absolute path already in a box's
    systems.json is somebody's deliberate choice, and rewriting it would break
    the operator who typed it.
    """
    monkeypatch.setattr(systems_router, "GAMECORE_ROOT", Path("/opt/gamecore"))
    monkeypatch.setattr(systems_router, "GAMECORE_DATA", Path("/userdata"))

    out = systems_router._expand([{"args": "lib/probe/probe.exe --flag"}])[0]["args"]
    assert out == "lib/probe/probe.exe --flag", (
        "if this ever changes, a relative launcher argument acquired a meaning "
        "and issue #36 needs revisiting")
