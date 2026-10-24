"""RPCS3 Smart Pack — identification, settings, sync, all on fixtures.

Every tree here is built under tmp_path. The fixture files copy the shapes
RPCS3 v0.0.41 itself writes and reads (config.yml's two-space layout,
RPCS3's "Used configuration" log dump, a player's own patch_config.yml the
pack must never touch), because a test that only reads back what
this module wrote would confirm its own assumptions and nothing else.
`test_yamlcpp_contract` goes further and re-reads the output with the yaml-cpp
RPCS3 is built against, when its source is available.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

from backend.tests.linux_until_port import linux_until_port_module

linux_until_port_module(11)  # rpcs3_smart imports fcntl (flock)

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
MOD_PATH = PACK / "files" / "rpcs3_smart.py"
spec = importlib.util.spec_from_file_location("rpcs3_smart", MOD_PATH)
smart = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = smart          # dataclasses resolve their module
spec.loader.exec_module(smart)

APP = "net.rpcs3.RPCS3"
H_GOW = "PPU-" + "a" * 40
H_UC2 = "PPU-" + "b" * 40
FLATPAK = ("flatpak", f"run {APP} --fullscreen --no-gui")


# ── fixture builders ─────────────────────────────────────────────────────────

def sfo(**fields) -> bytes:
    """A PARAM.SFO with utf8 strings (fmt 0x0204) and int32 (0x0404)."""
    keys = sorted(fields)
    key_table = b""
    data_table = b""
    index = b""
    for k in keys:
        v = fields[k]
        if isinstance(v, int):
            raw, fmt = struct.pack("<I", v), 0x0404
        else:
            raw, fmt = v.encode() + b"\0", 0x0204
        size = (len(raw) + 3) // 4 * 4
        index += struct.pack("<HHIII", len(key_table), fmt, len(raw), size, len(data_table))
        key_table += k.encode() + b"\0"
        data_table += raw.ljust(size, b"\0")
    key_table = key_table.ljust((len(key_table) + 3) // 4 * 4, b"\0")
    key_start = 20 + len(index)
    data_start = key_start + len(key_table)
    head = b"\0PSF" + struct.pack("<IIII", 0x101, key_start, data_start, len(keys))
    return head + index + key_table + data_table


CONFIG_YML = """\
Core:
  PPU Decoder: Recompiler (LLVM)
  SPU Block Size: Safe
  Accurate RSX reservation access: true
Video:
  Renderer: Vulkan
  Frame limit: PS3 Native
  Write Color Buffers: false
  Read Color Buffers: true
  Vblank Rate: 60
  Vblank NTSC Fixup: false
  Vulkan:
    Adapter: ""
    Asynchronous Texture Streaming 2: false
Audio:
  Renderer: Cubeb
Miscellaneous:
  Automatically start games after boot: true
"""

# A full dump, as RPCS3's settings window saves one, with one real personal
# choice (Frame limit 30 where config.yml says PS3 Native) and a key this
# build no longer knows, which must survive.
CUSTOM_YML = """\
Core:
  PPU Decoder: Recompiler (LLVM)
  SPU Block Size: Safe
  Accurate RSX reservation access: true
Video:
  Renderer: Vulkan
  Frame limit: 30
  Write Color Buffers: false
  Read Color Buffers: true
  Vblank Rate: 60
  Vblank NTSC Fixup: false
  Legacy Key From 0.0.30: 7
  Vulkan:
    Adapter: ""
    Asynchronous Texture Streaming 2: false
Audio:
  Renderer: Cubeb
Miscellaneous:
  Automatically start games after boot: true
"""

PATCH_OPS_GOW = """\
      - [ be32, 0x004cca58, 0x3d800f00 ]
      - [ alloc, 0x0f000003, 0x1000 ]
"""


def patch_yml(gow_ops: str = PATCH_OPS_GOW, uc2_versions: str = "[ 01.09 ]") -> str:
    # Mirrors the real file: `Anchors:` repeated, an anchor redefined, the
    # same hash appearing twice at top level, versions unquoted (01.01 must
    # stay a string), a Group shared by two patches.
    return f"""\
Version: 1.2

Anchors:
  gow_games: &gow_games
    "God of War: HD":
      BCES00791: [ 01.00 ]

