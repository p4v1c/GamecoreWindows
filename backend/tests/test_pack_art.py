"""Pictures packs ship for themes: catalog/<id>/art/<name>.<ext>.

The console photos used to live inside each theme, keyed by pack id in its
JavaScript: two themes, two copies, and changing a photo meant changing code.
They are the pack's now. What is held here: every console pack has one and the
grid hands its URL out, the URL serves it, the operator's copy wins, and the
two path segments cannot be walked out of the pack.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import paths
from backend.services.catalog import load_catalog
from backend.services.pack_art import art_for, pictures_in

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(monkeypatch):
    """The real app over the grid an install generates: the repo has no
    config/systems.json of its own."""
    import json
    rows = [{**s, "kind": "emulator"} for s in
            json.loads((ROOT / "install/generated/systems.json.dist").read_text())]
    monkeypatch.setattr("backend.routers.systems.list_all", lambda: rows)
    monkeypatch.setattr("backend.routers.systems.find",
                        lambda sid: next((r for r in rows if r["id"] == sid.lower()), None))
    return TestClient(app)


# Systems that are not one machine. Themes draw their logo where a photo goes.
NO_HARDWARE = {"lutris"}


def test_every_console_pack_ships_a_console_photo():
    missing = [pid for pid, pack in load_catalog(ROOT / "catalog").items()
               if pack.kind == "emulator" and not pack.superseded_by
               and pid not in NO_HARDWARE and "console" not in pack.art]
    assert missing == [], f"console packs with no art/console.*: {missing}"


def test_every_photo_says_where_it_comes_from():
    """A picture with no source is a picture nobody can relicense or replace."""
    bare = [p.parent.parent.name for p in sorted((ROOT / "catalog").glob("*/art/console.*"))
            if not (p.parent / "SOURCE.md").is_file()]
    assert bare == []


def test_the_grid_hands_each_system_its_pictures(client):
    rows = client.get("/api/systems").json()
    console = [r for r in rows if r.get("kind") == "emulator" and r.get("art", {}).get("console")]
    assert console, "no system carries art.console"
    url = console[0]["art"]["console"]
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.content[:4] == b"RIFF"


def test_unknown_names_and_escapes_are_refused(client):
    sid = client.get("/api/systems").json()[0]["id"]
    assert client.get(f"/api/systems/{sid}/art/nope").status_code == 404
    assert client.get(f"/api/systems/{sid}/art/..%2fpack").status_code == 404
    assert client.get("/api/systems/nosuch/art/console").status_code == 404


def test_the_operators_picture_wins_over_the_packs(tmp_path, monkeypatch):
    shipped = tmp_path / "catalog" / "azahar" / "art"
    shipped.mkdir(parents=True)
    (shipped / "console.webp").write_bytes(b"pack")
    mine = tmp_path / "data" / "assets" / "art" / "azahar"
    mine.mkdir(parents=True)
    (mine / "console.png").write_bytes(b"mine")
    monkeypatch.setattr("backend.services.pack_art._tiers", lambda: [tmp_path / "catalog"])
    monkeypatch.setattr("backend.services.pack_art.art_dir", lambda: tmp_path / "data" / "assets" / "art")
    assert art_for("azahar")["console"].read_bytes() == b"mine"


def test_a_pack_json_override_keeps_the_shipped_photo(tmp_path, monkeypatch):
    """An OTA correction or a local pack writes pack.json alone: the photo stays."""
    shipped = tmp_path / "catalog" / "azahar" / "art"
    shipped.mkdir(parents=True)
    (shipped / "console.webp").write_bytes(b"shipped")
    for tier in ("ota", "local"):
        (tmp_path / tier / "azahar").mkdir(parents=True)
        (tmp_path / tier / "azahar" / "pack.json").write_text("{}")
    monkeypatch.setattr("backend.services.pack_art._tiers", lambda: [
        tmp_path / "catalog", tmp_path / "ota", tmp_path / "local"])
    monkeypatch.setattr("backend.services.pack_art.art_dir", lambda: tmp_path / "none")
    assert art_for("azahar")["console"].read_bytes() == b"shipped"
    local_art = tmp_path / "local" / "azahar" / "art"
    local_art.mkdir()
    (local_art / "console.png").write_bytes(b"local")
    assert art_for("azahar")["console"].read_bytes() == b"local"


def test_only_plain_names_and_known_formats_count(tmp_path):
    for name in ("console.webp", "Back.PNG", "notes.txt", "a b.png", ".hidden.png"):
        (tmp_path / name).write_bytes(b"x")
    # NTFS matches names case-insensitively: Back.PNG is the "back" picture
    # (docs/dev-log/windows-port.md, D10).
    assert sorted(pictures_in(tmp_path)) == ["back", "console"]


def test_the_override_directory_is_part_of_the_data_layout():
    assert paths.art_dir().parts[-2:] == ("assets", "art")
