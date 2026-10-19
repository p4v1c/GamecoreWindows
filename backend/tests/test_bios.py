"""The BIOS check: absent, present-but-wrong, conforming — and nothing else.

Every file these tests look at is made in `tmp_path`. The real BIOS files on a
box are the owner's own dumps: the suite never reads them, never names a real
path, and nothing in the module under test can write anywhere at all.

The three verdicts are the point. They were one verdict — "the emulator did not
start" — and that is the ticket this whole screen exists to close.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import bios  # noqa: E402
from backend.services.catalog import load_catalog  # noqa: E402
from backend.services.catalog.loader import Pack  # noqa: E402

CATALOG = ROOT / "catalog"
LOCAL = ROOT / "config" / "catalog.d"

GOOD = b"a boot rom, as far as anything here is concerned"
GOOD_MD5 = hashlib.md5(GOOD).hexdigest()


def _pack(bios_block: dict, pid: str = "testpack") -> Pack:
    """A pack that exists only for this test. No catalogue id is named."""
    return Pack(
        id=pid,
        data={"id": pid, "kind": "emulator", "label": "Test System",
              "platform": "TEST", "color": "#123456",
              "launch": {"path": "true"}, "roms": {"dir": f"emu/{pid}"},
              "bios": bios_block},
        path=Path("/nonexistent"), origin="shipped")


def _one_required(md5: str | None = GOOD_MD5, directory: str = "@HOME@/sys") -> Pack:
    spec = {"file": "boot.bin", "required": True, "note": "the boot rom"}
    if md5:
        spec["md5"] = md5
    return _pack({"dir": directory, "files": [spec]})


def _row(pack: Pack, home: Path) -> dict:
    return bios.check_pack(pack, home)


# ── the three cases ────────────────────────────────────────────────────────

def test_a_missing_file_is_absent_and_says_where_it_goes(tmp_path):
    """Absent is the common case, and the only one with a cure the owner owns.

    The path matters as much as the verdict: "copy a BIOS" is the answer that
    produced the support thread, "copy it to THIS directory" is the one that
    ends it.
    """
    (tmp_path / "sys").mkdir()
    row = _row(_one_required(), tmp_path)

    assert row["status"] == bios.ABSENT
    entry = row["files"][0]
    assert entry["status"] == bios.ABSENT
    assert entry["path"] == str(tmp_path / "sys" / "boot.bin")
    assert entry["verified"] is False


def test_a_file_whose_md5_is_wrong_is_not_reported_as_missing(tmp_path):
    """Two different phone calls. "Copy this file" is the wrong answer here."""
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(b"truncated download")
    row = _row(_one_required(), tmp_path)

    assert row["status"] == bios.MISMATCH
    entry = row["files"][0]
    assert entry["status"] == bios.MISMATCH
    assert entry["expected_md5"] == GOOD_MD5
    # The hash the owner actually has, so a support answer can be given
    # without asking them to run md5sum over SSH.
    assert entry["actual_md5"] == hashlib.md5(b"truncated download").hexdigest()


def test_a_conforming_file_is_ok_and_says_it_was_verified(tmp_path):
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(GOOD)
    row = _row(_one_required(), tmp_path)

    assert row["status"] == bios.OK
    assert row["files"][0]["status"] == bios.OK
    assert row["files"][0]["verified"] is True


def test_a_file_with_no_declared_md5_is_ok_but_not_verified(tmp_path):
    """RPCS3 firmware and Switch keys change with every version.

    Pinning a hash on those would report every legitimate update as corrupt, so
    the pack declares none — and the report must not then claim it checked
    something. `verified` is what stops the screen saying "conforming" about a
    file nothing compared.
    """
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(b"whatever this version ships")
    row = _row(_one_required(md5=None), tmp_path)

    assert row["status"] == bios.OK
    assert row["files"][0]["verified"] is False
    assert row["files"][0]["expected_md5"] == ""


# ── the trap: red on a working box ─────────────────────────────────────────

@pytest.mark.parametrize("content", [None, b"a regional variant"])
def test_an_optional_file_never_turns_the_system_red(tmp_path, content):
    """Regional firmwares and per-title keys are absent on working boxes.

    Painting those red is how a screen built to remove tickets starts
    generating them. Neither absence nor a hash the pack does not recognise may
    escalate past the file's own line.
    """
    (tmp_path / "sys").mkdir()
    if content is not None:
        (tmp_path / "sys" / "extra.bin").write_bytes(content)
    pack = _pack({"dir": "@HOME@/sys", "files": [
        {"file": "extra.bin", "md5": GOOD_MD5, "required": False,
         "note": "only some dumps need it"}]})

    row = _row(pack, tmp_path)
    assert row["status"] == bios.OK
    assert row["files"][0]["status"] != bios.OK      # the line still reports it


def test_the_worst_required_file_decides_the_system(tmp_path):
    """A system is as broken as its worst required file, not its last one."""
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "first.bin").write_bytes(GOOD)
    pack = _pack({"dir": "@HOME@/sys", "files": [
        {"file": "first.bin", "md5": GOOD_MD5, "required": True, "note": "a"},
        {"file": "second.bin", "required": True, "note": "b"}]})

    assert _row(pack, tmp_path)["status"] == bios.ABSENT


# ── the directory-scanning case ────────────────────────────────────────────

def test_an_empty_scan_directory_is_absent_and_one_file_is_enough(tmp_path):
    scan = tmp_path / "sys"
    scan.mkdir()
    pack = _pack({"dir": "@HOME@/sys", "anyFile": {
        "required": True, "note": "any image the emulator recognises"}})

    assert _row(pack, tmp_path)["status"] == bios.ABSENT
    (scan / "some-dump.bin").write_bytes(GOOD)
    assert _row(pack, tmp_path)["status"] == bios.OK


def test_a_dump_filed_in_a_subfolder_does_not_count(tmp_path):
    """`SearchDirectory` reads one directory.

    Counting a subfolder would report a box as ready when the emulator will
    find nothing — the black screen back, with a green tick in front of it.
    """
    (tmp_path / "sys" / "NTSC-J").mkdir(parents=True)
    (tmp_path / "sys" / "NTSC-J" / "some-dump.bin").write_bytes(GOOD)
    pack = _pack({"dir": "@HOME@/sys", "anyFile": {
        "required": True, "note": "any image the emulator recognises"}})

    assert _row(pack, tmp_path)["status"] == bios.ABSENT


def test_a_missing_directory_is_absent_rather_than_an_exception(tmp_path):
    """Before the first launch the emulator has created nothing at all."""
    pack = _pack({"dir": "@HOME@/never-created", "anyFile": {
        "required": True, "note": "any image"}})
    assert _row(pack, tmp_path)["status"] == bios.ABSENT


# ── what the launch gate is allowed to refuse ──────────────────────────────

def test_the_launch_gate_names_the_file_and_the_directory(tmp_path):
    (tmp_path / "sys").mkdir()
    packs = {"testpack": _one_required()}

    missing = bios.missing_required("testpack", tmp_path, packs=packs)
    assert [m["file"] for m in missing] == ["boot.bin"]
    assert missing[0]["path"] == str(tmp_path / "sys" / "boot.bin")


def test_a_wrong_md5_does_not_refuse_the_launch(tmp_path):
    """The owner may be running a dump this catalogue does not record.

    Their emulator works. Refusing to start it on a hash would be GameCore
    inventing a fault, which is worse than the black screen it replaced: the
    black screen at least let the game be tried.
    """
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(b"a dump nobody wrote down")
    packs = {"testpack": _one_required()}

    assert bios.missing_required("testpack", tmp_path, packs=packs) == []


def test_an_optional_file_does_not_refuse_the_launch(tmp_path):
    (tmp_path / "sys").mkdir()
    packs = {"testpack": _pack({"dir": "@HOME@/sys", "files": [
        {"file": "extra.bin", "required": False, "note": "sometimes"}]})}

    assert bios.missing_required("testpack", tmp_path, packs=packs) == []


def test_a_system_with_no_bios_block_never_blocks_a_launch(tmp_path):
    pack = _pack({"dir": "@HOME@/sys", "files": [
        {"file": "boot.bin", "required": True, "note": "x"}]})
    del pack.data["bios"]
    assert bios.missing_required("testpack", tmp_path, packs={"testpack": pack}) == []
    assert bios.check_pack(pack, tmp_path) is None


def test_an_unknown_system_never_blocks_a_launch(tmp_path):
    assert bios.missing_required("nothing-like-this", tmp_path, packs={}) == []


# ── the two ways this could be quietly wrong ───────────────────────────────

def test_replacing_a_bad_dump_changes_the_verdict(tmp_path):
    """The md5 cache keys on mtime and size, not on the path.

    Keyed on the path alone, an owner who follows the screen's advice and
    copies the right file over the wrong one keeps reading "wrong md5" until
    the backend is restarted — and would reasonably conclude the good dump is
    bad too.
    """
    (tmp_path / "sys").mkdir()
    target = tmp_path / "sys" / "boot.bin"
    target.write_bytes(b"the wrong one")
    assert _row(_one_required(), tmp_path)["status"] == bios.MISMATCH

    import os
    target.write_bytes(GOOD)
    # A copy landing in the same second is exactly the case a coarse cache key
    # gets wrong, so the test does not rely on the clock having moved.
    os.utime(target, ns=(1, 2))
    assert _row(_one_required(), tmp_path)["status"] == bios.OK


def test_checking_writes_nothing(tmp_path):
    """The one rule this module cannot break.

    These are the owner's dumps. A report is stats and reads; if a future
    change ever touches a byte or a timestamp, it fails here.
    """
    (tmp_path / "sys").mkdir()
    good = tmp_path / "sys" / "boot.bin"
    good.write_bytes(GOOD)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob("*") if p.is_file()}

    bios.report(tmp_path, packs={"testpack": _one_required()})

    after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_hashing_can_be_skipped_entirely(tmp_path, monkeypatch):
    """A launch asks "is it there", not "is it the right one".

    `_free_stale_slots` already documents that a launch must not wait on I/O it
    does not need. Reading a 4 MB boot ROM to answer a question about a
    directory entry is the same mistake.
    """
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(GOOD)
    monkeypatch.setattr(bios, "_md5", lambda p: pytest.fail(f"hashed {p}"))

    assert bios.missing_required("testpack", tmp_path,
                                 packs={"testpack": _one_required()}) == []


def test_the_summary_sees_a_corrupt_file(tmp_path):
    """The diagnostic hashes where the launch gate does not.

    A support report that says "everything is fine" about a box whose boot ROM
    is a truncated download is the answer that sends the ticket round again.
    Affordable because the md5 cache makes the second read a dict lookup.
    """
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(b"truncated download")
    packs = {"testpack": _one_required()}

    assert bios.summary(tmp_path, packs=packs) == {
        "ok": False, "systems": {"testpack": bios.MISMATCH}}


def test_the_summary_says_null_rather_than_fine_when_it_cannot_run(monkeypatch):
    """`ok: false` and "the check broke" are not the same report."""
    def boom(*_a, **_k):
        raise OSError("no catalogue")

    monkeypatch.setattr(bios, "report", boom)
    assert bios.summary()["ok"] is None


# ── the sentence the player reads instead of a black screen ────────────────

def test_the_refusal_names_the_file_and_the_destination(tmp_path):
    (tmp_path / "sys").mkdir()
    message = bios.launch_blocker("testpack", tmp_path,
                                  packs={"testpack": _one_required()})

    assert "boot.bin" in message
    assert str(tmp_path / "sys" / "boot.bin") in message
    # The pack's own note, not a generic "copy it there": RPCS3's firmware is
    # installed by RPCS3 from a PUP, and telling someone to drop a module into
    # dev_flash is advice that cannot work.
    assert "the boot rom" in message


def test_the_refusal_for_a_scanned_directory_names_the_directory(tmp_path):
    """No filename can be named here, so the directory has to be."""
    (tmp_path / "sys").mkdir()
    pack = _pack({"dir": "@HOME@/sys", "anyFile": {
        "required": True, "note": "any image the emulator recognises"}})

    message = bios.launch_blocker("testpack", tmp_path, packs={"testpack": pack})
    assert str(tmp_path / "sys") in message


@pytest.mark.parametrize("content", [GOOD, b"a dump nobody wrote down"])
def test_a_present_file_never_refuses_a_launch(tmp_path, content):
    """Right hash or wrong, the file is there and the emulator will read it."""
    (tmp_path / "sys").mkdir()
    (tmp_path / "sys" / "boot.bin").write_bytes(content)
    assert bios.launch_blocker("testpack", tmp_path,
                               packs={"testpack": _one_required()}) == ""


# ── the gate, through the endpoint a player actually hits ──────────────────

_GHOST = {"id": "testpack", "label": "Test System", "kind": "emulator",
          "path": "/usr/bin/definitely-not-installed", "args": "", "romsPath": ""}


@pytest.fixture
def launcher(monkeypatch):
    """A client whose emulator cannot start, so nothing is ever launched.

    The BIOS gate runs before the exec step, so a launcher that always fails at
    exec still exercises it in full — and 503 from that step is how a test says
    "the gate let this through".
    """
    from fastapi.testclient import TestClient

    from backend import main
    from backend.services import systems as systems_service

    monkeypatch.setattr(systems_service, "list_all", lambda: [_GHOST])
    with TestClient(main.app) as client:
        yield client


def _with_pack(monkeypatch, pack):
    monkeypatch.setattr(bios, "load_catalog", lambda *a, **k: {"testpack": pack})


def test_launching_without_a_required_bios_names_the_file(launcher, monkeypatch,
                                                          tmp_path):
    """The whole point of the gate.

    Started anyway, the emulator sits on a black screen: a game that launched
    and did nothing, indistinguishable from a broken dump, a broken pad or a
    broken box. Refused here it costs the same second and says which file.
    """
    _with_pack(monkeypatch, _one_required(directory=str(tmp_path)))
    r = launcher.post("/api/games/launch", json={"system_id": "testpack"})

    assert r.status_code == 424, r.text
    assert "boot.bin" in r.json()["detail"]
    assert str(tmp_path / "boot.bin") in r.json()["detail"]


@pytest.mark.linux_until_port(3)
def test_a_box_that_has_the_file_still_launches(launcher, monkeypatch, tmp_path):
    """The gate must not become a second way for a working box to fail.

    503 is the exec step refusing a binary that is not there, which is exactly
    where this launch went before the gate existed.
    """
    (tmp_path / "boot.bin").write_bytes(GOOD)
    _with_pack(monkeypatch, _one_required(directory=str(tmp_path)))

    r = launcher.post("/api/games/launch", json={"system_id": "testpack"})
    assert r.status_code == 503, r.text


@pytest.mark.linux_until_port(3)
def test_a_check_that_explodes_does_not_stop_the_launch(launcher, monkeypatch):
    """Same guarantee `_free_stale_slots` documents, for the same reason.

    A BIOS check that cannot run must cost a game that starts, never a game
    that does not. Reaching 503 means the gate got out of the way.
    """
    def boom(*_a, **_k):
        raise OSError("the flatpak config directory went away")

    monkeypatch.setattr(bios, "load_catalog", boom)
    r = launcher.post("/api/games/launch", json={"system_id": "testpack"})
    assert r.status_code == 503, r.text


# ── the shipped catalogue ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def packs():
    return load_catalog(CATALOG, LOCAL)


@pytest.fixture(scope="module")
def declaring(packs):
    """The packs that declare a `bios` block — never a list of ids."""
    return [p for p in packs.values() if p.data.get("bios")]


def test_the_catalogue_declares_bios_for_something(declaring):
    """A guard on the tests below, which all pass vacuously on an empty list."""
    assert declaring


def test_every_declared_directory_stays_inside_the_home(declaring, tmp_path):
    """A pack path expression that escaped would aim the checker at the box.

    The report is read-only, so the damage would be a red line pointing at
    someone's documents rather than a lost file — but a BIOS screen naming a
    path outside the emulator's own tree is a bug report either way.
    """
    outside = []
    for pack in declaring:
        directory = pack.expand(pack.data["bios"]["dir"], tmp_path)
        if not directory.is_absolute() or tmp_path not in directory.parents:
            outside.append(f"{pack.id}: {directory}")
    assert outside == [], "\n".join(outside)


def test_a_flatpak_bios_directory_carries_the_installed_app_id(declaring, tmp_path):
    """The gopher64 rule, applied to BIOS paths.

    A directory under an app id nobody installs is a `mkdir -p` away from a
    green tick on a phantom tree — which is exactly how the N64 seed was
    deployed for months to an emulator that was never installed.
    """
    wrong = []
    for pack in declaring:
        directory = str(pack.expand(pack.data["bios"]["dir"], tmp_path))
        if ".var/app/" in directory and f".var/app/{pack.app_id}/" not in directory:
            wrong.append(f"{pack.id}: {directory} vs installed {pack.app_id}")
    assert wrong == [], "\n".join(wrong)


def test_a_required_file_that_carries_no_md5_says_why_in_its_note(declaring):
    """Not decoration. `verified: false` reaches the screen as "not checked",
    and the owner is entitled to know that was a decision rather than a gap."""
    for pack in declaring:
        for spec in pack.data["bios"].get("files", []):
            assert spec["note"].strip(), f"{pack.id}: {spec['file']} has an empty note"


def test_the_endpoint_answers_on_a_box_with_no_bios_at_all(declaring):
    """A fresh box has none of these files, and the page must still draw.

    conftest.py aims HOME at a throwaway tree, so this runs against exactly
    that: nothing installed, nothing copied. The failure it guards is a 500 on
    the one screen someone opens BECAUSE their box is not working yet.
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        r = client.get("/api/bios")
    assert r.status_code == 200
    rows = r.json()
    assert {row["id"] for row in rows} == {p.id for p in declaring}
    for row in rows:
        assert row["status"] in (bios.OK, bios.ABSENT, bios.MISMATCH)
        # Not "is it broken" but "what does this box still need": a system
        # whose tile is not on the grid is listed and dimmed, never hidden.
        assert row["installed"] is False
        assert row["files"], row["id"]


# The legal line of this project, kept by a test rather than by memory.
_LINK = re.compile(r"https?://|ftp://|www\.", re.I)

_BIOS_SURFACES = (
    "backend/services/bios.py",
    "backend/routers/bios.py",
    "frontend/src/components/modals/settings/BiosPage.tsx",
)


def test_nothing_about_bios_carries_a_download_link(declaring):
    """No link to a BIOS, a firmware or a key — not in the UI, not in the
    catalogue, not in a comment. This is the one rule that does not move, and
    it is far too easy to break with a well-meaning "helpful" note."""
    offenders = []
    for pack in declaring:
        block = pack.data["bios"]
        for spec in list(block.get("files", [])) + [block.get("anyFile") or {}]:
            if _LINK.search(spec.get("note", "")):
                offenders.append(f"{pack.id}: {spec.get('file', block['dir'])}")
    for rel in _BIOS_SURFACES:
        path = ROOT / rel
        if path.is_file() and _LINK.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == [], "\n".join(offenders)