PPU-{"c" * 40}:
  "Unrelated":
    Games: *gow_games
    Author: "x"
    Patch Version: 1.0
    Patch:
      - [ be32, 0x10, 0x20 ]

{H_GOW}:
  "Skip any videos with X button":
    Games:
      "God of War: HD":
        BCES00791: [ 01.01 ]
    Author: "x"
    Patch Version: 1.0
    Patch:
      - [ be32, 0x20, 0x30 ]

Anchors:
  gow_games: &gow_games
    "God of War: HD":
      BCES00791: [ 01.01 ]
      NPEA00255: [ 01.01 ]

{H_GOW}:
  "Native PS3 Timing":
    Games: *gow_games
    Author: "Dobrido"
    Notes: "Restores exact PS3 behavior. Requires 'Vblank NTSC Fixup' enabled."
    Patch Version: 2.0
    Patch:
{gow_ops}
{H_UC2}:
  "60 FPS":
    Games:
      "Uncharted 2: Among Thieves":
        BCES00509: {uc2_versions}
    Author: "x"
    Group: FPS
    Patch Version: 1.2
    Patch:
      - [ be32, 0x1, 0x2 ]
  "Unlock FPS":
    Games:
      "Uncharted 2: Among Thieves":
        BCES00509: {uc2_versions}
    Author: "x"
    Group: FPS
    Patch Version: 1.2
    Patch:
      - [ be32, 0x1, 0x3 ]
  "Bug Fix: Quit to Menu Crash":
    Games:
      "Uncharted 2: Among Thieves":
        BCES00509: {uc2_versions}
    Author: "ZEROx, illusion"
    Notes: "Fixes a rare bug where quitting to menu causes a game crash."
    Patch Version: 1.0
    Patch:
      - [ be32, 0x5, 0x6 ]
