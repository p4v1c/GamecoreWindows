"""/api/settings/wifi/radio and the `radio` field of /status.

`platform/radios.py` is replaced in every test: a real "off" takes the machine
running the suite off the network.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.settings import wifi
from backend.services import wifi_radio
from backend.services.errors import ServiceError
from backend.services.platform import network as platform_network
from backend.services.platform import radios
from backend.services.platform import wifi as platform_wifi


class FakeRadios:
    def __init__(self):
        self.state: bool | None = True
        self.blocked = False
        self.follow = True
        self.refuse = ""
        self.hang = False
        self.calls: list[tuple[int, bool]] = []

    async def enabled(self, kind):
        if self.hang:
            await asyncio.Event().wait()
        return self.state

    async def set_enabled(self, kind, on):
        self.calls.append((kind, on))
        if self.hang:
            await asyncio.Event().wait()
        if self.blocked:
            raise radios.RadioBlocked("blocked")
        if self.refuse:
            raise OSError(self.refuse)
        if self.follow:
            self.state = on
        return self.state


@pytest.fixture
def fake(monkeypatch):
    state = FakeRadios()
    monkeypatch.setattr(radios, "enabled", state.enabled)
    monkeypatch.setattr(radios, "set_enabled", state.set_enabled)

    async def nothing(*a, **k):
        return ""
    monkeypatch.setattr(platform_wifi, "connected_ssid", nothing)
    monkeypatch.setattr(platform_wifi, "adapter_guid", nothing)
    monkeypatch.setattr(platform_network, "adapters", lambda: [])
    return state


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(wifi.router, prefix="/api")

    @app.exception_handler(ServiceError)
    async def _service_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)
    return TestClient(app)


@pytest.mark.parametrize("state, expected", [(1, True), (2, False), (3, False), (0, None)])
def test_a_radio_state_reads_as_on_off_or_unknown(state, expected):
    assert radios.read_state(state) is expected


def test_status_reports_the_radio(client, fake):
    fake.state = False
    assert client.get("/api/settings/wifi/status").json()["radio"] is False


def test_no_wifi_radio_is_null_so_no_switch_is_drawn(client, fake):
    fake.state = None
    assert client.get("/api/settings/wifi/status").json()["radio"] is None


@pytest.mark.parametrize("enabled", [True, False])
def test_set_radio_switches_the_wifi_radio_and_reads_it_back(client, fake, enabled):
    fake.state = not enabled
    assert client.post("/api/settings/wifi/radio", json={"enabled": enabled}).json() == \
        {"enabled": enabled}
    assert fake.calls == [(radios.WIFI, enabled)]


def test_a_refusal_is_reported_with_windows_sentence(client, fake):
    fake.refuse = "Windows refused to switch the radio."
    r = client.post("/api/settings/wifi/radio", json={"enabled": False})
    assert r.status_code == 502 and "refused" in r.json()["detail"]


def test_a_radio_blocked_below_windows_is_a_409(client, fake):
    fake.blocked = True
    assert client.post("/api/settings/wifi/radio", json={"enabled": True}).status_code == 409


def test_turning_on_that_does_not_take_is_an_error_not_a_silent_success(client, fake):
    fake.state, fake.follow = False, False
    assert client.post("/api/settings/wifi/radio", json={"enabled": True}).status_code == 409


def test_a_radio_that_never_answers_is_bounded(client, fake, monkeypatch):
    monkeypatch.setattr(wifi_radio, "RADIO_SECS", 0.05)
    fake.hang = True
    assert client.post("/api/settings/wifi/radio", json={"enabled": True}).status_code == 504
    assert asyncio.run(wifi_radio.radio_enabled()) is None


def test_radio_request_needs_a_boolean(client, fake):
    assert client.post("/api/settings/wifi/radio", json={"enabled": "maybe"}).status_code == 422


def test_the_real_radios_read_without_changing_anything():
    assert asyncio.run(radios.enabled(radios.WIFI)) in (True, False, None)
