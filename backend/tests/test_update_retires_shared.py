"""The one-off retirement of `config/themes/_shared/`, run for real.

`_shared/` held the settings screen and power menu both shipped themes
imported. They are host code now — `frontend/src/settings/` — and no theme
imports that path. The directory is inert on an installed box (`list_themes()`
skips any name starting with `_`), and it is removed because a stale copy of a
screen that moved is what somebody edits in three months wondering why nothing
changes.

**The function is extracted from `update/linux.sh` and executed**, never
retyped. A copy of the logic here would be right the day it was written and
wrong the day the script changes — and its being wrong would look exactly like
this file passing.

What must NOT happen is the reason this is a named retirement rather than a
generic prune: the delivery loop promises "on the box, not in the release →
never touched", which is what keeps an operator's own theme safe from an
update. A prune of everything missing from the release would delete exactly the
themes that promise protects.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UPDATER = REPO / "update" / "linux.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs bash to run the updater's own code"
)


def _extract(name: str) -> str:
    """The named shell function, lifted verbatim out of the updater."""
    src = UPDATER.read_text()
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", src, re.S | re.M)
    assert m, f"{name}() not found in update/linux.sh — did it move or get renamed?"
    return f"{name}() {{\n{m.group(1)}}}\n"


def _run(themes_dir: Path) -> str:
    script = _extract("_retire_shared_dir") + f'_retire_shared_dir "{themes_dir}"\n'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _shipped_shared(root: Path) -> Path:
    """A `_shared/` shaped like the one this project used to ship."""
    d = root / "_shared"
    (d / "settings").mkdir(parents=True)
    (d / "theme.json").write_text(json.dumps({"id": "_shared", "version": "1.4.0"}))
    (d / "settings" / "screen.js").write_text("export const createSettings = () => {}\n")
    (d / "settings" / "power.js").write_text("export const createPowerView = () => {}\n")
    return d


@pytest.mark.linux_until_port(8)
def test_the_orphan_is_moved_out_of_the_themes_directory(tmp_path):
    themes = tmp_path / "themes"
    themes.mkdir()
    _shipped_shared(themes)

    out = _run(themes)

    assert not (themes / "_shared").exists(), "the stale copy is still on the box"
    assert "retired" in out


@pytest.mark.linux_until_port(8)
def test_it_is_recoverable_rather_than_gone(tmp_path):
    """Moved to .prev/, the same recovery the replace path uses. An operator who
    put something of their own in there gets it back from a known place rather
    than from a backup they may not have."""
    themes = tmp_path / "themes"
    themes.mkdir()
    _shipped_shared(themes)

    _run(themes)

    kept = themes / ".prev" / "_shared" / "settings" / "screen.js"
    assert kept.is_file(), "the retirement deleted rather than set aside"


@pytest.mark.linux_until_port(8)
def test_running_it_twice_is_not_an_error(tmp_path):
    """Every box takes every release. This runs on all of them, including the
    ones that took the previous one."""
    themes = tmp_path / "themes"
    themes.mkdir()
    _shipped_shared(themes)

    _run(themes)
    out = _run(themes)          # would raise on a non-zero exit

    assert "retired" not in out, "it claimed to retire a directory that was gone"


@pytest.mark.linux_until_port(8)
def test_a_second_run_does_not_destroy_the_first_snapshot_it_cannot_replace(tmp_path):
    """The snapshot from the real retirement survives a later empty run."""
    themes = tmp_path / "themes"
    themes.mkdir()
    _shipped_shared(themes)
    _run(themes)
    _run(themes)

    assert (themes / ".prev" / "_shared" / "settings" / "screen.js").is_file()


@pytest.mark.linux_until_port(8)
def test_a_shared_directory_this_project_did_not_ship_is_left_alone(tmp_path):
    """The markers are checked, and both of them.

    `_` is a documented prefix for a directory the picker ignores — the theme
    README tells authors so. An operator using it for their own shared code has
    no reason to have our screen in it, and an update may not take it from them.
    """
    themes = tmp_path / "themes"
    themes.mkdir()
    mine = themes / "_shared"
    (mine / "settings").mkdir(parents=True)
    (mine / "settings" / "mine.js").write_text("// my own\n")

    out = _run(themes)

    assert (mine / "settings" / "mine.js").is_file(), "an operator's own code was taken"
    assert "left untouched" in out


@pytest.mark.linux_until_port(8)
def test_nothing_else_under_themes_is_touched(tmp_path):
    """The blast radius, stated as a test.

    This is a named retirement precisely so that it cannot become the generic
    prune the delivery loop refuses to be — a theme on the box and not in the
    release is the operator's, and stays.
    """
    themes = tmp_path / "themes"
    themes.mkdir()
    _shipped_shared(themes)
    for name in ("shelf", "summer", "someone-elses-theme", "_skeleton"):
        d = themes / name
        d.mkdir()
        (d / "theme.json").write_text(json.dumps({"id": name, "version": "1.0.0"}))
        (d / "index.js").write_text("export default () => {}\n")

    _run(themes)

    survivors = sorted(p.name for p in themes.iterdir() if not p.name.startswith("."))
    assert survivors == ["_skeleton", "shelf", "someone-elses-theme", "summer"]


def test_the_updater_actually_calls_it(tmp_path):
    """Extracting and running the function proves the function. This proves the
    script uses it — without which every test above is about dead code."""
    src = UPDATER.read_text()
    assert re.search(r"^\s*_retire_shared_dir \"\$_themes_dir\"", src, re.M), (
        "update/linux.sh defines the retirement but never calls it"
    )


def test_the_repository_no_longer_ships_the_directory_being_retired():
    """If it came back, the updater would install it and then retire it on every
    single run."""
    assert not (REPO / "config" / "themes" / "_shared").exists()