"""


def config_db(**games) -> str:
    base = {f"BLES{i:05d}": {"config": "Video:\n  Frame limit: 60\n"} for i in range(120)}
    base.update({s: {"config": c} for s, c in games.items()})
    return json.dumps({"return_code": 0, "timestamp": 1, "games": base})


class Box:
    """A home directory with a Flatpak RPCS3 tree and a ROM directory."""

    def __init__(self, tmp: Path):
        self.home = tmp / "home"
        self.root = self.home / ".var/app" / APP / "config/rpcs3"
        self.cache = self.home / ".var/app" / APP / "cache/rpcs3"
        self.roms = tmp / "userdata/emu/rpcs3"
        self.pack = tmp / "pack"
        self.proc = tmp / "proc"
        self.proc.mkdir(parents=True)
        (self.root / "GuiConfigs").mkdir(parents=True)
        (self.root / "patches").mkdir(parents=True)
        (self.root / "custom_configs").mkdir(parents=True)
        self.cache.mkdir(parents=True)
        (self.root / "config.yml").write_text(CONFIG_YML)
        (self.root / "patches/patch.yml").write_text(patch_yml())
        (self.root / "GuiConfigs/config_database.dat").write_text(config_db(
            BCES00791="Video:\n  Frame limit: 60",
            BCES00509="Video:\n  Write Color Buffers: true\n  Vulkan:\n    Asynchronous Texture Streaming 2: true\n  Old Removed Key: true",
        ))
        (self.pack / "files").mkdir(parents=True)
        pack = json.loads((PACK / "pack.json").read_text())
        pack["perGame"]["profiles"].append({
            "gameId": "BCES00791", "label": "test", "why": "test", "emulator": ">=0.0.41",
            "settings": {"Video": {"Vblank NTSC Fixup": True}}})
        (self.pack / "pack.json").write_text(json.dumps(pack))

    # ROMs and updates
    def disc(self, name: str, serial: str, app_ver: str = "01.00", category: str = "DG") -> Path:
        d = self.roms / name / "PS3_GAME"
        (d / "USRDIR").mkdir(parents=True, exist_ok=True)
        (d / "USRDIR/EBOOT.BIN").write_bytes(b"SCE\0")
        (d / "PARAM.SFO").write_bytes(sfo(TITLE_ID=serial, APP_VER=app_ver, VERSION="01.00",
                                          CATEGORY=category, TITLE=name, BOOTABLE=1))
        return self.roms / name

    def update(self, serial: str, app_ver: str, category: str = "GD", eboot: bool = True) -> None:
        d = self.root / "dev_hdd0/game" / serial
        (d / "USRDIR").mkdir(parents=True, exist_ok=True)
        if eboot:
            (d / "USRDIR/EBOOT.BIN").write_bytes(b"SCE\0")
        (d / "PARAM.SFO").write_bytes(sfo(TITLE_ID=serial, APP_VER=app_ver, CATEGORY=category,
                                          TITLE="update"))

    def gow(self) -> Path:
        rom = self.disc("God of War Collection", "BCES00791")
        self.update("BCES00791", "01.01")
        return rom

    def uc2(self) -> Path:
        rom = self.disc("Uncharted 2", "BCES00509")
        self.update("BCES00509", "01.09")
        return rom

    def custom(self, serial: str, text: str = CUSTOM_YML) -> Path:
        p = self.root / "custom_configs" / f"config_{serial}.yml"
        p.write_text(text)
        return p

    def prepare(self, rom: Path, **kw) -> dict:
        exec_path, exec_args = kw.pop("launcher", FLATPAK)
        kw.setdefault("version", "0.0.41-19497-c0598f61")
        return smart.prepare(rom=rom, home=self.home, exec_path=exec_path, exec_args=exec_args,
                             pack_dir=self.pack, proc=self.proc, **kw)

    def running(self) -> None:
        d = self.proc / "4242"
        d.mkdir()
        (d / "comm").write_text("rpcs3\n")


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def setting(report, key):
    return next(d for d in report["settings"] if d["key"] == key)


# ── identification ───────────────────────────────────────────────────────────

def test_identify_disc_with_update_reports_the_update_version(box):
    rom = box.gow()
    g = smart.identify(rom, box.root)
    assert (g.serial, g.region, g.disc_app_ver, g.update_app_ver, g.app_version) == \
        ("BCES00791", "Europe", "01.00", "01.01", "01.01")
    assert g.executable_dir.endswith("dev_hdd0/game/BCES00791/USRDIR")


def test_identify_ignores_an_update_that_is_not_one(box):
    rom = box.disc("Game", "BLUS30443")
    box.update("BLUS30443", "01.05", category="HG")
    assert smart.identify(rom, box.root).app_version == "01.00"
    box.update("BLUS30443", "01.05", category="GD", eboot=False)
    shutil.rmtree(box.root / "dev_hdd0/game/BLUS30443/USRDIR")
    assert smart.identify(rom, box.root).app_version == "01.00"


def test_identify_follows_vfs_yml(box, tmp_path):
    rom = box.disc("Game", "BCES00791")
    elsewhere = tmp_path / "hdd0"
    (box.root / "vfs.yml").write_text(f'/dev_hdd0/: "{elsewhere}/"\n')
    d = elsewhere / "game/BCES00791"
    (d / "USRDIR").mkdir(parents=True)
    (d / "USRDIR/EBOOT.BIN").write_bytes(b"x")
    (d / "PARAM.SFO").write_bytes(sfo(TITLE_ID="BCES00791", APP_VER="01.01", CATEGORY="GD"))
    assert smart.identify(rom, box.root).app_version == "01.01"


@pytest.mark.parametrize("serial,region", [("BLUS30443", "USA"), ("BLJM60200", "Japan"),
                                           ("NPEA00255", "Europe"), ("BCAS20097", "Asia")])
def test_region_from_serial(box, serial, region):
    assert smart.identify(box.disc(serial, serial), box.root).region == region


@pytest.mark.parametrize("serial", ["", "BCES0079", "bces00791", "BCES007911", "../../x"])
def test_bad_serial_is_not_identified_and_nothing_is_written(box, serial):
    rom = box.disc("Bad", serial or "X")
    (rom / "PS3_GAME/PARAM.SFO").write_bytes(sfo(TITLE_ID=serial, APP_VER="01.00", CATEGORY="DG"))
    before = sorted(p.name for p in box.root.rglob("*"))
    r = box.prepare(rom)
    assert "game" not in r and r["errors"]
    assert sorted(p.name for p in box.root.rglob("*")) == before


def test_missing_sfo_is_not_identified(box, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(smart.NotIdentified):
        smart.identify(tmp_path / "empty", box.root)


def test_no_custom_config_writes_nothing_and_lets_rpcs3_apply_its_database(box):
    rom = box.disc("Uncharted 2", "BCES00509")                # no custom config, no profile
    r = box.prepare(rom)
    assert not (box.root / "custom_configs/config_BCES00509.yml").exists()
    assert r["configSource"]["effective"].startswith("RPCS3 applies the official")
    assert setting(r, "Video/Write Color Buffers")["action"] == "native"
    assert r["written"] == []


def test_creating_a_custom_config_carries_the_official_recommendation(box):
    r = box.prepare(box.gow())                                  # needs Vblank NTSC Fixup
    text = (box.root / "custom_configs/config_BCES00791.yml").read_text()
    flat = smart.flatten(smart.yload(text))
    assert flat == {("Video", "Frame limit"): "60", ("Video", "Vblank NTSC Fixup"): "true"}
    assert r["written"][0]["created"] is True


def test_merge_is_surgical_and_keeps_unknown_keys(box):
    p = box.custom("BCES00509")
    box.prepare(box.uc2())
    after = p.read_text()
    changed = [(a, b) for a, b in zip(CUSTOM_YML.splitlines(), after.splitlines()) if a != b]
    assert changed == [("  Write Color Buffers: false", "  Write Color Buffers: true"),
                       ("    Asynchronous Texture Streaming 2: false",
                        "    Asynchronous Texture Streaming 2: true")]
    assert "Legacy Key From 0.0.30: 7" in after
    assert len(after.splitlines()) == len(CUSTOM_YML.splitlines())


def test_key_unknown_to_this_build_is_not_written(box):
    box.custom("BCES00509")
    r = box.prepare(box.uc2())
    d = setting(r, "Video/Old Removed Key")
    assert d["action"] == "skipped" and "not a setting" in d["reason"]


def test_personal_value_wins_and_is_reported(box):
    box.custom("BCES00791")                  # Frame limit 30 ≠ config.yml's PS3 Native
    r = box.prepare(box.gow())
    d = setting(r, "Video/Frame limit")
    assert (d["action"], d["current"], d["target"]) == ("kept-personal", "30", "60")
    assert "Frame limit" in r["notice"]
    flat = smart.flatten(smart.yload((box.root / "custom_configs/config_BCES00791.yml").read_text()))
    assert flat[("Video", "Frame limit")] == "30"


def test_pack_profile_beats_the_database(box):
    pack = json.loads((box.pack / "pack.json").read_text())
    pack["perGame"]["profiles"] = [{"gameId": "BCES00509", "label": "t", "why": "t",
                                    "emulator": ">=0.0.30",
                                    "settings": {"Video": {"Write Color Buffers": False,
                                                           "Read Color Buffers": False}}}]
    (box.pack / "pack.json").write_text(json.dumps(pack))
    box.custom("BCES00509")
    r = box.prepare(box.uc2())
    assert setting(r, "Video/Write Color Buffers")["source"] == "profile"
    assert setting(r, "Video/Read Color Buffers")["action"] == "write"


def test_value_changed_by_the_player_after_gamecore_is_theirs(box):
    p = box.custom("BCES00509")
    rom = box.uc2()
    box.prepare(rom)
    p.write_text(p.read_text().replace("Write Color Buffers: true", "Write Color Buffers: false"))
    r = box.prepare(rom)
    d = setting(r, "Video/Write Color Buffers")
    assert d["action"] == "kept-personal" and "after GameCore" in d["reason"]
    assert "Write Color Buffers: false" in p.read_text()


def test_idempotent(box):
    p = box.custom("BCES00791")
    rom = box.gow()
    box.prepare(rom)
    snap = {f: f.read_bytes() for f in box.root.rglob("*") if f.is_file()}
    mt = {f: f.stat().st_mtime_ns for f in snap}
    r = box.prepare(rom)
    assert r["written"] == []
    assert {f: f.read_bytes() for f in snap} == snap
    assert {f: f.stat().st_mtime_ns for f in snap} == mt
    assert p.read_text().count("Vblank NTSC Fixup") == 1


def test_undo_restores_bytes_and_removes_what_it_created(box):
    p = box.custom("BCES00509")
    box.prepare(box.uc2())
    box.prepare(box.gow())
    assert (box.root / "custom_configs/config_BCES00791.yml").exists()
    smart.undo(box.home, box.root)
    assert p.read_text() == CUSTOM_YML
    assert not (box.root / "custom_configs/config_BCES00791.yml").exists()


# ── robustness ───────────────────────────────────────────────────────────────

def test_nothing_is_written_while_rpcs3_runs(box):
    box.custom("BCES00791")
    box.running()
    r = box.prepare(box.gow())
    assert "already running" in r["errors"][0]
    assert not (box.root / "patch_config.yml").exists()
    assert "Vblank NTSC Fixup: false" in (box.root / "custom_configs/config_BCES00791.yml").read_text()


def test_nothing_is_written_when_the_lock_is_held(box):
    with smart.locked(box.home, timeout=0) as got:
        assert got
        r = box.prepare(box.gow(), deadline=time.monotonic() + 0.5)
    assert "lock" in r["errors"][0]
    assert not (box.root / "patch_config.yml").exists()


def test_nothing_is_written_past_the_deadline(box):
    r = box.prepare(box.gow(), deadline=time.monotonic() - 1)
    assert not (box.root / "patch_config.yml").exists()
    assert r["errors"]


def test_runtime_follows_the_launch(tmp_path):
    rt = smart.runtime_for_launch("flatpak", f"run {APP} --no-gui", tmp_path)
    assert (rt.kind, rt.config) == ("flatpak", tmp_path / f".var/app/{APP}/config/rpcs3")
    rt = smart.runtime_for_launch("/opt/GameCore/lib/rpcs3", "--no-gui", tmp_path)
    assert (rt.kind, rt.config) == ("native", tmp_path / ".config/rpcs3")


def test_native_launch_uses_the_native_tree(box, tmp_path):
    nat = box.home / ".config/rpcs3"
    shutil.copytree(box.root, nat)
    rom = box.disc("Uncharted 2", "BCES00509")
    (nat / "custom_configs/config_BCES00509.yml").write_text(CUSTOM_YML)
    d = nat / "dev_hdd0/game/BCES00509"
    (d / "USRDIR").mkdir(parents=True)
    (d / "USRDIR/EBOOT.BIN").write_bytes(b"x")
    (d / "PARAM.SFO").write_bytes(sfo(TITLE_ID="BCES00509", APP_VER="01.09", CATEGORY="GD"))
    r = box.prepare(rom, launcher=("/opt/GameCore/lib/rpcs3", "--fullscreen --no-gui"))
    assert r["runtime"]["kind"] == "native"
    assert "Write Color Buffers: true" in (nat / "custom_configs/config_BCES00509.yml").read_text()
    assert not (box.root / "custom_configs/config_BCES00509.yml").exists()


def test_sync_roots_follow_what_is_installed(tmp_path):
    home, gc = tmp_path / "h", tmp_path / "gc"
    (home / f".var/app/{APP}/config").mkdir(parents=True)
    gc.mkdir()
    assert [r.kind for r in smart.runtimes_for_sync(home, gc, APP)] == ["flatpak"]
    (gc / "lib").mkdir()
    (gc / "lib/rpcs3").write_text("x")
    assert [r.kind for r in smart.runtimes_for_sync(home, gc, APP)] == ["flatpak", "native"]


# ── RPCS3's log ──────────────────────────────────────────────────────────────

REAL_LOG = f"""\ufeffRPCS3 v0.0.41-19497-c0598f61 Alpha | HEAD | local_build
·! 0:00:00.073554 SYS: Booting application from command line: /userdata/emu/rpcs3/God of War Collection
·! 0:00:00.079908 SYS: Serial: BCES00791
·! 0:00:00.079910 SYS: Version: APP_VER=01.00 VERSION=01.00
·! 0:00:00.147584 SYS: Applying custom config: /x/custom_configs/config_BCES00791.yml
·! 0:00:00.148898 SYS: Found custom config. Ignoring database config
·S 0:00:00.200000 SYS: Updates found at /dev_hdd0/game/BCES00791/
·! 0:00:00.300000 SYS: Serial: BCES00791
·! 0:00:00.300001 SYS: Version: APP_VER=01.01 VERSION=01.00
·W 0:00:00.411913 ppu_loader: PPU executable hash: PPU-{"f" * 40}
·S 0:00:09.100000 PAT: Applied patch (hash='{H_GOW}', description='Native PS3 Timing', author='Dobrido', patch_version='2.0', file_version='1.2') (<- 39)
·S 0:00:09.100100 ppu_loader: PPU executable hash: {H_GOW} (<- 39)
"""


def test_log_parsing_matches_rpcs3s_wording():
    o = smart.parse_log(REAL_LOG)
    assert (o["rpcs3"], o["serial"], o["appVersion"], o["update"]) == \
        ("0.0.41-19497-c0598f61", "BCES00791", "01.01", True)
    assert o["applied"] == [{"hash": H_GOW, "description": "Native PS3 Timing",
                             "patchVersion": "2.0", "changes": 39, "precompile": False}]
    assert H_GOW in o["exeHashes"]
    assert {"databaseIgnored": True} in o["configSources"]


def patch_response(content: str, version: str = "1.2", code: int = 0) -> bytes:
    return json.dumps({"return_code": code, "version": version,
                       "sha256": hashlib.sha256(content.encode()).hexdigest(),
                       "patch": content}).encode()


def big_patch() -> str:
    return "Version: 1.2\n" + "".join(
        f"PPU-{i:040x}:\n  p:\n    Patch Version: 1.0\n    Patch:\n      - [ be32, 0x1, 0x2 ]\n"
        for i in range(60))


def fetcher(config: bytes | Exception, patch: bytes | Exception):
    def f(url, timeout):
        v = config if "api.rpcs3.net" in url else patch
        if isinstance(v, Exception):
            raise v
        return v
    return f


def test_sync_updates_both_databases(box):
    pol = smart.load_policy(None)
    pol["syncPatchDatabase"] = True
    rt = smart.flatpak_runtime(box.home)
    r = smart.sync_runtime(box.home, rt, pol, offline=False, force=True, proc=box.proc,
                           fetcher=fetcher(config_db().encode(), patch_response(big_patch())))
    assert r == {"configDatabase": "updated", "patchDatabase": "updated"}
    assert (box.root / "patches/patch.yml").read_text() == big_patch()


@pytest.mark.parametrize("bad", [
    urllib.error.URLError("no network"), b"not json",
    json.dumps({"return_code": 0, "games": {}}).encode(),
    patch_response("Version: 1.2\n", version="1.3"),
    json.dumps({"return_code": 0, "version": "1.2", "sha256": "0" * 64, "patch": "x"}).encode(),
])
def test_sync_failure_keeps_the_last_valid_data(box, bad):
    before_c = (box.root / "GuiConfigs/config_database.dat").read_bytes()
    before_p = (box.root / "patches/patch.yml").read_bytes()
    rt = smart.flatpak_runtime(box.home)
    pol = smart.load_policy(None)
    pol["syncPatchDatabase"] = True
    r = smart.sync_runtime(box.home, rt, pol, offline=False, force=True,
                           proc=box.proc, fetcher=fetcher(bad, bad))
    assert all(v.startswith("error") for v in r.values())
    assert (box.root / "GuiConfigs/config_database.dat").read_bytes() == before_c
    assert (box.root / "patches/patch.yml").read_bytes() == before_p


def test_sync_defers_while_rpcs3_runs(box):
    box.running()
    before = (box.root / "patches/patch.yml").read_bytes()
    pol = smart.load_policy(None)
    pol["syncPatchDatabase"] = True
    r = smart.sync_runtime(box.home, smart.flatpak_runtime(box.home), pol,
                           offline=False, force=True, proc=box.proc,
                           fetcher=fetcher(config_db().encode(), patch_response(big_patch())))
    assert r["patchDatabase"] == "deferred:rpcs3-running"
    assert (box.root / "patches/patch.yml").read_bytes() == before


def test_sync_exit_status_reflects_failure(box, monkeypatch, tmp_path):
    gc = tmp_path / "gc"
    gc.mkdir()
    monkeypatch.setattr(smart, "fetch", fetcher(urllib.error.URLError("down"),
                                                urllib.error.URLError("down")))
    monkeypatch.setattr(smart, "rpcs3_running", lambda proc=None: False)
    args = ["sync", "--gamecore-path", str(gc), "--force", "--home", str(box.home)]
    assert smart.main(args) == 1
    state = json.loads((smart.state_dir(box.home) / "state.json").read_text())
    assert state["ok"] is False and state["runtimes"]["flatpak"]["configDatabase"].startswith("error")
    assert smart.main(args[:-3] + ["--offline", "--home", str(box.home)]) == 0


def test_offline_launch_still_works_from_cache(box, monkeypatch):
    monkeypatch.setattr(smart, "fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    box.custom("BCES00791")
    r = box.prepare(box.gow())
    assert setting(r, "Video/Vblank NTSC Fixup")["action"] == "write" and not r["errors"]


def test_policy_defaults_and_clamps(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"version": 1, "syncHours": -20, "networkTimeoutSeconds": 500,
                             "patchEngineVersion": "9.9"}))
    got = smart.load_policy(p)
    assert (got["syncHours"], got["networkTimeoutSeconds"], got["patchEngineVersion"]) == (1, 30, "1.2")
    p.write_text("{bad")
    assert smart.load_policy(p)["syncHours"] == 6


@pytest.mark.parametrize("appid", ["", "../evil", "a/b", "a b", "x;touch nope", "@APPID@"])
def test_bad_appids(appid):
    with pytest.raises(ValueError):
        smart.validate_app_id(appid)


def test_non_https_redirect_is_refused():
    h = smart.HTTPSOnlyRedirectHandler()
    with pytest.raises(urllib.error.HTTPError, match="non-HTTPS"):
        h.redirect_request(None, None, 302, "Found", {}, "http://example.invalid/db")


def test_units_point_at_the_engine():
    svc = (PACK / "files/gamecore-rpcs3-smart-sync.service").read_text()
    assert "catalog/rpcs3/files/rpcs3_smart.py\" sync" in svc
    timer = (PACK / "files/gamecore-rpcs3-smart-sync.timer").read_text()
    assert "Unit=gamecore-rpcs3-smart-sync.service" in timer


# ── the real parser ──────────────────────────────────────────────────────────

HARNESS = HERE / "yamlcpp_harness.cpp"


@pytest.mark.skipif(not os.environ.get("RPCS3_YAMLCPP_SRC"),
                    reason="set RPCS3_YAMLCPP_SRC to RPCS3's yaml-cpp checkout")
def test_yamlcpp_contract(box, tmp_path):
    """What RPCS3's own yaml-cpp reads out of the files this module wrote."""
    src = Path(os.environ["RPCS3_YAMLCPP_SRC"])
    exe = tmp_path / "harness"
    subprocess.run(["g++", "-std=c++20", "-O1", f"-I{src}/include", str(HARNESS),
                    *map(str, sorted((src / "src").glob("*.cpp"))), "-o", str(exe)], check=True)
    box.custom("BCES00791")
    box.prepare(box.gow())
    cfg = subprocess.run([str(exe), "config", str(box.root / "custom_configs/config_BCES00791.yml")],
                         capture_output=True, text=True, check=True).stdout
    assert "/Video/Vblank NTSC Fixup = true" in cfg and "/Video/Frame limit = 30" in cfg


