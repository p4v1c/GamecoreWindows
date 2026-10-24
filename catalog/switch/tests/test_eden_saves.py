"""Eden's saves into Ryujinx: containers Ryujinx can mount, never an overwrite.

Layouts measured on the reference box (Eden 0.2.1, Ryujinx 1.3.3); the index
fixture is the imkvdb.arc Ryujinx itself wrote after one game created a save.
"""
import hashlib
import importlib.util
import json
import os
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PACK = ROOT / "catalog/switch"
sys.path.insert(0, str(PACK))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"test_switch_{name}", PACK / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


es = _load("eden_saves")
ei = _load("eden_import")

USER = struct.pack("<QQ", 1, 0)              # RyuPlayer, Eden dir ...0001
EDEN_USER_DIR = "00000000000000000000000000000001"
DEVICE_DIR = "0" * 32
FIFA, MK8, ACNH = 0x0100216014472000, 0x0100152000022000, 0x01006F8002326000


def _eden(tmp_path, users=(USER,)) -> Path:
    eden = tmp_path / "eden"
    dat = eden / es.EDEN_PROFILES
    dat.parent.mkdir(parents=True)
    raw = bytearray(es.PROFILE_HEADER + es.PROFILE_SLOTS * es.PROFILE_RECORD)
    for slot, user in enumerate(users):
        at = es.PROFILE_HEADER + slot * es.PROFILE_RECORD
        raw[at:at + 16] = user
    dat.write_bytes(bytes(raw))
    saves = eden / es.EDEN_SAVES / "0000000000000000"
    for user_dir, title, files in (
            (EDEN_USER_DIR, FIFA, {"SaveData": b"fifa", "sub/Settings": b"s"}),
            (EDEN_USER_DIR, MK8, {"userdata.dat": b"mk8-account"}),
            (DEVICE_DIR, MK8, {"ghosts.dat": b"mk8-device"}),
            (DEVICE_DIR, ACNH, {"main.dat": b"island", es.EDEN_SIZE_FILE: bytes(16)}),
            # Eden made this one with the user's bytes swapped: only a size file.
            ("00000000000000000100000000000000", FIFA, {es.EDEN_SIZE_FILE: bytes(16)})):
        for rel, data in files.items():
            f = saves / user_dir / f"{title:016X}" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
    return eden


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(autouse=True)
def nothing_runs(monkeypatch):
    monkeypatch.setattr(es, "running", lambda *a: [])


def _import(ryujinx, eden, deadline=None):
    return es.import_saves(ryujinx, eden / es.EDEN_SAVES, eden, deadline)


def _by_title(ryujinx):
    """(title, type) -> container folder, from the index Ryujinx would read."""
    out = {}
    for key, value in es.read_index(ryujinx / es.INDEX_DIR):
        title, kind = struct.unpack_from("<Q", key)[0], key[0x20]
        out[(title, kind)] = ryujinx / es.USER_SAVES / f"{struct.unpack_from('<Q', value)[0]:016x}"
    return out


