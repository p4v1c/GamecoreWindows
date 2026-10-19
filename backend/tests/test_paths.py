"""Nothing outside `services/paths.py` may decide where data lives.

The split this file guards is invisible while it works. `GAMECORE_DATA`
defaults to the installation, so a module that builds `GAMECORE_ROOT /
"config" / "x.json"` by hand is byte-for-byte correct today, passes every
test, and runs correctly on every box — right up until the root is mounted
read-only or the data is moved to `/userdata`. Then it fails, at runtime, on
somebody's television, and the traceback names a file rather than the decision
that put it there.

That is not a thing code review reliably catches: the wrong line and the right
line look identical, and there are a dozen of each. So it is checked
mechanically, on the two halves of the rule:

  · no module outside paths.py joins a writable directory onto the code root;
  · no module outside paths.py reads GAMECORE_PATH or GAMECORE_DATA from the
    environment, which is the other way to mint a second opinion about the
    roots.

The rest of the file asserts the property the OTA release depends on: with
`GAMECORE_DATA` unset, every path resolves to exactly where it resolved
before this change. Zero bytes move, so the update is non-destructive by
construction rather than by inspection.
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from backend.services import paths

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

# Files allowed to know where things are. paths.py IS the decision; config.py
# re-exports it for the imports that predate the split.
#
# gamescrape.py is the one real exception, and it is not laziness: it is run as
# a plain script by install/steps/build-media-index.sh, so a relative import of
# paths.py has no package to resolve against. It reads the environment itself
# and its resolve_index_dir() docstring says why.
_EXEMPT = {
    BACKEND / "services" / "paths.py",
    BACKEND / "config.py",
    BACKEND / "services" / "gamemedia" / "gamescrape.py",
}

# The names a module could hold a code root under.
_CODE_ROOTS = {"GAMECORE_ROOT", "ASSETS_DIR", "REPO", "ROOT"}

# First path segment of every writable location. Joining one of these onto a
# code root is the mistake — `GAMECORE_ROOT / "catalog"` is fine, it is code.
_WRITABLE_SEGMENTS = {"config", "emu", "overlays", "logos", "addons"}


def _sources() -> list[Path]:
    return [p for p in sorted(BACKEND.rglob("*.py"))
            if p not in _EXEMPT
            and "__pycache__" not in p.parts
            and "tests" not in p.relative_to(BACKEND).parts]


def _joins(path: Path) -> list[tuple[int, str]]:
    """Every `<something> / "literal"` expression in the file, unparsed.

    Parsed rather than grepped on purpose: half the modules here discuss
    `config/` and `emu/` at length in their docstrings — that prose is the
    project's memory and must not be what turns this test red. An AST walk
    sees expressions only.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, str)):
            out.append((node.lineno, ast.unparse(node)))
    return out


def test_no_module_builds_a_writable_path_from_the_code_root():
    """The failure this exists to prevent, and how to fix it.

    Add an accessor to `services/paths.py` and call it. If the path really is
    read-only content shipped with the release, it belongs on the code root —
    say so by putting it next to `catalog_dir()` there, not by spelling it out
    at the call site.
    """
    offenders = []
    for src in _sources():
        for lineno, expr in _joins(src):
            head = expr.split("/")[0].strip().split(".")[-1].strip("()")
            if head not in _CODE_ROOTS:
                continue
            segment = expr.rsplit("/", 1)[-1].strip().strip("'\"")
            if segment in _WRITABLE_SEGMENTS:
                offenders.append(f"{src.relative_to(REPO)}:{lineno}: {expr}")
    assert not offenders, (
        "writable locations must come from services/paths.py, not be joined "
        "onto the code root:\n  " + "\n  ".join(offenders))


