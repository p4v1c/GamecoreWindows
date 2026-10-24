"""/api/settings/audio — outputs and volume over Core Audio.

The router is driven with a fake `platform/audio.py`: nothing here changes this
PC's sound. One test reads the real endpoints, which a CI runner may not have.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.settings import audio
from backend.services.platform import audio as platform_audio

SPEAKERS = "{0.0.0.00000000}.{14fb75af-fe3b-40c9-822a-548347055f38}"
HDMI = "{0.0.0.00000000}.{d1710a0a-80d4-4fb5-9d99-58e6b1581659}"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(audio.router, prefix="/api")
    return TestClient(app)


def test_the_outputs_are_listed_with_the_default_marked(client, monkeypatch):
    outputs = [{"id": SPEAKERS, "name": "Speakers (Realtek(R) Audio)", "default": False},
               {"id": HDMI, "name": "LG TV (HDMI)", "default": True}]
    monkeypatch.setattr(platform_audio, "list_outputs", lambda: outputs)
    assert client.get("/api/settings/audio/sinks").json() == outputs


def test_no_audio_service_is_an_empty_list_not_an_error(client, monkeypatch):
    def refuse():
        raise OSError("Core Audio refused")
    monkeypatch.setattr(platform_audio, "list_outputs", refuse)
    assert client.get("/api/settings/audio/sinks").json() == []


def test_the_volume_and_mute_are_read_from_the_default_output(client, monkeypatch):
    monkeypatch.setattr(platform_audio, "get_volume", lambda: (37, True))
    assert client.get("/api/settings/audio").json() == {"volume": 37, "muted": True}


def test_an_unreadable_volume_answers_the_old_default(client, monkeypatch):
    def refuse():
        raise OSError("no output")
    monkeypatch.setattr(platform_audio, "get_volume", refuse)
    assert client.get("/api/settings/audio").json() == {"volume": 100, "muted": False}


def test_the_volume_is_clamped_before_it_reaches_windows(client, monkeypatch):
    seen = []
    monkeypatch.setattr(platform_audio, "set_volume", seen.append)
    assert client.post("/api/settings/audio/volume", json={"volume": 140}).json() == \
        {"ok": True, "volume": 100}
    client.post("/api/settings/audio/volume", json={"volume": -5})
    assert seen == [100, 0]


def test_a_refused_volume_says_why(client, monkeypatch):
    def refuse(_):
        raise OSError("No audio output.")
    monkeypatch.setattr(platform_audio, "set_volume", refuse)
    assert client.post("/api/settings/audio/volume", json={"volume": 50}).json() == \
        {"ok": False, "error": "No audio output."}


def test_picking_an_output_passes_its_endpoint_id(client, monkeypatch):
    seen = []
    monkeypatch.setattr(platform_audio, "set_default_output", seen.append)
    assert client.post("/api/settings/audio/sink", json={"sink": HDMI}).json() == {"ok": True}
    assert seen == [HDMI]


def test_an_output_windows_refuses_is_reported(client, monkeypatch):
    def refuse(_):
        raise OSError("Core Audio refused: Paramètre incorrect.")
    monkeypatch.setattr(platform_audio, "set_default_output", refuse)
    body = client.post("/api/settings/audio/sink", json={"sink": "bogus"}).json()
    assert body["ok"] is False and "refused" in body["error"]


def test_the_real_endpoints_read_without_changing_anything():
    try:
        outputs = platform_audio.list_outputs()
        volume, muted = platform_audio.get_volume()
    except OSError:
        pytest.skip("no audio service on this machine")
    assert all(set(o) == {"id", "name", "default"} for o in outputs)
    assert sum(o["default"] for o in outputs) <= 1
    assert 0 <= volume <= 100 and isinstance(muted, bool)
