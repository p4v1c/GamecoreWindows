"""scripts/import-linux-data.py: a Linux /userdata tree becomes the Windows data root, nothing lost.

Every tree is built under tmp_path; the script is loaded as a module for the
pure parts and run as a command once, as the owner will run it.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.platform import filesystem

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "import-linux-data.py"


def _load():
    spec = importlib.util.spec_from_file_location("import_linux_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module      # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


imp = _load()


def _linux_box(root: Path) -> Path:
    """A /userdata tree as the Linux box leaves it."""
    files = {
        "emu/melonds/Mario Kart DS (Europe).nds": "rom",
        "emu/melonds/Mario Kart DS (Europe).sav": "save",
        "emu/covers/melonds/Mario Kart DS (Europe).png": "png",
        "emu/gamemedia/melonds/x.json": "{}",
        "emu/profile-saves/abc123/melonds/game.sav": "profile save",
        "emu/lutris/fifa.lutris": "launcher",
        "config/theme.json": json.dumps({"active": "shelf"}),
        "config/profiles/profiles.json": json.dumps({"active": "abc123", "profiles": []}),
        "config/per-game/melonds/game.json": json.dumps({"rom": "/userdata/emu/melonds/g.nds",
                                                         "tool": "/home/gc/bin/x"}),
        "config/systems.json": "[]",
        "config/apps.json": "[]",
        "config/session.json": "{}",
        "config/addons.json": "{}",
        "config/controller-autoconfig.json": "{}",
        "config/catalog.d/melonds.json": "{}",
        "addons/rom-manager/venv/bin/python": "elf",
        "logs/backend/backend.log": "log",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    con = sqlite3.connect(root / "config" / "playtime.db")
    con.execute("CREATE TABLE playtime (game_key TEXT, system_id TEXT, total_secs INTEGER)")
    con.execute("INSERT INTO playtime VALUES ('Mario Kart DS (Europe).nds', 'melonds', 3600)")
    con.commit()
    con.close()
    return root


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8")


def test_without_apply_nothing_is_written(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    dest = tmp_path / "GameCoreData"
    result = _run(str(src), "--to", str(dest))
    assert result.returncode == 0, result.stderr
    assert "Plan only" in result.stdout
    assert not dest.exists()


def test_the_library_saves_media_and_playtime_keep_their_place(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    dest = tmp_path / "GameCoreData"
    result = _run(str(src), "--to", str(dest), "--apply")
    assert result.returncode == 0, result.stderr
    for rel in ("emu/melonds/Mario Kart DS (Europe).nds", "emu/melonds/Mario Kart DS (Europe).sav",
                "emu/covers/melonds/Mario Kart DS (Europe).png", "emu/gamemedia/melonds/x.json",
                "emu/profile-saves/abc123/melonds/game.sav", "config/theme.json",
                "config/profiles/profiles.json"):
        assert (dest / rel).read_bytes() == (src / rel).read_bytes(), rel
    con = sqlite3.connect(dest / "config" / "playtime.db")
    assert con.execute("SELECT total_secs FROM playtime").fetchone() == (3600,)
    con.close()
    assert "integrity check ok" in result.stdout
    assert list((dest / "logs" / "import").glob("linux-import-*.txt"))


def test_linux_only_state_is_kept_aside_and_not_made_live(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    dest = tmp_path / "GameCoreData"
    assert _run(str(src), "--to", str(dest), "--apply").returncode == 0
    for rel in ("config/systems.json", "config/apps.json", "config/session.json", "config/addons.json",
                "config/controller-autoconfig.json", "config/catalog.d/melonds.json",
                "addons/rom-manager/venv/bin/python", "logs/backend/backend.log", "emu/lutris/fifa.lutris"):
        assert not (dest / rel).exists(), rel
        assert (dest / "linux-import" / rel).is_file(), rel


def test_a_file_already_in_the_destination_is_kept_and_the_source_set_aside(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    dest = tmp_path / "GameCoreData"
    (dest / "config").mkdir(parents=True)
    (dest / "config" / "theme.json").write_text('{"active": "orbit"}', encoding="utf-8")
    result = _run(str(src), "--to", str(dest), "--apply")
    assert json.loads((dest / "config" / "theme.json").read_text(encoding="utf-8")) == {"active": "orbit"}
    assert json.loads((dest / "linux-import" / "config" / "theme.json").read_text(encoding="utf-8")) == {
        "active": "shelf"}
    assert "already in the destination" in result.stdout


def test_paths_under_the_linux_root_are_rewritten_and_others_reported(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    dest = tmp_path / "GameCoreData"
    result = _run(str(src), "--to", str(dest), "--apply")
    fixed = json.loads((dest / "config" / "per-game" / "melonds" / "game.json").read_text(encoding="utf-8"))
    assert fixed["rom"] == str(dest / "emu" / "melonds" / "g.nds")
    assert fixed["tool"] == "/home/gc/bin/x"
    assert "Linux path left as it is: /home/gc/bin/x" in result.stdout
    original = dest / "linux-import" / "config" / "per-game" / "melonds" / "game.json"
    assert json.loads(original.read_text(encoding="utf-8"))["rom"] == "/userdata/emu/melonds/g.nds"


@pytest.mark.parametrize("name, expected", [
    ("Zelda: Oracle?.gbc", "Zelda_ Oracle_.gbc"),
    ("trailing dot.", "trailing dot"),
    ("CON.nds", "CON_.nds"),
    ("aux", "aux_"),
    ("Pokemon X (Europe).3ds", "Pokemon X (Europe).3ds"),
])
def test_names_windows_refuses_are_made_legal(name, expected):
    assert imp.windows_name(name) == expected


def test_two_names_that_differ_by_case_both_survive(tmp_path, monkeypatch):
    # NTFS cannot hold both names in one folder, so the walk is fed what a
    # case-sensitive Linux tree would list.
    src = tmp_path / "userdata"
    (src / "emu" / "gb").mkdir(parents=True)
    (src / "emu" / "gb" / "tetris.gb").write_text("a", encoding="utf-8")
    real_scandir = imp.os.scandir

    class Twin:
        def __init__(self, entry, name):
            self._entry, self.name = entry, name

        def __getattr__(self, attr):
            return getattr(self._entry, attr)

    def scandir(path):
        entries = list(real_scandir(path))
        if path.endswith("gb"):
            entries.append(Twin(entries[0], "Tetris.gb"))
        return iter(entries)

    monkeypatch.setattr(imp.os, "scandir", scandir)
    plan = imp.make_plan(src, tmp_path / "dest")
    assert sorted(dst for _, dst in plan.copies) == ["emu/gb/Tetris.gb", "emu/gb/tetris (2).gb"]
    assert plan.renamed == [("emu/gb/tetris.gb", "emu/gb/tetris (2).gb")]


def test_a_link_is_reported_and_not_followed(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    disk = tmp_path / "usb"
    (disk / "big").mkdir(parents=True)
    (disk / "big" / "game.iso").write_text("iso", encoding="utf-8")
    (src / "volumes").mkdir()
    filesystem.link_dir(src / "volumes" / "USB", disk)
    plan = imp.make_plan(src, tmp_path / "dest")
    assert "volumes/USB" in plan.links
    assert not any("game.iso" in s for s, _ in plan.copies)


@pytest.mark.parametrize("layout", ["equal", "dest-inside", "source-inside"])
def test_nested_or_equal_roots_are_refused(tmp_path, layout):
    src = _linux_box(tmp_path / "userdata")
    dest = {"equal": src, "dest-inside": src / "win", "source-inside": tmp_path}[layout]
    result = _run(str(src), "--to", str(dest), "--apply")
    assert result.returncode == 1
    assert "must not contain each other" in result.stderr


def test_a_folder_that_is_not_a_data_root_is_refused(tmp_path):
    (tmp_path / "photos").mkdir()
    result = _run(str(tmp_path / "photos"), "--to", str(tmp_path / "dest"))
    assert result.returncode == 1
    assert "not a GameCore data root" in result.stderr


def test_a_second_run_overwrites_nothing(tmp_path):
    src = _linux_box(tmp_path / "userdata")
    dest = tmp_path / "GameCoreData"
    assert _run(str(src), "--to", str(dest), "--apply").returncode == 0
    (dest / "emu" / "melonds" / "Mario Kart DS (Europe).sav").write_text("played on Windows", encoding="utf-8")
    assert _run(str(src), "--to", str(dest), "--apply").returncode == 0
    assert (dest / "emu" / "melonds" / "Mario Kart DS (Europe).sav").read_text(encoding="utf-8") == (
        "played on Windows")


def test_nothing_in_the_tree_runs_the_import():
    """A human types it once; an installer step or the OTA running it would copy a library unasked."""
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True,
                             text=True, check=True).stdout.split("\0")
    hits = [rel for rel in filter(None, tracked)
            if not rel.endswith(".md") and not rel.startswith("docs/") and rel not in ("scripts/import-linux-data.py",
                                                      "backend/tests/test_import_linux_data.py")
            and "import-linux-data" in (REPO / rel).read_text(encoding="utf-8", errors="ignore")]
    assert not hits, hits