def _full_dump(extra: str) -> str:
    filler = "".join(f"  Filler {i}: 0\n" for i in range(160))
    return CUSTOM_YML.replace("Miscellaneous:\n", f"Filler:\n{filler}Miscellaneous:\n") \
                     .replace("  Vblank NTSC Fixup: false\n", f"  Vblank NTSC Fixup: false\n{extra}")


def test_schema_comes_from_the_newest_full_dump(box):
    # config.yml predates "New Key"; a recent full dump of another game has it.
    newest = box.custom("BLES00001", _full_dump("  New Key: false\n"))
    os.utime(newest, (time.time() + 60, time.time() + 60))
    box.custom("BCES00509")                              # this game's file lacks it
    db = json.loads((box.root / "GuiConfigs/config_database.dat").read_text())
    db["games"]["BCES00509"]["config"] = "Video:\n  New Key: true"
    (box.root / "GuiConfigs/config_database.dat").write_text(json.dumps(db))
    r = box.prepare(box.uc2())
    assert setting(r, "Video/New Key")["action"] == "write"


def test_value_that_config_yml_cannot_judge_is_kept(box):
    newest = box.custom("BLES00001", _full_dump("  New Key: false\n"))
    os.utime(newest, (time.time() + 60, time.time() + 60))
    box.custom("BCES00509", _full_dump("  New Key: false\n"))
    db = json.loads((box.root / "GuiConfigs/config_database.dat").read_text())
    db["games"]["BCES00509"]["config"] = "Video:\n  New Key: true"
    (box.root / "GuiConfigs/config_database.dat").write_text(json.dumps(db))
    r = box.prepare(box.uc2())
    d = setting(r, "Video/New Key")
    assert d["action"] == "kept-personal" and "cannot tell" in d["reason"]


