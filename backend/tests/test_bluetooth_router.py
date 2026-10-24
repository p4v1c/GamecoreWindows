"""/api/settings/bluetooth — paired list, scan, pair, forget.

**No test here may reach the real adapter.** `platform/bluetooth.py` is
replaced in every test: a real scan puts the developer's adapter into discovery
for ten seconds, and a real unpair forgets their headphones. Two tests read the
real paired list and the pure address formatting.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.settings import bluetooth
from backend.services.platform import bluetooth as platform_bluetooth

PAD = "40:1B:5F:B9:EA:8D"
JBL = "0C:A6:94:AD:5C:5E"


def dev(mac, name, connected=False, paired=True):
    return {"mac": mac, "name": name, "connected": connected, "paired": paired, "id": f"id-{mac}"}


class FakeBluetooth:
    def __init__(self):
        self.paired = [dev(PAD, "Wireless Controller", connected=True), dev(JBL, "JBL Charge 2")]
        self.found: list[dict] = []
        self.pair_result = (True, "Paired")
        self.unpair_result = (True, "Unpaired")
        self.calls: list[tuple] = []
        self.hang = False
        self.forget_works = True

    async def paired_devices(self):
        return list(self.paired)

    async def scan(self, seconds):
        self.calls.append(("scan", seconds))
        return list(self.found)

    async def pair(self, mac):
        self.calls.append(("pair", mac))
        if self.hang:
            await asyncio.Event().wait()
        if self.pair_result[0]:
            self.paired.append(dev(mac, "New pad"))
        return self.pair_result

    async def unpair(self, mac):
        self.calls.append(("unpair", mac))
        if self.forget_works:
            self.paired = [d for d in self.paired if d["mac"] != mac]
        return self.unpair_result


@pytest.fixture
def fake(monkeypatch):
    state = FakeBluetooth()
    for name in ("paired_devices", "scan", "pair", "unpair"):
        monkeypatch.setattr(platform_bluetooth, name, getattr(state, name))
    return state


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(bluetooth.router, prefix="/api")
    return TestClient(app)


def test_the_paired_list_carries_each_device_and_its_connected_state(client, fake):
    assert client.get("/api/settings/bluetooth/devices").json() == [
        {"mac": JBL, "name": "JBL Charge 2", "connected": False, "paired": True},
        {"mac": PAD, "name": "Wireless Controller", "connected": True, "paired": True}]


def test_nothing_paired_is_an_empty_list_not_an_error(client, fake):
    fake.paired = []
    assert client.get("/api/settings/bluetooth/devices").json() == []


def test_an_adapter_windows_refuses_is_an_empty_list(client, fake, monkeypatch):
    async def refuse():
        raise OSError("no radio")
    monkeypatch.setattr(platform_bluetooth, "paired_devices", refuse)
    assert client.get("/api/settings/bluetooth/devices").json() == []


def test_named_devices_come_before_bare_addresses(client, fake):
    fake.found = [dev("AA:BB:CC:DD:EE:01", "", paired=False),
                  dev("AA:BB:CC:DD:EE:02", "Xbox Wireless Controller", paired=False)]
    body = client.post("/api/settings/bluetooth/scan").json()
    assert [d["name"] for d in body["found"]] == ["Xbox Wireless Controller",
                                                 "AA:BB:CC:DD:EE:01"]
    assert body["seconds"] == bluetooth.SCAN_SECS and body["ok"] is True
    assert all(d["paired"] is False and d["connected"] is False for d in body["found"])


def test_the_scan_runs_for_the_seconds_the_ui_announces(client, fake):
    client.post("/api/settings/bluetooth/scan")
    assert fake.calls == [("scan", bluetooth.SCAN_SECS)]


def test_pairing_reports_a_device_not_yet_connected(client, fake):
    body = client.post("/api/settings/bluetooth/pair", json={"mac": "aa:bb:cc:dd:ee:03"}).json()
    assert body["ok"] is True and "Switch the device on" in body["message"]
    assert fake.calls == [("pair", "AA:BB:CC:DD:EE:03")]


def test_a_pin_device_is_refused_with_the_reason(client, fake):
    fake.pair_result = (False, "This device asks for a PIN, which the box cannot type")
    body = client.post("/api/settings/bluetooth/pair", json={"mac": PAD}).json()
    assert body == {"ok": False, "message": "This device asks for a PIN, which the box cannot type"}


def test_a_pairing_that_never_answers_is_bounded(client, fake, monkeypatch):
    monkeypatch.setattr(bluetooth, "PAIR_SECS", 0.05)
    fake.hang = True
    body = client.post("/api/settings/bluetooth/pair", json={"mac": PAD}).json()
    assert body["ok"] is False and "in time" in body["message"]


@pytest.mark.parametrize("bad", ["not-a-mac", "AA:BB:CC:DD:EE:FF\nremove", "--help", ""])
def test_anything_but_an_address_never_reaches_windows(client, fake, bad):
    assert client.post("/api/settings/bluetooth/pair", json={"mac": bad}).status_code == 400
    assert fake.calls == []


def test_forgetting_unpairs_and_proves_it_went(client, fake):
    body = client.delete(f"/api/settings/bluetooth/devices/{JBL}").json()
    assert body["ok"] is True and fake.calls == [("unpair", JBL)]


def test_a_device_still_paired_afterwards_was_not_forgotten(client, fake):
    fake.forget_works = False
    body = client.delete(f"/api/settings/bluetooth/devices/{JBL}").json()
    assert body == {"ok": False, "message": "The device is still paired"}


def test_a_lower_case_address_is_the_same_device(client, fake):
    client.delete(f"/api/settings/bluetooth/devices/{JBL.lower()}")
    assert fake.calls == [("unpair", JBL)]


def test_connect_reports_a_connected_device_and_explains_the_rest(client, fake):
    assert client.post("/api/settings/bluetooth/connect", json={"mac": PAD}).json() == \
        {"ok": True, "message": "Connected"}
    body = client.post("/api/settings/bluetooth/connect", json={"mac": JBL}).json()
    assert body["ok"] is False and "by itself" in body["message"]


def test_disconnect_is_ok_only_when_the_link_is_already_down(client, fake):
    assert client.post("/api/settings/bluetooth/disconnect", json={"mac": JBL}).json() == {"ok": True}
    assert client.post("/api/settings/bluetooth/disconnect", json={"mac": PAD}).json() == {"ok": False}


@pytest.mark.parametrize("raw, mac", [(0x8CDE52ADD7E4, "8C:DE:52:AD:D7:E4"),
                                      ("8c:de:52:ad:d7:e4", "8C:DE:52:AD:D7:E4"),
                                      (0x13080504, "00:00:13:08:05:04"), ("junk", "")])
def test_an_address_is_written_the_way_the_interface_reads_it(raw, mac):
    assert platform_bluetooth.format_address(raw) == mac


def test_the_real_paired_list_reads_without_changing_anything():
    try:
        devices = asyncio.run(platform_bluetooth.paired_devices())
    except OSError:
        pytest.skip("no Bluetooth on this machine")
    assert all(d["paired"] and len(d["mac"]) == 17 for d in devices)
