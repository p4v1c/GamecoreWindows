"""`GET /api/overlays/resolve/{system_id}` — the one call a launch makes.

Electron asks this before it starts the window monitor, and whatever comes
back decides what is drawn over the game. Two properties matter more than the
happy path.

It must never fail a launch. A system with no bezel is the normal answer for
five of the thirteen — the 16:9 consoles have no black bars to hide — and a
404 or a 500 here would put a failed request in front of every one of those
launches on a box that is behaving perfectly.

And it must not be a way to read the disk. `system_id` names a directory under
the overlays root and `rom` names a file inside it; both arrive from the
renderer, which got the system id from a tile the operator can edit by hand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import bezels, paths                        # noqa: E402
from backend.tests.test_bezels import write_png                   # noqa: E402


@pytest.fixture
def client():
    from backend import main
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "GAMECORE_ROOT", tmp_path)
    monkeypatch.setattr(paths, "GAMECORE_DATA", tmp_path)
    bezels.forget()
    (tmp_path / "assets" / "overlays").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "overlays.json").write_text(json.dumps(
        {"duckstation": {"window_rect": {"x": 0, "y": 0, "w": 1920, "h": 1080},
                         "hole": {"x": 240, "y": 52, "w": 1440, "h": 968}}}))
    yield tmp_path
    bezels.forget()


def test_two_games_resolve_to_two_bezels_over_the_wire(client, library):
    """The phase's acceptance criterion, seen from where Electron stands."""
    overlays = library / "assets" / "overlays"
    write_png(overlays / "duckstation.png", 1920, 1080, (240, 52, 1440, 968))
    write_png(overlays / "duckstation" / "Crash Bandicoot (USA).png",
              1920, 1080, (240, 0, 1440, 1080))
    write_png(overlays / "duckstation" / "Silent Hill (USA).png",
              1920, 1080, (300, 60, 1320, 960))

    crash = client.get("/api/overlays/resolve/duckstation",
                       params={"rom": "Crash Bandicoot (Europe) (v1.1).bin"}).json()
    hill = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Silent Hill (USA) (Disc 1).chd"}).json()

    assert crash["source"] == hill["source"] == "game"
    assert crash["asset"] != hill["asset"]
    assert crash["hole"] == {"x": 240, "y": 0, "w": 1440, "h": 1080}
    assert hill["hole"] == {"x": 300, "y": 60, "w": 1320, "h": 960}


def test_a_game_with_no_bezel_of_its_own_gets_the_system_one(client, library):
    write_png(library / "assets" / "overlays" / "duckstation.png",
              1920, 1080, (240, 52, 1440, 968))
    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Some Import (Japan).chd"}).json()
    assert body["source"] == "system"
    assert body["asset"] == "/assets/overlays/duckstation.png"


def test_a_system_with_no_bezel_at_all_is_a_200_and_not_a_404(client, library):
    """Five of the thirteen systems are 16:9 and want no overlay. This is the
    common answer, not an error, and it has to be cheap and quiet."""
    body = client.get("/api/overlays/resolve/rpcs3",
                      params={"rom": "Whatever.pkg"}).json()
    assert body == {"system_id": "rpcs3", "source": "none",
                    "asset": None, "hole": None, "frame": None,
                    # Always present, `None` for the eleven packs that are one
                    # console: the monitor echoes this field back when it
                    # reports a measurement, and a key that came and went would
                    # make the request body a different shape per launch.
                    "console": None,
                    "measure": False}


def test_no_rom_at_all_still_answers(client, library):
    """An app tile launches with no ROM path — `game_key` is then the system
    id itself and there is nothing to look a game up by."""
    write_png(library / "assets" / "overlays" / "duckstation.png",
              1920, 1080, (240, 52, 1440, 968))
    body = client.get("/api/overlays/resolve/duckstation").json()
    assert body["source"] == "system"


@pytest.mark.parametrize("bad", ["..", "../../etc", "a/b", "", " ", "x" * 65])
def test_a_system_id_that_is_not_one_is_refused(client, library, bad):
    """`system_id` is joined onto the overlays root. Without the guard, `..`
    walks out of it and the endpoint reports whether arbitrary PNGs exist."""
    r = client.get(f"/api/overlays/resolve/{bad}")
    assert r.status_code in (400, 404), r.text