def test_only_paths_py_reads_the_root_variables_from_the_environment():
    """Two modules reading GAMECORE_DATA is two answers to one question.

    The env vars are read once, at import, and everything downstream caches a
    path built from them. A second reader picks the value up at a different
    moment and cannot be repointed by `paths.use_roots`, which is how a test
    ends up green against a directory the code under test is not using.
    """
    offenders = []
    for src in _sources():
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value in ("GAMECORE_PATH", "GAMECORE_DATA"):
                offenders.append(f"{src.relative_to(REPO)}:{node.lineno}: {node.value!r}")
    assert not offenders, (
        "only services/paths.py may read the root variables:\n  "
        + "\n  ".join(offenders))


def test_no_shell_script_writes_data_under_the_install_root():
    """The same rule, on the half the AST walk cannot see.

    The installers, the updater and the two CLIs build paths as strings, and
    they had a dozen of these: the generated `systems.json`, the ROM
    directories, the credentials, `catalog.d`, the addon registry. Each one was
    correct until the roots differ, and then it writes the player's password
    into a read-only tree.

    Found by sweeping by hand once. Kept found by this.
    """
    scripts = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z",
         "install/*.sh", "install/**/*.sh", "install/bin/*",
         "update/*.sh", "scripts/*.sh"],
        capture_output=True, text=True, check=True).stdout.split("\0")

    bad = re.compile(r"\$\{?(?:GAMECORE_PATH|GC_PATH)\}?/(config|emu|assets/overlays|assets/logos)\b")
    offenders = []
    for rel in filter(None, scripts):
        path = REPO / rel
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue          # prose explaining the split is not the split
            if bad.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        "these write player data under the install root; use GAMECORE_DATA:\n  "
        + "\n  ".join(offenders))


# ── The property the OTA depends on ──────────────────────────────────────────
#
# Spelled out rather than derived from paths._LAYOUT on purpose. Deriving it
# would assert that the table equals itself, and pass no matter what the table
# said. These are the locations an installed box already has on disk, and the
# release must not disagree with them.
_LEGACY = {
    "config":   "config",
    "roms":     "emu",
    "covers":   "emu/covers",
    "media":    "emu/gamemedia",
    "index":    "emu/gamescrape",
    "metadata": "emu/metadata",
    "overlays": "assets/overlays",
    "logos":    "assets/logos",
    "themes":   "config/themes",
}


@pytest.mark.parametrize("name,relative", sorted(_LEGACY.items()))
def test_with_no_data_root_every_path_stays_where_the_box_already_has_it(name, relative):
    """The release is non-destructive by construction.

    `update/linux.sh` rsyncs new code into the install and excludes `emu/` and
    `config/` — the data stays exactly where it was. If any of these resolved
    somewhere else after the update, the box would boot with an empty library
    and no settings, and the rollback would hand the OLD code a tree the NEW
    code had already moved. Hence: nothing moves.
    """
    assert not paths.is_split(), "the suite must run with the roots collapsed"
    assert paths.data_dir(name) == paths.GAMECORE_ROOT / relative


def test_setting_a_data_root_moves_every_writable_path_and_no_code_path():
    """The other half: once pointed at /userdata, nothing writable is left
    behind inside the installation — that is what lets the root go read-only."""
    root, data = Path("/opt/gamecore"), Path("/userdata")
    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    try:
        paths.use_roots(root, data)
        assert paths.is_split()
        for name in _LEGACY:
            assert data in paths.data_dir(name).parents, (
                f"{name} is still inside the installation")
        for code in (paths.catalog_dir(), paths.backend_data_dir(),
                     paths.frontend_dist_dir(), paths.install_bin_dir()):
            assert root in code.parents
    finally:
        paths.use_roots(*before)


def test_a_relative_roms_path_follows_the_data_root():
    """`romsPath` is `emu/duckstation/` in systems.json — relative, and the
    single reason a moved library is a one-variable change rather than a sweep
    through utils, prefetch, playtime_repair and routers/games."""
    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    try:
        paths.use_roots(Path("/opt/gamecore"), Path("/userdata"))
        assert paths.resolve_data_path("emu/duckstation/") == Path("/userdata/emu/duckstation")
        # An absolute path in the file is somebody's deliberate choice.
        assert paths.resolve_data_path("/mnt/usb/roms") == Path("/mnt/usb/roms")
        assert paths.resolve_data_path("") is None
    finally:
        paths.use_roots(*before)


