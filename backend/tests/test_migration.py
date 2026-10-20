"""The migration, exercised only ever against trees this file built itself.

`scripts/migrate-userdata.py` moves a real person's ROM library, their
playtime and their credentials. The rules it is built on are the ones worth
testing, because each of them exists to make a specific disaster impossible:

  · dry run by default, so a mistyped command is a printout;
  · copy, never delete, so a migration that turns out wrong costs disk space;
  · never overwrite, so a second run cannot damage the first's result;
  · **nothing calls it** — the OTA must not be able to reach it, because a
    release that migrated data on its way in would leave a rollback facing a
    tree the old code cannot read.

That last one is asserted over every tracked file in the repository, and it is
the most important test here: the others check that the script behaves, this
one checks that it is never *invoked*.

Nothing here goes near a real installation. Every path is under `tmp_path`,
and the script refuses `--from`/`--to` pairs that are nested or equal anyway.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "migrate-userdata.py"


def _old_install(root: Path) -> dict[str, str]:
    """An install laid out the way v1.0.6 laid one out. Returns {rel: content}."""
    tree = {
        "config/systems.json": '[{"id": "duckstation"}]',
        "config/theme.json": '{"active": "shelf"}',
        "config/auth_secret": "32 bytes of secret, in spirit",
        "config/themes/shelf/theme.json": '{"name": "shelf"}',
        "emu/duckstation/Crash Bandicoot.bin": "a ROM",
        "emu/duckstation/Crash Bandicoot.cue": 'FILE "Crash Bandicoot.bin" BINARY\n',
        "emu/covers/duckstation/Crash Bandicoot.png": "cover art",
        "emu/gamemedia/duckstation/crash/game.json": '{"id": 1}',
        "assets/logos/duckstation.png": "a logo",
    }
    for rel, content in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tree


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=180)


@pytest.fixture
def box(tmp_path):
    old = tmp_path / "GameCore"
    old.mkdir()
    return old, _old_install(old), tmp_path / "userdata"


def test_without_the_flag_it_writes_nothing_at_all(box):
    """The default has to be the harmless one. Somebody will run this to see
    what it does, and what it does must be: print."""
    old, _, new = box
    r = _run("--from", str(old), "--to", str(new))
    assert r.returncode == 0, r.stderr
    assert not new.exists(), "the dry run created the destination"
    assert "DRY RUN" in r.stdout


def test_the_plan_names_every_section_with_its_size_and_count(box):
    """A human is meant to read this before agreeing to it, so it has to say
    what would move, how much of it, and where from."""
    old, _, new = box
    out = _run("--from", str(old), "--to", str(new)).stdout
    for section in ("config", "roms", "logos"):
        assert section in out
    assert str(old / "emu") in out, "the plan must name the source directories"
    assert "TOTAL" in out
    # 9 files were planted; the plan must account for all of them.
    assert "9" in out


def test_it_copies_everything_and_deletes_nothing(box):
    old, tree, new = box
    r = _run("--from", str(old), "--to", str(new), "--i-know-what-i-am-doing")
    assert r.returncode == 0, r.stderr
    for rel, content in tree.items():
        assert (old / rel).read_text() == content, f"{rel} was removed or altered"
        assert (new / rel).read_text() == content, f"{rel} did not arrive"


def test_a_second_run_leaves_the_first_ones_files_alone(box):
    """Not merely idempotent — non-destructive. If the box has been running on
    the new tree, its files are NEWER than the originals, and a second run that
    overwrote them would silently roll the player back."""
    old, _, new = box
    _run("--from", str(old), "--to", str(new), "--i-know-what-i-am-doing")
    moved_on = new / "config" / "theme.json"
    moved_on.write_text('{"active": "summer"}')

    r = _run("--from", str(old), "--to", str(new), "--i-know-what-i-am-doing")
    assert r.returncode == 0, r.stderr
    assert moved_on.read_text() == '{"active": "summer"}'
    assert "Copied 0 file(s)" in r.stdout


@pytest.mark.parametrize("dest", ["same", "nested"])
def test_a_destination_that_would_eat_its_own_source_is_refused(box, dest):
    old, _, _ = box
    target = old if dest == "same" else old / "userdata"
    r = _run("--from", str(old), "--to", str(target), "--i-know-what-i-am-doing")
    assert r.returncode == 2
    assert "ERROR" in r.stderr


def test_it_stops_short_of_switching_the_box_over(box):
    """Copying the bytes is not the migration. The box reads the new tree only
    once GAMECORE_DATA is set, and that stays a separate human step — two
    reversible actions with a person between them."""
    old, _, new = box
    out = _run("--from", str(old), "--to", str(new),
               "--i-know-what-i-am-doing").stdout
    assert "GAMECORE_DATA" in out and "does NOT use the new tree yet" in out


# ── What the plan has to say out loud ────────────────────────────────────────

@pytest.mark.linux_until_port(10)
def test_a_symlink_is_named_and_not_copied(box):
    """`rglob` neither copies a link nor descends into a linked directory. A
    ROM directory that is really a link to another disk would vanish from the
    new tree — so the plan says so, with the target, instead of staying quiet."""
    old, _, new = box
    (old / "emu" / "duckstation" / "elsewhere.chd").symlink_to("/mnt/usb/x.chd")
    (old / "emu" / "on-the-nas").symlink_to("/mnt/nas/roms")

    out = _run("--from", str(old), "--to", str(new)).stdout
    assert "2 symlink(s) will NOT be copied" in out
    assert "elsewhere.chd  ->  /mnt/usb/x.chd" in out
    assert "on-the-nas  ->  /mnt/nas/roms" in out

    _run("--from", str(old), "--to", str(new), "--i-know-what-i-am-doing")
    assert not (new / "emu" / "duckstation" / "elsewhere.chd").exists()
    assert not (new / "emu" / "on-the-nas").exists()
    assert (new / "emu" / "duckstation" / "Crash Bandicoot.bin").is_file()


def test_a_live_database_is_called_out(box):
    """SQLite writes in place; a copy taken mid-transaction opens fine and has
    lost the last thing written. Small is not atomic."""
    old, _, new = box
    (old / "config" / "playtime.db").write_bytes(b"SQLite format 3\x00")
    out = _run("--from", str(old), "--to", str(new)).stdout
    assert "1 database file(s)" in out and "playtime.db" in out
    assert "stop gamecore-ui gamecore-backend" in out


@pytest.mark.linux_until_port(10)
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write anywhere")
def test_a_destination_this_user_cannot_create_is_planned_but_not_written(box, tmp_path):
    """`/userdata` sits under `/`, which is root's.

    The dry run still shows the plan — looking before deciding is what it is
    for, and nobody should have to create the directory just to read it — and
    says what to create. The write refuses up front with the same commands,
    rather than a PermissionError traceback out of the first mkdir after the
    plan was read and approved.
    """
    old, _, _ = box
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        dry = _run("--from", str(old), "--to", str(locked / "userdata"))
        wet = _run("--from", str(old), "--to", str(locked / "userdata"),
                   "--i-know-what-i-am-doing")
    finally:
        locked.chmod(0o755)

    assert dry.returncode == 0, dry.stderr
    assert "section" in dry.stdout and "DRY RUN" in dry.stdout      # the plan
    assert "not writable" in dry.stdout
    assert "btrfs subvolume create" in dry.stdout and "install -d" in dry.stdout

    assert wet.returncode == 2
    assert "not writable" in wet.stderr and "btrfs subvolume create" in wet.stderr
    assert "section" not in wet.stdout                              # nothing planned
    assert not (locked / "userdata").exists()


def test_the_switch_over_names_every_consumer_of_the_data_root(box):
    """Miss one and the box is split: the interface reads the new tree, ROM
    uploads land in the old one, and nothing on screen says which. Each of
    these was found by reading the code that resolves the root, not by
    guessing; the list is what a person has to type, so it is asserted on."""
    old, _, new = box
    out = _run("--from", str(old), "--to", str(new),
               "--i-know-what-i-am-doing").stdout
    # both units, as drop-ins that leave override.conf alone
    assert "gamecore-backend.service.d/userdata.conf" in out
    assert "gamecore-ui.service.d/userdata.conf" in out
    # the root-owned CLI copy, refreshed by hand
    assert "install -m 755" in out and "/usr/local/bin/gamecore-addon" in out
    # the addons' own units, rewritten from what the CLI now knows
    assert "gamecore-addon update" in out
    # every Flatpak emulator's sandbox — the one found the hard way: a launch
    # hands RPCS3 a path under the new root and RPCS3 says the game is missing
    assert "flatpak override --user --filesystem=" in out
    assert "catalog-query.py flatpaks" in out
    # and the emulators' own settings that still name the old tree
    assert 'grep -rl' in out and "/emu" in out.split("Only then")[1]
    # a check before anything is played, and the way back
    assert "grep GAMECORE_DATA" in out
    assert "The way back" in out and "userdata.conf" in out.split("The way back")[1]


# ── The one that matters most ────────────────────────────────────────────────
# Files allowed to mention the script: itself, and this test.
_MAY_MENTION = {
    "scripts/migrate-userdata.py",
    "backend/tests/test_migration.py",
}


def _executable_text(path: Path) -> str:
    """The file with its prose removed.

    Pointing at the migration in a comment is how the codebase explains
    itself — `services/paths.py` names it precisely to tell the reader where
    the bytes eventually move. What must not exist is a line that *runs* it,
    so docstrings and comments are stripped before the search rather than the
    whole file being exempted.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return text
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if not body or not isinstance(body[0], ast.Expr):
                continue
            first = body[0].value
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                for i in range(first.lineno - 1, (first.end_lineno or first.lineno)):
                    lines[i] = ""
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


def test_nothing_the_ota_runs_can_reach_the_migration():
    """A release that migrated data on its way in would be unrollbackable.

    `update/linux.sh` restores the previous *code* from `${GAMECORE_PATH}.prev`
    — it has no idea data exists elsewhere. So if an update moved the data, the
    rollback would hand the old code a tree it cannot read, and there is no
    procedure anywhere to recombine them.

    The temptation this guards against is a real one and it sounds reasonable:
    "the box is already down for the update, may as well migrate while we're
    here". Executable references are therefore banned outright, and the ban is
    checked rather than remembered.
    """
    hits = []
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                             capture_output=True, text=True,
                             check=True).stdout.split("\0")
    for rel in filter(None, tracked):
        if rel in _MAY_MENTION or rel.endswith(".md"):
            continue
        if "migrate-userdata" in _executable_text(REPO / rel):
            hits.append(rel)
    assert not hits, (
        "the migration must stay a command a human types — it is referenced "
        f"from: {hits}")