def test_every_eden_save_gets_a_container_ryujinx_can_mount(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    notes = _import(ryujinx, eden)

    saves = _by_title(ryujinx)
    assert set(saves) == {(FIFA, 1), (MK8, 1), (MK8, 3), (ACNH, 3)}
    assert (saves[(FIFA, 1)] / "0/sub/Settings").read_bytes() == b"s"
    assert (saves[(MK8, 3)] / "0/ghosts.dat").read_bytes() == b"mk8-device"
    assert not (saves[(ACNH, 3)] / "0" / es.EDEN_SIZE_FILE).exists()
    extra = (saves[(FIFA, 1)] / "ExtraData0").read_bytes()
    assert len(extra) == 0x200 and extra == (saves[(FIFA, 1)] / "ExtraData1").read_bytes()
    assert struct.unpack_from("<Q", extra)[0] == FIFA
    assert extra[0x08:0x18] == USER and extra[0x20] == 1
    assert struct.unpack_from("<Q", extra, 0x40)[0] == FIFA        # owner id
    device = (saves[(ACNH, 3)] / "ExtraData0").read_bytes()
    assert device[0x08:0x18] == bytes(16) and device[0x20] == 3
    last = struct.unpack("<Q", (ryujinx / es.INDEX_DIR / "0/lastPublishedId").read_bytes())[0]
    assert last == 4
    assert any("not an Eden user" in n for n in notes)


def test_a_fresh_ryujinx_gets_the_index_save_it_would_create(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    _import(ryujinx, eden)
    extra = (ryujinx / es.INDEX_DIR / "ExtraData0").read_bytes()
    assert struct.unpack_from("<Q", extra, 0x18)[0] == es.INDEX_SAVE_ID
    assert struct.unpack_from("<QQ", extra, 0x58) == (es.INDEX_SAVE_SIZE, es.INDEX_SAVE_SIZE)


def test_the_index_is_written_in_libhacs_key_order(tmp_path):
    """Out of order, LibHac's flat map aborts Ryujinx at boot (measured: MK8's
    account and device saves share a program id; type decides)."""
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    _import(ryujinx, eden)
    keys = [es._order(e) for e in es.read_index(ryujinx / es.INDEX_DIR)]
    assert keys == sorted(keys)
    mk8 = [k[1] for k in keys if k[0] == MK8]
    assert mk8 == [1, 3]


def test_ryujinxs_own_index_round_trips_byte_for_byte(tmp_path):
    raw = (Path(__file__).parent / "fixtures/ryujinx-index/imkvdb.arc").read_bytes()
    (tmp_path / "0").mkdir()
    (tmp_path / "0/imkvdb.arc").write_bytes(raw)
    entries = es.read_index(tmp_path)
    assert len(entries) == 2
    assert es.pack_index(entries) == raw


def test_a_second_launch_copies_nothing(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    _import(ryujinx, eden)
    before = _tree_hash(ryujinx)
    notes = _import(ryujinx, eden)
    assert not [n for n in notes if " copied from " in n]
    assert _tree_hash(ryujinx) == before


def test_a_save_ryujinx_already_has_is_never_overwritten(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    index = ryujinx / es.INDEX_DIR
    own = ryujinx / es.USER_SAVES / "0000000000000001"
    (own / "0").mkdir(parents=True)
    (own / "0/SaveData").write_bytes(b"played on ryujinx")
    attr = es.attribute(FIFA, USER, es.TYPE_ACCOUNT)
    value = struct.pack("<QQQBB", 1, 0, 0, es.SPACE_USER, 0).ljust(0x40, b"\0")
    (index / "0").mkdir(parents=True)
    (index / "0/imkvdb.arc").write_bytes(es.pack_index([(attr, value)]))
    (index / "0/lastPublishedId").write_bytes(struct.pack("<Q", 1))

    notes = _import(ryujinx, eden)

    assert (own / "0/SaveData").read_bytes() == b"played on ryujinx"
    assert _by_title(ryujinx)[(FIFA, 1)] == own
    assert any(n.startswith(f"{FIFA:016X}:1: Ryujinx has its own save") for n in notes)


def test_new_ids_never_reuse_a_folder_or_a_published_id(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    (ryujinx / es.USER_SAVES / "0000000000000007").mkdir(parents=True)   # orphan
    (ryujinx / es.INDEX_DIR / "0").mkdir(parents=True)
    (ryujinx / es.INDEX_DIR / "0/lastPublishedId").write_bytes(struct.pack("<Q", 3))
    _import(ryujinx, eden)
    ids = sorted(int(p.name, 16) for p in _by_title(ryujinx).values())
    assert ids == [8, 9, 10, 11]


def test_a_save_deleted_in_ryujinx_does_not_come_back(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    _import(ryujinx, eden)
    index = ryujinx / es.INDEX_DIR
    kept = [e for e in es.read_index(index) if struct.unpack_from("<Q", e[0])[0] != ACNH]
    (index / "0/imkvdb.arc").write_bytes(es.pack_index(kept))

    _import(ryujinx, eden)
    assert (ACNH, 3) not in _by_title(ryujinx)


def test_eden_is_never_written(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    before = _tree_hash(eden)
    _import(ryujinx, eden)
    _import(ryujinx, eden)
    assert _tree_hash(eden) == before


def test_several_eden_users_copy_only_the_device_saves(tmp_path):
    other = struct.pack("<QQ", 7, 9)
    eden, ryujinx = _eden(tmp_path, users=(USER, other)), tmp_path / "Ryujinx"
    notes = _import(ryujinx, eden)
    assert set(_by_title(ryujinx)) == {(MK8, 3), (ACNH, 3)}
    assert any("several Eden users" in n for n in notes)


def test_account_saves_follow_ryujinxs_user_not_edens(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    (ryujinx / "system").mkdir(parents=True)
    (ryujinx / "system/Profiles.json").write_text(json.dumps(
        {"profiles": [], "last_opened": "00000000000000020000000000000003"}))
    _import(ryujinx, eden)
    extra = (_by_title(ryujinx)[(FIFA, 1)] / "ExtraData0").read_bytes()
    assert extra[0x08:0x18] == struct.pack("<QQ", 2, 3)


def test_past_the_deadline_nothing_is_half_written(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    notes = _import(ryujinx, eden, deadline=time.monotonic() - 1)
    assert _by_title(ryujinx) == {}
    assert not list(ryujinx.glob("bis/user/save/*"))
    assert notes[-1].startswith("out of time")


def test_an_unreadable_index_refuses_and_writes_nothing(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    (ryujinx / es.INDEX_DIR / "0").mkdir(parents=True)
    (ryujinx / es.INDEX_DIR / "0/imkvdb.arc").write_bytes(b"garbage")
    with pytest.raises(es.ImportRefused):
        _import(ryujinx, eden)
    assert not (ryujinx / es.USER_SAVES).exists()


def test_a_running_emulator_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "running", lambda *a: [es.EDEN_APP_ID])
    with pytest.raises(es.ImportRefused, match="running"):
        _import(tmp_path / "Ryujinx", _eden(tmp_path))


# ── whose Eden saves ────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(11)
def test_a_profile_reads_its_own_eden_folder(tmp_path):
    root = tmp_path / "profile-saves"
    ryujinx, eden = tmp_path / "Ryujinx", _eden(tmp_path)
    (ryujinx / es.USER_SAVES).parent.mkdir(parents=True)
    target = root / "b40883f1e311c269" / "switch" / "user-save"
    target.mkdir(parents=True)
    os.symlink(target, ryujinx / es.USER_SAVES)
    assert es.eden_source(ryujinx, eden, root) == target.parent / "save"


def test_the_primary_reads_edens_own_folder(tmp_path):
    ryujinx, eden = tmp_path / "Ryujinx", _eden(tmp_path)
    assert es.eden_source(ryujinx, eden, tmp_path / "profile-saves") == eden / es.EDEN_SAVES


@pytest.mark.linux_until_port(11)
def test_eden_stopped_mid_profile_gives_the_primarys_parked_folder(tmp_path):
    ryujinx, eden = tmp_path / "Ryujinx", _eden(tmp_path)
    saves = eden / es.EDEN_SAVES
    parked = saves.with_name("save.gamecore-primary")
    saves.rename(parked)
    os.symlink(tmp_path, saves)
    assert es.eden_source(ryujinx, eden, None) == parked


@pytest.mark.linux_until_port(11)
def test_a_profile_import_lands_in_the_profiles_folders(tmp_path):
    """Through the links profile_saves placed: louis's saves stay louis's."""
    root, eden = tmp_path / "profile-saves", _eden(tmp_path)
    ryujinx = tmp_path / "Ryujinx"
    folder = root / "b40883f1e311c269" / "switch"
    louis_eden = folder / "save" / "0000000000000000" / EDEN_USER_DIR / f"{FIFA:016X}"
    louis_eden.mkdir(parents=True)
    (louis_eden / "SaveData").write_bytes(b"louis")
    (ryujinx / "bis/system/save").mkdir(parents=True)
    (ryujinx / "bis/user").mkdir(parents=True)
    for name, link in (("user-save", es.USER_SAVES), ("save-index", es.INDEX_DIR)):
        (folder / name).mkdir()
        os.symlink(folder / name, ryujinx / link)

    es.import_saves(ryujinx, es.eden_source(ryujinx, eden, root), eden)

    saves = _by_title(ryujinx)
    assert set(saves) == {(FIFA, 1)}
    assert (folder / "user-save" / saves[(FIFA, 1)].name / "0/SaveData").read_bytes() == b"louis"
    assert (folder / "save-index/0/imkvdb.arc").is_file()


# ── keys and firmware, at install ───────────────────────────────────────────

def test_keys_and_firmware_are_copied_only_where_ryujinx_has_none(tmp_path):
    eden, ryujinx = _eden(tmp_path), tmp_path / "Ryujinx"
    (eden / "keys").mkdir()
    (eden / "keys/prod.keys").write_text("eden prod")
    (eden / "keys/title.keys").write_text("eden title")
    nca = eden / ei.EDEN_FIRMWARE / "0a1b.nca"
    nca.mkdir(parents=True)
    (nca / "00").write_bytes(b"nca")
    (ryujinx / "system").mkdir(parents=True)
    (ryujinx / "system/prod.keys").write_text("own prod")

    ei.import_system_files(ryujinx, eden)

    assert (ryujinx / "system/prod.keys").read_text() == "own prod"
    assert (ryujinx / "system/title.keys").read_text() == "eden title"
    assert (ryujinx / ei.FIRMWARE / "0a1b.nca/00").read_bytes() == b"nca"
    (eden / ei.EDEN_FIRMWARE / "ffff.nca").mkdir()
    notes = ei.import_system_files(ryujinx, eden)
    assert not (ryujinx / ei.FIRMWARE / "ffff.nca").exists()
    assert "firmware: Ryujinx has its own, Eden's left alone" in notes
