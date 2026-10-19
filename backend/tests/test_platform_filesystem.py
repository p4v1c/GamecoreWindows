"""Directory links on NTFS: junctions, made and read for real.

The suite itself depends on these: conftest.py links the repository's
`catalog/` into the throwaway root, and test_covers.py wipes that root with
`shutil.rmtree` — a link that rmtree followed would delete the checkout's
catalogue. Every test here works in `tmp_path`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.services.platform import filesystem


@pytest.fixture
def target(tmp_path: Path) -> Path:
    folder = tmp_path / "target"
    folder.mkdir()
    (folder / "save.dat").write_text("owner")
    return folder


def test_a_link_shows_the_folder_it_points_at(tmp_path, target):
    link = tmp_path / "link"
    filesystem.link_dir(link, target)
    assert filesystem.is_link(link)
    assert (link / "save.dat").read_text() == "owner"
    assert filesystem.link_target(link) == target


def test_writes_through_the_link_land_in_the_target(tmp_path, target):
    link = tmp_path / "link"
    filesystem.link_dir(link, target)
    (link / "new.dat").write_text("profile")
    assert (target / "new.dat").read_text() == "profile"


def test_removing_the_link_keeps_what_it_pointed_at(tmp_path, target):
    link = tmp_path / "link"
    filesystem.link_dir(link, target)
    link.unlink()
    assert not link.exists() and not filesystem.is_link(link)
    assert (target / "save.dat").read_text() == "owner"


def test_rmtree_of_a_tree_holding_a_link_spares_the_target(tmp_path, target):
    """What conftest.py and test_covers.py rely on to keep the checkout whole."""
    tree = tmp_path / "root"
    tree.mkdir()
    filesystem.link_dir(tree / "catalog", target)
    shutil.rmtree(tree)
    assert not tree.exists()
    assert (target / "save.dat").read_text() == "owner"


def test_a_link_whose_target_went_away_is_still_a_link(tmp_path, target):
    """Or the profile code would take it for "nothing there" and leave it."""
    link = tmp_path / "link"
    filesystem.link_dir(link, target)
    shutil.rmtree(target)
    assert not link.exists()
    assert filesystem.is_link(link)


def test_a_plain_folder_or_file_is_not_a_link(tmp_path, target):
    assert not filesystem.is_link(target)
    assert not filesystem.is_link(target / "save.dat")
    assert not filesystem.is_link(tmp_path / "absent")


def test_a_taken_name_is_refused(tmp_path, target):
    taken = tmp_path / "taken"
    taken.mkdir()
    with pytest.raises(FileExistsError):
        filesystem.link_dir(taken, target)


def test_a_missing_target_is_refused_rather_than_left_dangling(tmp_path):
    with pytest.raises(FileNotFoundError):
        filesystem.link_dir(tmp_path / "link", tmp_path / "absent")
    assert not filesystem.is_link(tmp_path / "link")


def test_a_relative_target_is_made_absolute(tmp_path, target, monkeypatch):
    """A junction cannot hold a relative target; the caller's cwd decides it."""
    monkeypatch.chdir(tmp_path)
    filesystem.link_dir(tmp_path / "link", Path("target"))
    assert filesystem.link_target(tmp_path / "link") == target


def test_the_nt_prefix_is_not_part_of_the_target(monkeypatch):
    monkeypatch.setattr(filesystem.os, "readlink", lambda p: "\\\\?\\D:\\Saves\\Sam")
    assert filesystem.link_target(Path("x")) == Path("D:/Saves/Sam")
    monkeypatch.setattr(filesystem.os, "readlink", lambda p: "D:\\Saves\\Sam")
    assert filesystem.link_target(Path("x")) == Path("D:/Saves/Sam")
