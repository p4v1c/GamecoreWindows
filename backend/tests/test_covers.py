"""End-to-end tests for the cover pipeline, using synthetic game files.

Builds: a fake PS3 game folder (PARAM.SFO + ICON0.PNG), a fake PS4 folder,
a synthetic PSP ISO (ICON0 + PARAM.SFO inside ISO9660), synthetic PS1 (.bin,
raw 2352 MODE2) and PS2 (.iso, 2048) images with SYSTEM.CNF, and a fake
GameCube ISO header. Then exercises local_media directly and the FastAPI
endpoints via TestClient.

Run under pytest:  pytest backend/tests/test_covers.py
Or directly:       python backend/tests/test_covers.py

The GameTDB/xlenore checks need internet and carry @pytest.mark.network;
everything else is offline. `pytest -m "not network"` skips them.
"""
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# GAMECORE_PATH decides every path in backend.config, and it is read at import
# time — so it has to be set before anything under backend/ is imported. Under
# pytest that is conftest.py's job (it loads first, and hands the directory over
# in GAMECORE_TEST_ROOT); this branch covers running the file directly.
# Never os.environ.setdefault here: inheriting a GAMECORE_PATH from the shell
# would aim the cover cache at a real installation.
_root = os.environ.get("GAMECORE_TEST_ROOT")
if _root is None:
    _root = str(Path(tempfile.mkdtemp(prefix="gamecore-test-")) / "fake_root")
    os.environ["GAMECORE_TEST_ROOT"] = _root
    os.environ["GAMECORE_PATH"] = _root
ROOT = Path(_root)

import asyncio

import httpx
import pytest

from backend.services.platform import filesystem


def await_(coro):
    """Run one coroutine to completion from a sync test."""
    return asyncio.run(coro)


FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngdata" * 20


# ── Builders ──────────────────────────────────────────────────────────────────

def build_sfo(pairs: dict) -> bytes:
    keys = b""
    data = b""
    offsets = []
    for k, v in pairs.items():
        koff = len(keys)
        keys += k.encode() + b"\x00"
        doff = len(data)
        raw = v.encode() + b"\x00"
        data += raw
        offsets.append((koff, 0x0204, len(raw), len(raw), doff))
    key_tbl = 20 + 16 * len(pairs)
    data_tbl = key_tbl + len(keys)
    out = struct.pack("<4sIIII", b"\x00PSF", 0x101, key_tbl, data_tbl, len(pairs))
    for koff, fmt, ln, mx, doff in offsets:
        out += struct.pack("<HHIII", koff, fmt, ln, mx, doff)
    return out + keys + data


def drec(name: bytes, extent: int, size: int, flags: int) -> bytes:
    n = len(name)
    rec_len = 33 + n + ((33 + n) % 2)
    rec = bytearray(rec_len)
    rec[0] = rec_len
    struct.pack_into("<I", rec, 2, extent)
    struct.pack_into(">I", rec, 6, extent)
    struct.pack_into("<I", rec, 10, size)
    struct.pack_into(">I", rec, 14, size)
    rec[25] = flags
    struct.pack_into("<H", rec, 28, 1)
    struct.pack_into(">H", rec, 30, 1)
    rec[32] = n
    rec[33:33 + n] = name
    return bytes(rec)