def test_a_file_without_final_newline_keeps_it_that_way(box):
    p = box.custom("BCES00791", CUSTOM_YML.rstrip("\n"))
    box.prepare(box.gow())
    after = p.read_text()
    assert not after.endswith("\n")
    assert after.replace("Vblank NTSC Fixup: true", "Vblank NTSC Fixup: false") == CUSTOM_YML.rstrip("\n")


def test_second_launch_writes_nothing(box):
    box.custom("BCES00791")
    rom = box.gow()
    first = box.prepare(rom)
    assert first["written"]
    second = box.prepare(rom)
    assert second["written"] == []
    assert setting(second, "Video/Vblank NTSC Fixup")["action"] == "already"


def test_schema_from_rpcs3s_own_dump_wins(box):
    # config.yml and every custom config lack "Brand New Key"; the build's own
    # "Used configuration" dump has it, and has no "Old Removed Key".
    box.custom("BCES00509")
    db = json.loads((box.root / "GuiConfigs/config_database.dat").read_text())
    db["games"]["BCES00509"]["config"] = "Video:\n  Brand New Key: true\n  Old Removed Key: true"
    (box.root / "GuiConfigs/config_database.dat").write_text(json.dumps(db))
    dump = CUSTOM_YML.replace("  Vblank NTSC Fixup: false\n", "  Vblank NTSC Fixup: false\n  Brand New Key: false\n")
    log = ("RPCS3 v0.0.41-19497-c0598f61 Alpha\n·! 0 SYS: Used configuration:\n" + dump +
           "·! 0 SYS: Serial: BLES00001\n")
    (box.cache / "RPCS3.log").write_text(log)
    r = box.prepare(box.uc2(), version="0.0.41-19497-c0598f61")
    assert r["schemaSource"] == "RPCS3's own configuration dump"
    assert setting(r, "Video/Brand New Key")["action"] == "write"
    assert setting(r, "Video/Old Removed Key")["action"] == "skipped"
    # another build: the dump no longer counts
    r = box.prepare(box.uc2(), version="0.0.42-1-x")
    assert r["schemaSource"] == "config files (fallback)"