def test_a_rom_name_cannot_point_the_lookup_at_another_directory(client, library):
    """Only the filename is used. A `rom` carrying directories would otherwise
    let the pack index be built somewhere nobody chose."""
    overlays = library / "assets" / "overlays"
    write_png(overlays / "duckstation.png", 1920, 1080, (240, 52, 1440, 968))
    write_png(overlays / "duckstation" / "Crash Bandicoot (USA).png",
              1920, 1080, (240, 0, 1440, 1080))

    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "../../../Crash Bandicoot (USA).cue"}).json()
    # The traversal is stripped, so this still resolves the game — by name,
    # from inside the pack directory, which is the only place it may come from.
    assert body["source"] == "game"
    assert body["asset"] == ("/assets/overlays/duckstation/"
                             "Crash%20Bandicoot%20%28USA%29.png")


# ── Installing a downloaded pack ────────────────────────────────────────────

def _addon_pack(library: Path) -> Path:
    d = library / "addons" / "bezelproject" / "psx"
    d.mkdir(parents=True)
    write_png(d / "Crash Bandicoot (USA).png", 1920, 1080, (240, 0, 1440, 1080))
    write_png(d / "Silent Hill (USA).png", 1920, 1080, (300, 60, 1320, 960))
    return d


def test_an_installed_pack_resolves_per_game(client, library):
    """The point of the whole addon: after this, two games have two bezels and
    nobody typed anything."""
    src = _addon_pack(library)
    r = client.post("/api/overlays/packs/duckstation", json={"source": str(src)})
    assert r.json()["installed"] == 2

    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Crash Bandicoot (Europe).chd"}).json()
    assert body["source"] == "game"


def test_only_png_files_are_taken(client, library):
    """A Bezel Project pack ships a `.info` beside every image, and archives
    arrive with readmes and licence files in them."""
    src = _addon_pack(library)
    (src / "Crash Bandicoot (USA).info").write_text("1440 1080 240 0")
    (src / "README.md").write_text("#")
    (src / "sub").mkdir()

    body = client.post("/api/overlays/packs/duckstation", json={"source": str(src)}).json()
    assert body == {"system_id": "duckstation", "installed": 2, "skipped": 3}


