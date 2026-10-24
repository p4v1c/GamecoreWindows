"""/api/settings/display — the mode, and the way back from a bad one.

**No test here may change the developer's screen.** `platform/display.py` is
replaced in every test (`fake_screen`, `fake_display.py`): run for real, this is the
module that puts a machine into a mode its monitor may refuse.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.routers.settings import display as display_router
from backend.services import display
from backend.services.errors import ServiceError
from backend.services.platform import display as platform_display
from backend.tests.fake_display import OUTPUT


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(display_router.router, prefix="/api")

    @app.exception_handler(ServiceError)
    async def _service_error(_request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)
    return TestClient(app)


def _set(client, width, height, rate):
    return client.post("/api/settings/display/mode",
                       json={"width": width, "height": height, "rate": rate})


# ── what it reads ───────────────────────────────────────────────────────────

def test_the_state_lists_the_modes_and_the_live_one(client, fake_screen):
    body = client.get("/api/settings/display").json()
    assert body["output"] == OUTPUT and body["backend"] == "windows"
    assert body["current"] == {"width": 1920, "height": 1080, "rate": 60.0}
    assert {"width": 1280, "height": 720, "rate": 50.0} in body["modes"]
    assert body["pending"] is False and body["revert_secs"] == display.REVERT_SECS


def test_a_pc_with_no_display_answers_an_empty_list_not_an_error(client, fake_screen):
    fake_screen.output = ""
    body = client.get("/api/settings/display").json()
    assert body["modes"] == [] and body["current"] is None


def test_one_mode_per_size_and_rate_whatever_the_scaling_options(fake_screen):
    raw = [(1920, 1080, 60, 32), (1920, 1080, 60, 32), (1920, 1080, 120, 32),
           (1280, 720, 60, 16), (800, 600, 1, 32)]
    assert platform_display.unique_modes(raw) == [
        {"width": 1920, "height": 1080, "rate": 120.0},
        {"width": 1920, "height": 1080, "rate": 60.0}]


# ── what it refuses ─────────────────────────────────────────────────────────

def test_a_mode_the_output_never_advertised_is_refused(client, fake_screen):
    assert _set(client, 3840, 2160, 60.0).status_code == 400
    assert fake_screen.applied == []


def test_the_mode_cannot_change_while_a_game_is_running(client, fake_screen):
    fake_screen.playing = True
    assert _set(client, 1280, 720, 60.0).status_code == 409
    assert fake_screen.applied == []


def test_asking_for_the_mode_already_on_screen_arms_nothing(client, fake_screen):
    r = _set(client, 1920, 1080, 60.0)
    assert r.status_code == 200 and r.json()["changed"] is False
    assert display._pending is None


# ── the way back ────────────────────────────────────────────────────────────

def test_applying_a_mode_arms_the_revert_for_this_session_only(client, fake_screen):
    r = _set(client, 1280, 720, 60.0)
    assert r.status_code == 200 and r.json()["changed"] is True
    assert fake_screen.applied == [(1280, 720, 60.0, False)], "an unconfirmed mode reached the registry"
    assert display._pending is not None, "nothing would bring the old mode back"


def test_confirming_disarms_it_and_writes_the_mode_to_the_registry(client, fake_screen):
    _set(client, 1280, 720, 60.0)
    assert client.post("/api/settings/display/confirm").json()["confirmed"] is True
    assert display._pending is None
    assert fake_screen.applied[-1] == (1280, 720, 60.0, True)


def test_confirming_twice_is_not_an_error(client, fake_screen):
    assert client.post("/api/settings/display/confirm").status_code == 200
    assert fake_screen.applied == []


def test_the_old_mode_comes_back_when_nobody_confirms(fake_screen):
    """Silence means the fake_screen is unreadable: the previous mode is put back."""
    previous = {"backend": "windows", "output": OUTPUT,
                "width": 1920, "height": 1080, "rate": 60.0}
    asyncio.run(display._revert_after(0.01, previous))
    assert fake_screen.changes() == [(1920, 1080, 60.0)]


def test_a_confirmation_stops_the_revert_before_it_fires(fake_screen):
    previous = {"backend": "windows", "output": OUTPUT,
                "width": 1920, "height": 1080, "rate": 60.0}

    async def scenario():
        task = asyncio.create_task(display._revert_after(5, previous))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert fake_screen.applied == []


def test_revert_now_goes_back_without_waiting(client, fake_screen):
    _set(client, 1280, 720, 60.0)
    assert client.post("/api/settings/display/revert").json() == {"ok": True, "reverted": True}
    assert fake_screen.changes()[-1] == (1920, 1080, 60.0) and display._pending is None


# ── two changes inside one confirmation window ──────────────────────────────

def test_a_refused_second_mode_leaves_the_first_its_way_back(client, fake_screen):
    """The rollback must not be given up before its replacement exists."""
    assert _set(client, 1680, 1050, 59.0).json()["changed"]
    fake_screen.refuse = {(1280, 720)}
    assert _set(client, 1280, 720, 60.0).status_code == 500
    assert display._pending is not None, "the first change lost its rollback"
    assert (display._pending["previous"]["width"], display._pending["previous"]["height"]) \
        == (1920, 1080)


def test_a_second_change_comes_back_to_the_last_confirmed_mode(client, fake_screen):
    _set(client, 1680, 1050, 59.0)
    _set(client, 1280, 720, 60.0)
    assert display._pending["previous"]["width"] == 1920


def test_a_confirmed_mode_becomes_the_one_to_come_back_to(client, fake_screen):
    _set(client, 1680, 1050, 59.0)
    client.post("/api/settings/display/confirm")
    _set(client, 1280, 720, 60.0)
    assert display._pending["previous"]["width"] == 1680


# ── the mode the player chose, kept ─────────────────────────────────────────

def test_a_confirmed_mode_is_written_down(client, fake_screen, monkeypatch, tmp_path):
    monkeypatch.setattr(display, "config_dir", lambda: tmp_path)
    _set(client, 1280, 720, 60.0)
    assert display.preferred_mode() is None, "an unconfirmed mode was persisted"
    client.post("/api/settings/display/confirm")
    assert display.preferred_mode() == {"width": 1280, "height": 720, "rate": 60.0,
                                        "output": OUTPUT}


def test_a_mode_that_was_never_confirmed_is_not_remembered(client, fake_screen, monkeypatch, tmp_path):
    monkeypatch.setattr(display, "config_dir", lambda: tmp_path)
    _set(client, 1280, 720, 60.0)
    asyncio.run(display._revert_after(0.01, display._pending["previous"]))
    assert display.preferred_mode() is None


def test_an_unreadable_preference_is_ignored_rather_than_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(display, "config_dir", lambda: tmp_path)
    (tmp_path / display.PREFERENCE_FILE).write_text("{ not json")
    assert display.preferred_mode() is None
    (tmp_path / display.PREFERENCE_FILE).write_text('{"width": "wide"}')
    assert display.preferred_mode() is None


def test_the_interface_scale_is_remembered_and_only_a_listed_size_is_taken(client):
    assert client.get("/api/settings/display/scale").json() == {
        "scale": 1.0, "choices": [0.9, 1.0, 1.25, 1.5]}
    assert client.post("/api/settings/display/scale", json={"scale": 1.25}).json()["scale"] == 1.25
    assert client.get("/api/settings/display/scale").json()["scale"] == 1.25
    assert client.post("/api/settings/display/scale", json={"scale": 3}).status_code == 400
    assert client.get("/api/settings/display/scale").json()["scale"] == 1.25
    client.post("/api/settings/display/scale", json={"scale": 1.0})


def test_the_real_display_reads_without_changing_anything():
    output = platform_display.primary_output()
    if not output:
        pytest.skip("no display attached to this machine's desktop")
    current = platform_display.current_mode(output)
    assert current is not None
    modes = platform_display.modes(output)
    assert any(m["width"] == current["width"] and m["height"] == current["height"]
               for m in modes)