def build_iso(files: dict[str, bytes]) -> bytes:
    """Single-level-or-two-level ISO9660: files = {"SYSTEM.CNF": b"..", "PSP_GAME/ICON0.PNG": b".."}."""
    S = 2048
    # Layout: 16 PVD, 17 terminator, 18 root dir, 19 subdir (if any), 20+ file data
    tree: dict[str, dict] = {"": {}}
    for path, content in files.items():
        parts = path.split("/")
        if len(parts) == 1:
            tree[""][parts[0]] = content
        else:
            tree.setdefault(parts[0], {})[parts[1]] = content

    subdirs = [d for d in tree if d]
    root_lba, sub_lba = 18, 19
    data_lba = 19 + len(subdirs)

    # Assign extents for file contents
    extents: dict[tuple, tuple] = {}
    lba = data_lba
    for d, entries in tree.items():
        for name, content in entries.items():
            extents[(d, name)] = (lba, len(content))
            lba += max(1, (len(content) + S - 1) // S)
    total = lba

    img = bytearray(total * S)

    def put(lba_, data):
        img[lba_ * S: lba_ * S + len(data)] = data

    # Root directory
    root = drec(b"\x00", root_lba, S, 2) + drec(b"\x01", root_lba, S, 2)
    for i, d in enumerate(subdirs):
        root += drec(d.encode(), sub_lba + i, S, 2)
    for name, _ in tree[""].items():
        e, s = extents[("", name)]
        root += drec(name.encode() + b";1", e, s, 0)
    put(root_lba, root)

    # Subdirectories
    for i, d in enumerate(subdirs):
        sub = drec(b"\x00", sub_lba + i, S, 2) + drec(b"\x01", root_lba, S, 2)
        for name, _ in tree[d].items():
            e, s = extents[(d, name)]
            sub += drec(name.encode() + b";1", e, s, 0)
        put(sub_lba + i, sub)

    # File data
    for (d, name), (e, s) in extents.items():
        put(e, tree[d][name])

    # PVD + terminator
    pvd = bytearray(S)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[156:156 + 34] = drec(b"\x00", root_lba, S, 2)[:34]
    put(16, pvd)
    term = bytearray(S)
    term[0] = 255
    term[1:6] = b"CD001"
    put(17, term)
    return bytes(img)


def to_raw_2352(iso: bytes, mode: int = 2) -> bytes:
    """Wrap 2048-byte sectors into raw 2352 sectors (like a PS1 .bin dump)."""
    head = 16 if mode == 1 else 24
    out = bytearray()
    for i in range(0, len(iso), 2048):
        sector = bytearray(2352)
        sector[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"  # sync
        sector[head:head + 2048] = iso[i:i + 2048].ljust(2048, b"\x00")
        out += sector
    return bytes(out)


# ── Fixture setup ────────────────────────────────────────────────────────────

def _close_open_files():
    """Windows cannot delete a file a handle holds: the playtime connection and
    the log files an earlier test's lifespan opened would block the wipe below.
    Both reopen on their next use."""
    from backend import db
    from backend.services import logs
    if db._DB is not None:
        asyncio.run(db._DB.close())
        db._DB = None
    for handler in logs._handlers():
        handler.close()


def setup_root():
    if ROOT.exists():
        _close_open_files()
        shutil.rmtree(ROOT)
    (ROOT / "config").mkdir(parents=True)

    # Put back what the rmtree above just took away. conftest.py symlinks the
    # catalogue into the throwaway root precisely so the consumers that build a
    # table from it — scraper.py's platform maps, gamemedia.py's aliases,
    # local_media.py's formats — are not empty under test. Wiping the root
    # deleted that link, so those tables came up empty for every module
    # imported after this fixture ran, and full for every module imported
    # before it: the suite was green or red depending on collection order, and
    # green for the wrong reason. Running this file on its own was the red one.
    link = ROOT / "catalog"
    if not link.exists():
        filesystem.link_dir(link, REPO / "catalog")

    # PS3 game folder — named by serial, like real dumps
    ps3 = ROOT / "emu/rpcs3/BLUS30443"
    (ps3 / "PS3_GAME").mkdir(parents=True)
    (ps3 / "PS3_GAME/ICON0.PNG").write_bytes(FAKE_PNG)
    (ps3 / "PS3_GAME/PARAM.SFO").write_bytes(build_sfo({
        "TITLE": "Demon's  Souls", "TITLE_ID": "BLUS30443", "CATEGORY": "DG",
    }))

    # PS4 game folder
    ps4 = ROOT / "emu/shadps4/CUSA00552"
    (ps4 / "sce_sys").mkdir(parents=True)
    (ps4 / "sce_sys/icon0.png").write_bytes(FAKE_PNG)
    (ps4 / "sce_sys/param.sfo").write_bytes(build_sfo({
        "TITLE": "Bloodborne", "TITLE_ID": "CUSA00552",
    }))

    # PSP ISO with embedded icon + sfo
    psp_dir = ROOT / "emu/ppsspp"
    psp_dir.mkdir(parents=True)
    (psp_dir / "SomePspGame.iso").write_bytes(build_iso({
        "PSP_GAME/ICON0.PNG": FAKE_PNG,
        "PSP_GAME/PARAM.SFO": build_sfo({"TITLE": "Crisis Core", "DISC_ID": "ULUS10336"}),
    }))

    # PS2 ISO with SYSTEM.CNF
    ps2_dir = ROOT / "emu/pcsx2"
    ps2_dir.mkdir(parents=True)
    (ps2_dir / "MyPs2Game.iso").write_bytes(build_iso({
        "SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_209.46;1\r\nVER = 1.00\r\n",
    }))

    # PS1 raw .bin with SYSTEM.CNF (MODE2 2352)
    ps1_dir = ROOT / "emu/duckstation"
    ps1_dir.mkdir(parents=True)
    (ps1_dir / "MyPs1Game.bin").write_bytes(to_raw_2352(build_iso({
        "SYSTEM.CNF": b"BOOT = cdrom:\\SCUS_949.00;1\r\nTCB = 4\r\n",
    })))

    # GameCube ISO header (just the ID6)
    gc_dir = ROOT / "emu/dolphin"
    gc_dir.mkdir(parents=True)
    (gc_dir / "Melee.iso").write_bytes(b"GALE01" + b"\x00" * 100)

    # systems.json pointing at the fake root
    import json
    systems = [
        {"id": "rpcs3", "name": "PS3", "romsPath": "emu/rpcs3/", "extensions": [], "scanDirs": True, "path": "/bin/true"},
        {"id": "shadps4", "name": "PS4", "romsPath": "emu/shadps4/", "extensions": [], "scanDirs": True, "path": "/bin/true"},
        {"id": "ppsspp", "name": "PSP", "romsPath": "emu/ppsspp/", "extensions": ["*.iso"], "path": "/bin/true"},
        {"id": "pcsx2", "name": "PS2", "romsPath": "emu/pcsx2/", "extensions": ["*.iso"], "path": "/bin/true"},
        {"id": "duckstation", "name": "PS1", "romsPath": "emu/duckstation/", "extensions": ["*.bin"], "path": "/bin/true"},
        {"id": "dolphin", "name": "GC", "romsPath": "emu/dolphin/", "extensions": ["*.iso"], "path": "/bin/true"},
    ]
    (ROOT / "config/systems.json").write_text(json.dumps(systems))
    (ROOT / "config/apps.json").write_text("[]")
    return ROOT


@pytest.fixture(scope="module")
def fake_root():
    """The synthetic game tree, built once for the module.

    Module-scoped on purpose: GAMECORE_PATH is frozen into backend.config at
    import time, so the root cannot move between tests. The cover tests below
    share it the way the pipeline shares a real library.
    """
    return setup_root()


@pytest.fixture(scope="module")
def client(fake_root):
    """TestClient over the real app, with the lifespan running."""
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


# ── Local parsers ─────────────────────────────────────────────────────────────

def test_sfo_parse_reads_the_title(fake_root):
    from backend.services import sfo
    meta = sfo.parse(ROOT / "emu/rpcs3/BLUS30443/PS3_GAME/PARAM.SFO")
    assert meta.get("TITLE") == "Demon's  Souls", f"sfo.parse TITLE ({meta})"


def test_iso9660_opens_a_2048_byte_image(fake_root):
    from backend.services.iso9660 import Iso9660
    iso = Iso9660.open(ROOT / "emu/ppsspp/SomePspGame.iso")
    assert iso is not None, "iso9660 open 2048"
    iso.close()


def test_iso9660_reads_a_nested_file(fake_root):
    from backend.services.iso9660 import Iso9660
    iso = Iso9660.open(ROOT / "emu/ppsspp/SomePspGame.iso")
    assert iso is not None
    try:
        assert iso.read_file("PSP_GAME/ICON0.PNG") == FAKE_PNG, "iso9660 nested file"
    finally:
        iso.close()


@pytest.mark.parametrize("system,rel,expected", [
    ("rpcs3", "emu/rpcs3/BLUS30443", "Demon's Souls"),
    ("shadps4", "emu/shadps4/CUSA00552", "Bloodborne"),
    ("ppsspp", "emu/ppsspp/SomePspGame.iso", "Crisis Core"),
])
def test_local_media_reads_the_title_off_the_dump(fake_root, system, rel, expected):
    from backend.services import local_media
    assert local_media.get_title(system, ROOT / rel) == expected


@pytest.mark.parametrize("system,rel,expected", [
    ("rpcs3", "emu/rpcs3/BLUS30443", ("ps3", "BLUS30443")),
    ("pcsx2", "emu/pcsx2/MyPs2Game.iso", ("ps2", "SLUS-20946")),
    ("duckstation", "emu/duckstation/MyPs1Game.bin", ("psx", "SCUS-94900")),
    ("dolphin", "emu/dolphin/Melee.iso", ("wii", "GALE01")),
])
def test_local_media_reads_the_disc_id(fake_root, system, rel, expected):
    from backend.services import local_media
    assert local_media.disc_id(system, ROOT / rel) == expected


@pytest.mark.parametrize("system,rel", [
    ("rpcs3", "emu/rpcs3/BLUS30443"),
    ("ppsspp", "emu/ppsspp/SomePspGame.iso"),
])
def test_local_media_extracts_the_embedded_icon(fake_root, system, rel, tmp_path):
    from backend.services import local_media
    dest = tmp_path / "out.png"
    assert local_media.extract_icon(system, ROOT / rel, dest)
    assert dest.read_bytes() == FAKE_PNG


# ── API end-to-end ────────────────────────────────────────────────────────────

def test_games_endpoint_lists_the_ps3_dump(client):
    r = client.get("/api/systems/rpcs3/games")
    assert r.status_code == 200, r.text
    games = r.json()
    assert games and games[0]["display_name"] == "Demon's Souls", str(games)
    assert games[0]["filename"] == "BLUS30443", str(games)


def test_ps3_cover_comes_from_the_local_icon0(client):
    r = client.get("/api/covers/rpcs3/BLUS30443")
    assert r.status_code == 200 and r.content == FAKE_PNG


def test_ps3_cover_is_cached_per_system(client):
    client.get("/api/covers/rpcs3/BLUS30443")
    assert (ROOT / "emu/covers/rpcs3/BLUS30443.png").is_file(), "PS3 cover cached per-system"


def test_psp_cover_comes_from_the_icon0_inside_the_iso(client):
    r = client.get("/api/covers/ppsspp/SomePspGame.iso")
    assert r.status_code == 200 and r.content == FAKE_PNG


def test_refresh_re_resolves_a_cached_cover(client):
    r = client.get("/api/covers/rpcs3/BLUS30443?refresh=1")
    assert r.status_code == 200 and r.content == FAKE_PNG


def test_cover_path_is_confined_to_the_roms_root(client, monkeypatch):
    """{filename:path} accepts slashes and '..' — the pipeline must not follow them.

    Offline on purpose: fetch_cover is stubbed out so the test is about
    containment and nothing else.
    """
    from backend.services import cover_pipeline

    secret = ROOT.parent / "secret.txt"
    secret.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not for you" * 20)

    async def no_scrape(*a, **kw):
        return None
    monkeypatch.setattr(cover_pipeline, "fetch_cover", no_scrape)

    for attempt in ("..%2F..%2F..%2Fsecret.txt", "../../../secret.txt", "..%2Fsecret.txt"):
        r = client.get(f"/api/covers/ppsspp/{attempt}")
        assert r.status_code == 404, f"{attempt} → {r.status_code}"
        assert secret.read_bytes() not in r.content

    # _rom_in_root is the guard; check it directly too, so the assertion does not
    # depend on how the 404 happened to be produced.
    system = {"id": "ppsspp", "romsPath": "emu/ppsspp/"}
    assert cover_pipeline._rom_in_root(system, "../../../secret.txt") is None
    assert cover_pipeline._rom_in_root(system, str(secret)) is None
    assert cover_pipeline._rom_in_root(system, "SomePspGame.iso") is not None, \
        "a genuine ROM still resolves"

    # Nothing may be cached outside the system's own covers directory.
    stray = [p for p in (ROOT / "emu/covers").iterdir() if p.is_file()]
    assert stray == [], f"cache files written outside a system dir: {stray}"


def test_no_miss_is_written_when_the_network_is_down(client, monkeypatch):
    """A .miss claims "nobody has this cover" — a dead network is not that.

    The worst case is the first boot: prefetch starts 15 s in, before the owner
    has configured Wi-Fi from this very interface, and every game in the library
    got a marker with a seven-day TTL.
    """
    from backend.services import cover_pipeline

    async def network_is_down(*a, **kw):
        raise httpx.RequestError("no route to host")

    monkeypatch.setattr(httpx.AsyncClient, "get", network_is_down)
    monkeypatch.setattr(httpx.AsyncClient, "head", network_is_down)

    (ROOT / "emu/melonds").mkdir(parents=True, exist_ok=True)
    (ROOT / "emu/melonds/Brand New Game.nds").write_bytes(b"nds")
    system = {"id": "melonds", "romsPath": "emu/melonds/"}

    assert await_(cover_pipeline.resolve(system, "Brand New Game.nds")) is None
    assert not (ROOT / "emu/covers/melonds/Brand New Game.miss").exists(), \
        "a failed request must not be recorded as a missing cover"


def test_a_genuine_miss_is_still_cached(client, monkeypatch):
    """The negative cache has to keep working, or every visit re-hits the CDN."""
    from backend.services import cover_pipeline

    async def not_found(*a, **kw):
        return httpx.Response(404, request=httpx.Request("GET", "https://example.invalid"))

    monkeypatch.setattr(httpx.AsyncClient, "get", not_found)
    monkeypatch.setattr(httpx.AsyncClient, "head", not_found)

    (ROOT / "emu/melonds").mkdir(parents=True, exist_ok=True)
    (ROOT / "emu/melonds/Nobody Has This.nds").write_bytes(b"nds")
    system = {"id": "melonds", "romsPath": "emu/melonds/"}

    assert await_(cover_pipeline.resolve(system, "Nobody Has This.nds")) is None
    assert (ROOT / "emu/covers/melonds/Nobody Has This.miss").exists(), \
        "a real 404 is worth remembering"


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_a_quota_or_server_error_is_not_a_miss(client, monkeypatch, status):
    from backend.services import cover_pipeline

    async def refused(*a, **kw):
        return httpx.Response(status, request=httpx.Request("GET", "https://example.invalid"))

    monkeypatch.setattr(httpx.AsyncClient, "get", refused)
    monkeypatch.setattr(httpx.AsyncClient, "head", refused)

    (ROOT / "emu/melonds").mkdir(parents=True, exist_ok=True)
    name = f"Rate Limited {status}.nds"
    (ROOT / "emu/melonds" / name).write_bytes(b"nds")
    system = {"id": "melonds", "romsPath": "emu/melonds/"}

    assert await_(cover_pipeline.resolve(system, name)) is None
    assert not (ROOT / "emu/covers/melonds" / f"Rate Limited {status}.miss").exists(), \
        f"HTTP {status} means try again later, not 'no cover exists'"


@pytest.mark.network
def test_gamecube_cover_is_looked_up_on_gametdb_by_id6(client):
    # No local icon → disc-ID lookup on GameTDB.
    #
    # The content-type is GameTDB's to choose, not ours to pin: they served PNG
    # until 2026-08, WebP since. The pipeline keeps the suffix the server sent
    # and the router maps it (_MEDIA_TYPES), so either is correct end to end —
    # what this test guards is that the disc-ID lookup finds a real cover.
    r = client.get("/api/covers/dolphin/Melee.iso")
    assert r.status_code == 200 and r.headers["content-type"] in {"image/png", "image/webp"} \
        and len(r.content) > 10000, \
        f"status={r.status_code} type={r.headers.get('content-type')} len={len(r.content)}"


@pytest.mark.network
def test_ps2_cover_is_looked_up_on_xlenore_by_serial(client):
    r = client.get("/api/covers/pcsx2/MyPs2Game.iso")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 10000, \
        f"status={r.status_code} len={len(r.content)}"


@pytest.mark.network
def test_ps1_cover_is_looked_up_on_xlenore_by_serial(client):
    r = client.get("/api/covers/duckstation/MyPs1Game.bin")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 10000, \
        f"status={r.status_code} len={len(r.content)}"


@pytest.mark.network
def test_a_game_nobody_has_a_cover_for_is_404_and_negatively_cached(client):
    # Needs the network: a .miss marker is only written when a lookup actually
    # came back empty, never when the request failed to leave the box (#14).
    (ROOT / "emu/ppsspp/Unknown_Game_zzz.iso").write_bytes(b"not an iso")

    r = client.get("/api/covers/ppsspp/Unknown_Game_zzz.iso")
    assert r.status_code == 404, "unknown game 404"
    assert (ROOT / "emu/covers/ppsspp/Unknown_Game_zzz.miss").is_file(), "negative cache written"

    # Second hit is served from the negative cache, without touching the network.
    r = client.get("/api/covers/ppsspp/Unknown_Game_zzz.iso")
    assert r.status_code == 404, "negative cache 404 again"


if __name__ == "__main__":
    setup_root()

    from fastapi.testclient import TestClient
    from backend.main import app

    _tmp = Path(tempfile.mkdtemp(prefix="gamecore-icons-"))

    def run(fn, *args):
        fn(ROOT, *args)
        print(f"[OK ] {fn.__name__}" + (f"[{args[0]}]" if args else ""))

    run(test_sfo_parse_reads_the_title)
    run(test_iso9660_opens_a_2048_byte_image)
    run(test_iso9660_reads_a_nested_file)
    for _a in (("rpcs3", "emu/rpcs3/BLUS30443", "Demon's Souls"),
               ("shadps4", "emu/shadps4/CUSA00552", "Bloodborne"),
               ("ppsspp", "emu/ppsspp/SomePspGame.iso", "Crisis Core")):
        run(test_local_media_reads_the_title_off_the_dump, *_a)
    for _a in (("rpcs3", "emu/rpcs3/BLUS30443", ("ps3", "BLUS30443")),
               ("pcsx2", "emu/pcsx2/MyPs2Game.iso", ("ps2", "SLUS-20946")),
               ("duckstation", "emu/duckstation/MyPs1Game.bin", ("psx", "SCUS-94900")),
               ("dolphin", "emu/dolphin/Melee.iso", ("wii", "GALE01"))):
        run(test_local_media_reads_the_disc_id, *_a)
    for _a in (("rpcs3", "emu/rpcs3/BLUS30443"), ("ppsspp", "emu/ppsspp/SomePspGame.iso")):
        test_local_media_extracts_the_embedded_icon(ROOT, *_a, tmp_path=_tmp)
        print(f"[OK ] test_local_media_extracts_the_embedded_icon[{_a[0]}]")

    with TestClient(app) as _client:
        for _fn in (test_games_endpoint_lists_the_ps3_dump,
                    test_ps3_cover_comes_from_the_local_icon0,
                    test_ps3_cover_is_cached_per_system,
                    test_psp_cover_comes_from_the_icon0_inside_the_iso,
                    test_refresh_re_resolves_a_cached_cover,
                    test_gamecube_cover_is_looked_up_on_gametdb_by_id6,
                    test_ps2_cover_is_looked_up_on_xlenore_by_serial,
                    test_ps1_cover_is_looked_up_on_xlenore_by_serial,
                    test_a_game_nobody_has_a_cover_for_is_404_and_negatively_cached):
            _fn(_client)
            print(f"[OK ] {_fn.__name__}")

    shutil.rmtree(_tmp, ignore_errors=True)
    print("\nAll tests passed.")


# ── A negative is only as good as the list of who was asked ───────────────────

def test_an_old_miss_marker_is_retried_once_a_new_source_exists(fake_root, monkeypatch):
    """Adding a better source must retry what a weaker one gave up on.

    Markers written before gamemedia existed are empty files. Reading them as
    "everything was asked" left a box showing a week of "no artwork" while a
    source that had the cover sat configured and idle — 13 of 58 metadata
    entries on the reference box the day ScreenScraper was added.
    """
    from backend.services import cover_pipeline, gamemedia

    miss = ROOT / "emu/covers/melonds/Old Marker.miss"
    miss.parent.mkdir(parents=True, exist_ok=True)
    miss.write_text("")                       # the pre-gamemedia shape

    monkeypatch.setattr(gamemedia, "available", lambda: False)
    assert cover_pipeline._miss_worth_another_look(miss) is False, \
        "no new source configured — the marker stands"

    monkeypatch.setattr(gamemedia, "available", lambda: True)
    assert cover_pipeline._miss_worth_another_look(miss) is True, \
        "gamemedia never asked — worth another look"


def test_a_marker_that_already_consulted_every_source_stands(fake_root, monkeypatch):
    from backend.services import cover_pipeline, gamemedia
    import json as _json

    miss = ROOT / "emu/covers/melonds/Fresh Marker.miss"
    miss.parent.mkdir(parents=True, exist_ok=True)
    miss.write_text(_json.dumps({"tiers_tried": ["gamemedia", "names"]}))

    monkeypatch.setattr(gamemedia, "available", lambda: True)
    assert cover_pipeline._miss_worth_another_look(miss) is False, \
        "gamemedia answered — the TTL is what governs from here"