def test_windows_roots_keep_code_and_data_apart():
    """An installed PC: code in Program Files, data on another volume."""
    root, data = Path(r"C:\Program Files\GameCore"), Path(r"D:\GameCoreData")
    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    try:
        paths.use_roots(root, data)
        assert paths.is_split()
        assert paths.config_dir() == Path(r"D:\GameCoreData\config")
        assert paths.roms_root() == Path(r"D:\GameCoreData\emu")
        assert paths.catalog_dir() == Path(r"C:\Program Files\GameCore\catalog")
        # A drive path in systems.json is the owner's choice; a relative one
        # follows the data root, written with either slash.
        assert paths.resolve_data_path(r"E:\Roms\psx") == Path(r"E:\Roms\psx")
        assert paths.resolve_data_path("emu/duckstation/") == data / "emu" / "duckstation"
        assert paths.resolve_data_path(r"emu\duckstation") == data / "emu" / "duckstation"
    finally:
        paths.use_roots(*before)


def test_one_folder_spelled_two_ways_is_not_a_split(tmp_path):
    """NTFS names are case-insensitive: `C:\\GameCore` and `c:/gamecore/` are
    one directory, and calling them split would send writes to the code root."""
    folder = tmp_path / "GameCore"
    folder.mkdir()
    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    try:
        paths.use_roots(folder, Path(str(folder).lower().replace("\\", "/") + "/"))
        assert not paths.is_split()
    finally:
        paths.use_roots(*before)


def test_the_data_root_comes_from_the_environment():
    """Re-imported with GAMECORE_DATA set, because that is how the box will
    run: the installer writes it into the systemd unit, not into code."""
    env = dict(os.environ)
    os.environ["GAMECORE_PATH"] = "/opt/gamecore"
    os.environ["GAMECORE_DATA"] = "/userdata"
    try:
        fresh = importlib.reload(paths)
        assert fresh.GAMECORE_DATA == Path("/userdata")
        assert fresh.config_dir() == Path("/userdata/config")
    finally:
        os.environ.clear()
        os.environ.update(env)
        importlib.reload(paths)


# ── The same rule, in Electron ───────────────────────────────────────────────

def test_electron_reads_the_players_files_from_the_data_root():
    """`electron/main.js` cannot be imported outside Electron, so this reads it.

    It joins `config/overlays.json` onto a root, and that root used to be
    `path.join(__dirname, '..')` — the install. Correct while the data lives
    there; afterwards it is the abandoned copy, and a launch would take its
    window from Electron's overlays.json and its hole from the backend's — two
    files, identical on the day of the move, drifting from the first edit.

    The rule: any `path.join(<root>, 'config' | 'emu' | 'assets', …)` must use
    a root whose nearest preceding assignment reads GAMECORE_DATA. The code
    roots (the venv, the monitor script) are joined onto `'.venv'` and
    `'backend'`, which this deliberately does not match.
    """
    offenders = []
    for js in sorted((REPO / "electron").glob("*.js")):
        last_assign: dict[str, str] = {}
        for n, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"\s*(?:const|let|var)\s+(\w+)\s*=\s*(.*)", line)
            if m:
                last_assign[m.group(1)] = m.group(2)
            for var in re.findall(r"path\.join\(\s*(\w+)\s*,\s*'(?:config|emu|assets)'", line):
                if "GAMECORE_DATA" not in last_assign.get(var, ""):
                    offenders.append(f"{js.name}:{n}: {line.strip()}  "
                                     f"(root `{var}` = {last_assign.get(var, '?')!r})")
    assert not offenders, (
        "these read player data under the install root; the root must come from "
        "GAMECORE_DATA:\n  " + "\n  ".join(offenders))