def test_used_configuration_parsed_from_a_real_log_shape():
    text = ("·! 0:00:00.14 SYS: Used configuration:\nCore:\n  PPU Decoder: LLVM\nVideo:\n  Vulkan:\n"
            "    Asynchronous Texture Streaming: true\n·! 0:00:00.2 SYS: next\n")
    assert smart.used_config_keys(text) == ["Core/PPU Decoder", "Video/Vulkan/Asynchronous Texture Streaming"]




def test_patches_are_never_touched(box):
    original = f"{H_UC2}:\n  Anything:\n    Title:\n      BCES00509:\n        '01.09':\n          Enabled: true\n"
    (box.root / "patch_config.yml").write_text(original)
    before = (box.root / "patches/patch.yml").read_bytes()
    box.custom("BCES00509")
    box.prepare(box.uc2())
    assert (box.root / "patch_config.yml").read_text() == original
    assert (box.root / "patches/patch.yml").read_bytes() == before


def test_shipped_policy_fetches_the_patch_catalogue_but_never_enables_anything(box):
    activations = f"{H_UC2}:\n  Mine:\n    Title:\n      BCES00509:\n        '01.09':\n          Enabled: true\n"
    (box.root / "patch_config.yml").write_text(activations)
    (box.root / "patches/imported_patch.yml").write_text("Version: 1.2\n")
    r = smart.sync_runtime(box.home, smart.flatpak_runtime(box.home), smart.load_policy(
        PACK / "files/patch-policy.json"), offline=False, force=True, proc=box.proc,
        fetcher=fetcher(config_db().encode(), patch_response(big_patch())))
    assert r == {"configDatabase": "updated", "patchDatabase": "updated"}
    assert (box.root / "patches/patch.yml").read_text() == big_patch()
    assert (box.root / "patch_config.yml").read_text() == activations
    assert (box.root / "patches/imported_patch.yml").read_text() == "Version: 1.2\n"
