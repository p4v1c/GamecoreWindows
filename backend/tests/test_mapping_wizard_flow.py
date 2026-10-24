"""The mapping wizard, end to end: capture → SDL mapping line → autoconfig.

Each link has its own tests — the capture session in test_controller_capture.py,
the database in test_mapping_db.py, the derivation in
test_configgen_snapshots.py. None of them walks the whole way, and the whole
way is what the owner sees: they map an unknown pad from the couch, and the
next time it connects the 3DS emulator has to answer to it.

That chain used to sit next to "Scan mapping" and share words with it
("capture", "mapping", "forget"). Removing that action is exactly the kind of
change that could cut a link here without any single-link test noticing, so
this runs the real modules in order: `controller_capture.start/commit`,
`mapping_db`, then `apply_profile` dispatching to the real azahar generator.

No pad is touched. The device layer — which pads are plugged in, their
/dev/input nodes, the GUIDs SDL computes — is stubbed, the same seams the
capture tests use, and the emulators write into a throwaway HOME built from the
catalogue's own seeds by the characterisation harness.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import configgen                          # noqa: E402
from backend.services import controller_capture as cap           # noqa: E402
from backend.services import controller_profiles as cp           # noqa: E402
from backend.services.catalog import load_catalog                # noqa: E402
from backend.services.configgen import controllers as cc         # noqa: E402
from backend.services.configgen import derive, mapping_db        # noqa: E402
from backend.tests import characterisation as ch                 # noqa: E402

# A pad nothing on the box knows: vendor dead, product beef. The GUID is the
# one SDL computes for it, and it encodes that vendor:product — the snapshot
# guard decodes it, so a GUID for any other pair would be refused on sight.
VENDOR, PRODUCT, EVDEV = "dead", "beef", "Generic USB Gamepad"
GUID = "03000325adde0000efbe000011010000"

# What the owner pressed, step by step, as the wizard records it.
PRESSED = {
    "a": "b0", "b": "b1", "x": "b2", "y": "b3",
    "dpup": "h0.1", "dpdown": "h0.4", "dpleft": "h0.8", "dpright": "h0.2",
    "leftshoulder": "b4", "rightshoulder": "b5",
    "back": "b6", "start": "b7",
    "leftx": "a0", "lefty": "a1",
}


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fresh install with an azahar config, and one unknown pad plugged in."""
    home = tmp_path / "home"
    ch.build_tree(home)
    ch.install_stubs(cp, home, monkeypatch)

    # The wizard's database, away from the real one.
    monkeypatch.setattr(mapping_db, "USER_DB", tmp_path / "user.txt")
    monkeypatch.setattr(mapping_db, "SERVED_DB", tmp_path / "served.txt")
    monkeypatch.setattr(mapping_db, "DB_FILE", tmp_path / "community.txt")
    (tmp_path / "community.txt").write_text("# nothing\n")

    # The device layer the wizard reads.
    monkeypatch.setattr(cap, "detect_pads", lambda *a, **k: [(VENDOR, PRODUCT, EVDEV)])
    layout = cap.sdl_layout(8, 0, 0)
    monkeypatch.setattr(cap, "_pad_device",
                        lambda v, p: (f"{v}:{p}", {"sdl9": layout}, {"sdl9": ()}))
    monkeypatch.setattr(cap, "sdl_guids", lambda *a, **k: [GUID])

    # What the emulators' SDL says about it, once it is connected: the same
    # GUID, and read through evdev — the case a capture is valid for.
    monkeypatch.setattr(cc, "sdl2_probe", lambda v, p, lib="": {"guid": GUID})
    monkeypatch.setattr(derive, "evdev_driven", lambda v, p: True)

    azahar = load_catalog()["azahar"]
    target = configgen.generator_opts(azahar, home, home / "snapshots")["target"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[Controls]\nprofile=0\nsomethingElse=1\n")

    cap.cancel()
    yield target
    cap.cancel()


def _map_the_pad() -> dict:
    started = cap.start()
    assert started["ok"] is True, started
    return cap.commit(PRESSED, "My Pad")


def test_a_mapped_pad_is_configured_on_its_next_connection(box):
    committed = _map_the_pad()

    assert committed["ok"] is True, committed
    assert committed["missing"] == []
    [line] = mapping_db.read_user()
    assert line.startswith(f"{GUID},My Pad,") and "a:b0" in line and "dpup:h0.1" in line
    assert GUID in mapping_db.served().read_text(), "the line never reached SDL's file"

    result = cp.apply_profile(1, VENDOR, PRODUCT, EVDEV)

    written = box.read_text()
    assert any(m.startswith("azahar") and "built for" in m for m in result), list(result)
    assert f"guid:{GUID}" in written, written
    assert "somethingElse=1" in written, "the rest of the owner's file must survive"


def test_the_same_connection_twice_writes_nothing_the_second_time(box):
    _map_the_pad()
    cp.apply_profile(1, VENDOR, PRODUCT, EVDEV)
    first = box.read_text()

    again = cp.apply_profile(1, VENDOR, PRODUCT, EVDEV)

    assert box.read_text() == first
    assert not any(m.startswith("azahar") for m in again), list(again)


def test_an_emulator_taken_over_by_hand_is_left_alone(box):
    """The per-emulator exception outranks a fresh capture. Somebody who set
    the 3DS up themselves must not have it rewritten because they later mapped
    a pad in the wizard for everything else."""
    _map_the_pad()
    cp.set_autoconfig(False, "azahar")
    before = box.read_text()

    result = cp.apply_profile(1, VENDOR, PRODUCT, EVDEV)

    assert box.read_text() == before
    assert "Nintendo 3DS" in result.off_labels, result.off_labels

    # And handed back, it is written again.
    cp.set_autoconfig(True, "azahar")
    cp.apply_profile(1, VENDOR, PRODUCT, EVDEV)
    assert f"guid:{GUID}" in box.read_text()


def test_forgetting_a_capture_stops_it_being_used(box):
    """The wizard's own forget — `/controllers/mapping/forget` — is what
    remains for undoing a wrong capture, and it has to reach autoconfig too."""
    _map_the_pad()
    assert mapping_db.remove(GUID) is True
    assert GUID not in (mapping_db.served().read_text() if mapping_db.served() else "")

    cp.apply_profile(1, VENDOR, PRODUCT, EVDEV)

    assert f"guid:{GUID}" not in box.read_text()