def test_a_pack_outside_the_addons_directory_is_refused(client, library, tmp_path):
    """Without this the endpoint is "copy any file the backend can read into a
    directory served over HTTP", which is a much bigger thing than bezels."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    write_png(outside / "x.png", 40, 20, (10, 0, 20, 20))

    r = client.post("/api/overlays/packs/duckstation", json={"source": str(outside)})
    assert r.status_code == 400


def test_a_traversal_out_of_the_addons_directory_is_refused(client, library):
    _addon_pack(library)
    r = client.post("/api/overlays/packs/duckstation",
                    json={"source": str(library / "addons" / ".." / "config")})
    assert r.status_code == 400


@pytest.mark.linux_until_port(4)
def test_a_symlink_in_a_pack_is_not_followed(client, library, tmp_path):
    """A pack is an archive someone downloaded. A link inside one is a way to
    reach back out of the directory this endpoint went to trouble to confine."""
    src = _addon_pack(library)
    secret = tmp_path / "id_rsa.png"
    write_png(secret, 40, 20, (10, 0, 20, 20))
    (src / "sneaky.png").symlink_to(secret)

    body = client.post("/api/overlays/packs/duckstation", json={"source": str(src)}).json()
    assert body["installed"] == 2
    assert not (library / "assets" / "overlays" / "duckstation" / "sneaky.png").exists()


def test_installing_over_an_existing_pack_replaces_by_name(client, library):
    """Nothing is deleted and no directory is moved — a second pack simply
    wins the names it shares with the first."""
    src = _addon_pack(library)
    client.post("/api/overlays/packs/duckstation", json={"source": str(src)})
    write_png(src / "Crash Bandicoot (USA).png", 1920, 1080, (0, 100, 1920, 880))
    (src / "Silent Hill (USA).png").unlink()

    client.post("/api/overlays/packs/duckstation", json={"source": str(src)})
    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Crash Bandicoot (USA).cue"}).json()
    assert body["hole"] == {"x": 0, "y": 100, "w": 1920, "h": 880}
    # The bezel the second pack did not carry is still there.
    assert (library / "assets" / "overlays" / "duckstation" / "Silent Hill (USA).png").exists()


# ── Learning that the emulator draws somewhere else ─────────────────────────

def test_a_reported_mismatch_moves_the_hole_on_the_next_launch(client, library):
    """The loop this endpoint exists to close.

    The bezel announces 4:3 and the emulator letterboxes instead. The first
    launch draws the announced hole and reports what it saw; the second starts
    out right and is told not to bother looking again.
    """
    # No PNG on purpose. With artwork on screen the image covers everything
    # and the hole only ever decides the fallback bars — so `measure` is now
    # False the moment an asset resolves, and the loop below is exercised on
    # the one shape it visibly corrects: the declared frame.
    first = client.get("/api/overlays/resolve/duckstation").json()
    assert first["source"] == "declared"
    assert first["hole"] == {"x": 240, "y": 52, "w": 1440, "h": 968}
    assert first["measure"] is True

    r = client.post("/api/overlays/measured/duckstation", json={
        "announced": first["hole"],
        "measured": {"x": 0, "y": 120, "w": 1920, "h": 840},
        "window": {"w": 1920, "h": 1080},
    })
    assert r.json() == {"ok": True, "applied": True, "reason": None}

    second = client.get("/api/overlays/resolve/duckstation").json()
    assert second["hole"] == {"x": 0, "y": 120, "w": 1920, "h": 840}
    # The announced rectangle is still reported, because it is the key the
    # correction was filed under — relearning against the corrected value
    # would move the hole a little further every single launch.
    assert second["announced"] == {"x": 240, "y": 52, "w": 1440, "h": 968}
    assert second["measure"] is False


def test_a_logo_on_a_loading_screen_is_reported_and_refused(client, library):
    """The monitor can only decide the two samples agreed. Whether what they
    agreed on is a game is decided here, where it can be tested."""
    r = client.post("/api/overlays/measured/duckstation", json={
        "announced": {"x": 240, "y": 52, "w": 1440, "h": 968},
        "measured": {"x": 860, "y": 470, "w": 200, "h": 140},
        "window": {"w": 1920, "h": 1080},
    })
    assert r.json()["applied"] is False

    body = client.get("/api/overlays/resolve/duckstation").json()
    assert body["hole"] == {"x": 240, "y": 52, "w": 1440, "h": 968}
    # Nothing was written, so the next launch looks again rather than giving up.
    assert body["measure"] is True


def test_a_measurement_within_tolerance_changes_nothing(client, library):
    """Otherwise every launch rewrites the hole by a pixel and the box never
    stops capturing."""
    write_png(library / "assets" / "overlays" / "duckstation.png",
              1920, 1080, (240, 0, 1440, 1080))
    r = client.post("/api/overlays/measured/duckstation", json={
        "announced": {"x": 240, "y": 0, "w": 1440, "h": 1080},
        "measured": {"x": 242, "y": 1, "w": 1438, "h": 1079},
        "window": {"w": 1920, "h": 1080},
    })
    assert r.json() == {"ok": True, "applied": False, "reason": "within tolerance"}


def test_a_malformed_measurement_is_refused_not_crashed(client, library):
    r = client.post("/api/overlays/measured/duckstation", json={
        "announced": {"x": 0}, "measured": {}, "window": {},
    })
    assert r.status_code == 400


# ── The player's own choice ─────────────────────────────────────────────────

def _pack(library: Path) -> None:
    overlays = library / "assets" / "overlays"
    write_png(overlays / "duckstation.png", 1920, 1080, (240, 52, 1440, 968))
    write_png(overlays / "duckstation" / "Crash Bandicoot (USA).png",
              1920, 1080, (240, 0, 1440, 1080))


def test_turning_an_overlay_off_draws_nothing_at_all(client, library):
    """Not the declared frame. Switching a bezel off used to have nowhere to
    land except "no PNG", and "no PNG" means "draw the JSON frame" — so the
    artwork would be replaced by black bars, which is the opposite of the
    request."""
    _pack(library)
    r = client.put("/api/overlays/choices/duckstation",
                   json={"rom": "Crash Bandicoot (USA).cue", "choice": "off"})
    assert r.status_code == 200, r.text

    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Crash Bandicoot (USA).cue"}).json()
    assert body["source"] == "off"
    assert body["asset"] is None
    assert body["hole"] is None


def test_a_choice_applies_to_the_game_and_not_to_the_system(client, library):
    _pack(library)
    client.put("/api/overlays/choices/duckstation",
               json={"rom": "Crash Bandicoot (USA).cue", "choice": "off"})

    other = client.get("/api/overlays/resolve/duckstation",
                       params={"rom": "Silent Hill (USA).cue"}).json()
    assert other["source"] == "system"


def test_a_choice_survives_the_dump_being_replaced(client, library):
    """Stored under the normalised key, so the European re-dump of a game
    keeps the setting made on the American one. A preference filed under a
    filename would silently reset the day someone swapped a ROM."""
    _pack(library)
    client.put("/api/overlays/choices/duckstation",
               json={"rom": "Crash Bandicoot (USA).cue", "choice": "off"})

    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Crash Bandicoot (Europe) (Rev 1).chd"}).json()
    assert body["source"] == "off"


def test_going_back_to_automatic_forgets_the_choice(client, library):
    """Removed, not stored as "auto". The automatic answer changes when a pack
    is installed, and a written-down answer would never notice."""
    _pack(library)
    client.put("/api/overlays/choices/duckstation",
               json={"rom": "Crash Bandicoot (USA).cue", "choice": "off"})
    r = client.put("/api/overlays/choices/duckstation",
                   json={"rom": "Crash Bandicoot (USA).cue", "choice": None})
    assert r.json()["current"] is None
    assert r.json()["resolved"]["source"] == "game"


def test_the_system_bezel_can_be_chosen_over_the_game_one(client, library):
    """It does not live in the pack directory, so the index that answers every
    other lookup cannot find it — the one case that needs its own branch."""
    _pack(library)
    r = client.put("/api/overlays/choices/duckstation",
                   json={"rom": "Crash Bandicoot (USA).cue", "choice": "duckstation.png"})
    assert r.status_code == 200, r.text
    assert r.json()["resolved"]["asset"] == "/assets/overlays/duckstation.png"


def test_a_bezel_that_does_not_exist_cannot_be_chosen(client, library):
    """A preference is only ever allowed to name something on this box.
    Otherwise the setting saves happily and does nothing at launch."""
    _pack(library)
    r = client.put("/api/overlays/choices/duckstation",
                   json={"rom": "Crash Bandicoot (USA).cue", "choice": "Nonesuch.png"})
    assert r.status_code == 404


def test_a_chosen_bezel_that_later_disappears_falls_back(client, library):
    """A pack uninstalled under a preference that names it. Falling back to
    the cascade beats an overlay pointing at a missing file."""
    _pack(library)
    client.put("/api/overlays/choices/duckstation",
               json={"rom": "Crash Bandicoot (USA).cue",
                     "choice": "Crash Bandicoot (USA).png"})
    (library / "assets" / "overlays" / "duckstation" / "Crash Bandicoot (USA).png").unlink()

    body = client.get("/api/overlays/resolve/duckstation",
                      params={"rom": "Crash Bandicoot (USA).cue"}).json()
    assert body["source"] == "system"


def test_the_options_screen_is_offered_only_what_exists(client, library):
    _pack(library)
    body = client.get("/api/overlays/choices/duckstation",
                      params={"rom": "Crash Bandicoot (USA).cue"}).json()
    assert body["current"] is None
    assert [o["level"] for o in body["options"]] == ["game", "system"]

    bare = client.get("/api/overlays/choices/duckstation",
                      params={"rom": "Silent Hill (USA).cue"}).json()
    assert [o["level"] for o in bare["options"]] == ["system"]


def test_a_choice_needs_a_game(client, library):
    r = client.put("/api/overlays/choices/duckstation", json={"rom": "", "choice": "off"})
    assert r.status_code == 400


def test_the_declared_hole_answers_when_the_png_is_missing(client, library):
    """A box whose bezel was never uploaded still gets the frame overlays.json
    describes — that is what the JSON is for, and all it is for."""
    body = client.get("/api/overlays/resolve/duckstation").json()
    assert body["source"] == "declared"
    assert body["asset"] is None
    assert body["hole"] == {"x": 240, "y": 52, "w": 1440, "h": 968}


# ── Depositing a bezel, and the upload that used to be accepted ─────────────

@pytest.fixture
def multi(library):
    """A box whose grid knows mgba runs three consoles."""
    from backend.services import consoles
    consoles.forget()
    (library / "config" / "systems.json").write_text(json.dumps([{
        "id": "mgba", "platform": "GBA",
        "extensions": ["*.gba", "*.gbc", "*.gb", "*.zip"],
        "consoles": [
            {"id": "gba", "label": "Game Boy Advance", "extensions": ["*.gba"]},
            {"id": "gb", "label": "Game Boy", "extensions": ["*.gb"]},
        ],
    }]))
    yield library
    consoles.forget()


def _png_bytes(tmp_path, name, w, h, hole):
    write_png(tmp_path / name, w, h, hole)
    return (tmp_path / name).read_bytes()


def test_a_console_bezel_can_be_deposited_and_is_then_resolved(client, multi, tmp_path):
    """The whole point, end to end: drop a PNG for one console, and only that
    console's games get it."""
    blob = _png_bytes(tmp_path, "up.png", 1920, 1080, (150, 0, 1620, 1080))
    r = client.post("/api/overlays/mgba/consoles/gba",
                    files={"file": ("gba.png", blob, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["hole"]["w"] == 1620
    assert Path(r.json()["path"]).name == "mgba.gba.png"

    gba = client.get("/api/overlays/resolve/mgba",
                     params={"rom": "Pokemon Emerald (USA).gba"}).json()
    assert gba["source"] == "console" and gba["console"] == "gba"

    # The Game Boy has no bezel of its own yet and must not inherit that one.
    gb = client.get("/api/overlays/resolve/mgba",
                    params={"rom": "Tetris (World).gb"}).json()
    assert gb["source"] == "none"


def test_a_console_the_pack_never_declared_is_refused(client, multi, tmp_path):
    """Not a character-class check: a file under a name the cascade never looks
    for would upload happily and then do nothing at all."""
    blob = _png_bytes(tmp_path, "up.png", 40, 20, (10, 0, 20, 20))
    r = client.post("/api/overlays/mgba/consoles/gbc",
                    files={"file": ("x.png", blob, "image/png")})
    assert r.status_code == 404


def test_an_image_with_no_transparent_area_is_refused(client, multi, tmp_path):
    """The gap in the old validation, and the worst one.

    Magic bytes right, size right, upload completes — and the result is a
    rectangle painted over the whole game with nothing on screen to say so.
    """
    # `hole=None` is write_png's fully opaque frame: valid PNG, no hole.
    blob = _png_bytes(tmp_path, "solid.png", 40, 20, None)

    r = client.post("/api/overlays/mgba/consoles/gba",
                    files={"file": ("solid.png", blob, "image/png")})
    assert r.status_code == 422
    assert "transparent" in r.json()["detail"]
    # And nothing was published — the refusal must not leave the file behind.
    assert not (multi / "assets" / "overlays" / "mgba.gba.png").exists()


def test_a_refused_upload_does_not_destroy_the_bezel_already_there(client, multi, tmp_path):
    good = _png_bytes(tmp_path, "good.png", 1920, 1080, (150, 0, 1620, 1080))
    client.post("/api/overlays/mgba/consoles/gba",
                files={"file": ("g.png", good, "image/png")})
    solid = _png_bytes(tmp_path, "solid.png", 40, 20, None)
    client.post("/api/overlays/mgba/consoles/gba",
                files={"file": ("s.png", solid, "image/png")})

    bezels.forget()
    assert bezels.measure_hole(multi / "assets" / "overlays" / "mgba.gba.png")["w"] == 1620


def test_a_measurement_may_only_name_a_console_the_pack_declares(client, multi):
    """The body arrives over HTTP; an arbitrary string here would let a caller
    write cache keys of its own choosing into the corrections file."""
    payload = {"announced": {"x": 420, "y": 0, "w": 1080, "h": 1080},
               "measured": {"x": 150, "y": 0, "w": 1620, "h": 1080},
               "window": {"w": 1920, "h": 1080}}
    assert client.post("/api/overlays/measured/mgba",
                       json={**payload, "console": "gba"}).status_code == 200
    assert client.post("/api/overlays/measured/mgba",
                       json={**payload, "console": "../../etc"}).status_code == 400


# ── What is in place, and what is still empty ──────────────────────────────

def test_the_slots_of_a_multi_console_pack_show_filled_and_empty(client, multi, tmp_path):
    """The question a manager UI asks and `choices` cannot answer.

    `choices` lists what exists, so a console with no bezel is simply absent
    from it — indistinguishable, from a browser, from an upload that failed.
    """
    blob = _png_bytes(tmp_path, "up.png", 1920, 1080, (150, 0, 1620, 1080))
    client.post("/api/overlays/mgba/consoles/gba",
                files={"file": ("gba.png", blob, "image/png")})

    slots = client.get("/api/overlays/mgba/slots").json()["slots"]
    by = {s["console"]: s for s in slots}

    assert [s["level"] for s in slots] == ["system", "console", "console"]
    assert by[None]["present"] is False                   # no mgba.png here
    assert by["gba"]["present"] is True
    assert by["gba"]["ratio"] == "3:2"
    assert by["gba"]["filename"] == "mgba.gba.png"
    # The empty one still names the file it is waiting for: the convention has
    # to be visible before anything exists, or it is not guessable at all.
    assert by["gb"]["present"] is False
    assert by["gb"]["filename"] == "mgba.gb.png"
    assert by["gb"]["hole"] is None


def test_a_mono_console_pack_has_exactly_one_slot(client, library, tmp_path):
    slots = client.get("/api/overlays/duckstation/slots").json()["slots"]
    assert len(slots) == 1
    assert slots[0]["level"] == "system" and slots[0]["console"] is None


def test_a_console_bezel_can_be_removed_and_the_cascade_falls_back(client, multi, tmp_path):
    """Without a delete, a bezel dropped on the wrong console can only ever be
    replaced — the cascade would keep resolving it ahead of the system one."""
    write_png(multi / "assets" / "overlays" / "mgba.png", 1920, 1080, (420, 0, 1080, 1080))
    blob = _png_bytes(tmp_path, "up.png", 1920, 1080, (150, 0, 1620, 1080))
    client.post("/api/overlays/mgba/consoles/gba",
                files={"file": ("gba.png", blob, "image/png")})
    assert client.get("/api/overlays/resolve/mgba",
                      params={"rom": "X (USA).gba"}).json()["source"] == "console"

    assert client.delete("/api/overlays/mgba/consoles/gba").status_code == 200
    bezels.forget()
    assert client.get("/api/overlays/resolve/mgba",
                      params={"rom": "X (USA).gba"}).json()["source"] == "system"

    # Twice is a 404, not a silent success: the UI has to be able to tell
    # "removed" from "there was nothing there".
    assert client.delete("/api/overlays/mgba/consoles/gba").status_code == 404


def test_removing_a_console_the_pack_never_declared_is_a_404(client, multi):
    assert client.delete("/api/overlays/mgba/consoles/gbc").status_code == 404


def test_with_artwork_on_screen_nothing_asks_the_monitor_to_look(client, library):
    """The point of the change: with a PNG resolved, the image is drawn edge
    to edge and the hole only ever decides fallback bars nobody sees — the
    reference box spent weeks re-carrying an `mgba@1:1` correction that no
    frame ever displayed. An asset therefore turns `measure` off; delete the
    PNG and the declared frame — the thing a correction actually moves — turns
    it back on by itself."""
    png = library / "assets" / "overlays" / "duckstation.png"
    write_png(png, 1920, 1080, (240, 52, 1440, 968))
    body = client.get("/api/overlays/resolve/duckstation").json()
    assert body["source"] == "system" and body["asset"]
    assert body["measure"] is False

    png.unlink()
    bezels.forget()
    body = client.get("/api/overlays/resolve/duckstation").json()
    assert body["source"] == "declared" and body["asset"] is None
    assert body["measure"] is True
