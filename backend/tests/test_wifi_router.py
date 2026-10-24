"""/api/settings/wifi — the endpoints that hold the PC's own connection.

**No test here may reach the real Wi-Fi.** `platform/wifi.py` and
`platform/network.py` are replaced in every test: a real disconnect takes the
machine running the suite off the network, the developer's session included.

The SSID is attacker-controlled input (anyone in radio range picks it), so the
tests check it travels to Windows verbatim and comes back verbatim.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.settings import wifi
from backend.services import wifi_radio
from backend.services.platform import network as platform_network
from backend.services.platform import wifi as platform_wifi

GUID = "{6B4D76D7-01DA-4421-9FEA-1410310E8EAB}"
WIFI_ADAPTER = {"name": "Wi-Fi", "guid": GUID, "kind": "wifi", "connected": True,
                "virtual": False, "ip": "10.0.0.7", "gateway": "10.0.0.1",
                "dns": ["10.0.0.1", "9.9.9.9"], "mac": "70:9C:D1:F2:4A:22"}


def ap(ssid, signal=50, secured=True, connected=False, security="WPA2", mhz=2412,
       channel=1):
    return {"ssid": ssid, "signal": signal, "secured": secured, "security": security,
            "frequency": mhz, "channel": channel, "bssid": "aa:bb:cc:dd:ee:ff",
            "connected": connected}


class FakeWifi:
    """What Windows would answer, and what it was asked."""

    def __init__(self):
        self.rows: list[dict] = []
        self.joined = ""
        self.adapters = [dict(WIFI_ADAPTER, connected=False)]
        self.result = (True, False, "")
        self.connects: list[tuple[str, str]] = []
        self.forgotten: list[tuple[str, str]] = []
        self.hang = False
        self.disconnect_ok = True

    async def scan(self, rescan=True):
        return list(self.rows)

    async def connected_ssid(self, adapter=None):
        return self.joined

    async def adapter_guid(self):
        return GUID

    async def connect(self, ssid, password):
        self.connects.append((ssid, password))
        if self.hang:
            await asyncio.Event().wait()
        return self.result

    async def disconnect(self):
        return self.disconnect_ok

    def forget(self, ssid, guid):
        self.forgotten.append((ssid, guid))
        return True


@pytest.fixture
def fake(monkeypatch):
    state = FakeWifi()
    for name in ("scan", "connected_ssid", "adapter_guid", "connect", "disconnect", "forget"):
        monkeypatch.setattr(platform_wifi, name, getattr(state, name))
    monkeypatch.setattr(platform_network, "adapters", lambda: state.adapters)

    async def radio():
        return True
    monkeypatch.setattr(wifi_radio, "radio_enabled", radio)
    return state


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(wifi.router, prefix="/api")
    return TestClient(app)


# ── the SSID goes to Windows as it is ───────────────────────────────────────

@pytest.mark.parametrize("ssid", [
    'Cafe "Central"', "Bob's Phone", "guest; rm -rf /", "-starts-with-a-dash",
    "Le Café $(whoami)", "back\\slash", "两个 世界", "❤️VAZ😍🔥",
])
def test_a_hostile_ssid_reaches_windows_verbatim(client, fake, ssid):
    client.post("/api/settings/wifi/connect", json={"ssid": ssid, "password": "hunter22"})
    assert fake.connects == [(ssid, "hunter22")]


def test_a_scanned_ssid_is_returned_verbatim(client, fake):
    fake.rows = [ap("café: «salon»")]
    assert client.get("/api/settings/wifi/networks").json()[0]["ssid"] == "café: «salon»"


# ── what a password attempt leaves behind ───────────────────────────────────

def test_a_password_attempt_clears_the_saved_profile_first(client, fake):
    client.post("/api/settings/wifi/connect", json={"ssid": "Home", "password": "new-key"})
    assert fake.forgotten == [("Home", GUID)]


def test_a_connect_with_no_password_leaves_saved_profiles_alone(client, fake):
    client.post("/api/settings/wifi/connect", json={"ssid": "Home"})
    assert fake.forgotten == []
    assert fake.connects == [("Home", "")]


def test_a_refused_password_is_reported_and_not_left_saved(client, fake):
    fake.result = (False, True, "Wrong password")
    body = client.post("/api/settings/wifi/connect",
                       json={"ssid": "Home", "password": "wrong"}).json()
    assert body == {"ok": False, "wrong_password": True, "error": "Wrong password"}
    assert fake.forgotten == [("Home", GUID), ("Home", GUID)]


def test_a_failure_with_no_password_keeps_the_profile(client, fake):
    fake.result = (False, False, "The network is out of range")
    body = client.post("/api/settings/wifi/connect", json={"ssid": "Home"}).json()
    assert body["ok"] is False and body["wrong_password"] is False
    assert fake.forgotten == []


def test_a_connect_that_never_answers_is_bounded(client, fake, monkeypatch):
    monkeypatch.setattr(wifi, "CONNECT_TIMEOUT", 0.05)
    fake.hang = True
    body = client.post("/api/settings/wifi/connect",
                       json={"ssid": "Home", "password": "k"}).json()
    assert body == {"ok": False, "wrong_password": False, "error": "Connection timed out"}


def test_a_successful_join_says_so(client, fake):
    assert client.post("/api/settings/wifi/connect", json={"ssid": "Home"}).json() == \
        {"ok": True, "wrong_password": False}


# ── the scan ────────────────────────────────────────────────────────────────

def test_a_scan_that_returns_nothing_is_an_empty_list(client, fake):
    assert client.get("/api/settings/wifi/networks").json() == []


def test_a_scan_windows_refuses_is_an_empty_list(client, fake, monkeypatch):
    async def refuse(rescan=True):
        raise OSError("Windows refused access to the Wi-Fi adapter")
    monkeypatch.setattr(platform_wifi, "scan", refuse)
    assert client.get("/api/settings/wifi/networks").json() == []


def test_the_connected_entry_wins_over_a_stronger_duplicate(client, fake):
    fake.rows = [ap("Mesh", signal=90), ap("Mesh", signal=40, connected=True), ap("Other", 60)]
    assert client.get("/api/settings/wifi/networks").json() == [
        {"ssid": "Mesh", "signal": 40, "secured": True, "connected": True},
        {"ssid": "Other", "signal": 60, "secured": True, "connected": False}]


def test_the_strongest_access_point_gives_the_signal(client, fake):
    fake.rows = [ap("Mesh", signal=30), ap("Mesh", signal=80)]
    assert client.get("/api/settings/wifi/networks").json()[0]["signal"] == 80


def test_an_open_network_is_reported_as_unsecured(client, fake):
    fake.rows = [ap("Cafe", secured=False, security="Open")]
    assert client.get("/api/settings/wifi/networks").json()[0]["secured"] is False


# ── details ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mhz, expected", [(2412, "2.4 GHz"), (5180, "5 GHz"),
                                           (5955, "6 GHz"), (0, "")])
def test_the_band_is_named_from_the_frequency(mhz, expected):
    assert wifi.band_of(mhz) == expected


def test_one_detail_row_per_ssid_from_its_strongest_access_point(client, fake):
    fake.rows = [ap("Mesh", signal=20, mhz=2412, channel=1),
                 ap("Mesh", signal=70, mhz=5180, channel=36)]
    assert client.get("/api/settings/wifi/details").json() == [
        {"ssid": "Mesh", "security": "WPA2", "channel": 36, "band": "5 GHz", "rate": ""}]


# ── status ──────────────────────────────────────────────────────────────────

def test_the_status_of_a_pc_connected_to_nothing_is_well_formed(client, fake):
    fake.adapters = []
    assert client.get("/api/settings/wifi/status").json() == {
        "connected": False, "ssid": "", "ip": "", "iface": "",
        "ethernet": {"connected": False, "iface": "", "ip": ""}, "radio": True,
        "gateway": "", "dns": [], "mac": ""}


def test_a_connected_pc_reports_its_link(client, fake):
    fake.joined, fake.adapters = "Home", [WIFI_ADAPTER]
    body = client.get("/api/settings/wifi/status").json()
    assert body["connected"] is True and body["ssid"] == "Home"
    assert body["ip"] == "10.0.0.7" and body["iface"] == "Wi-Fi"
    assert body["dns"] == ["10.0.0.1", "9.9.9.9"] and body["mac"] == "70:9C:D1:F2:4A:22"


def test_a_disconnected_pc_still_reports_gateway_dns_and_mac(client, fake):
    fake.adapters = [WIFI_ADAPTER]
    body = client.get("/api/settings/wifi/status").json()
    assert body["connected"] is False and body["gateway"] == "10.0.0.1"


def test_a_wired_pc_reports_its_ethernet(client, fake):
    fake.adapters = [{**WIFI_ADAPTER, "name": "Ethernet", "guid": "{X}", "kind": "ethernet",
                      "ip": "192.168.1.20"}]
    assert client.get("/api/settings/wifi/status").json()["ethernet"] == \
        {"connected": True, "iface": "Ethernet", "ip": "192.168.1.20"}


def test_a_virtual_adapter_is_not_the_wired_connection(client, fake):
    fake.adapters = [{**WIFI_ADAPTER, "name": "vEthernet (WSL)", "guid": "{X}",
                      "kind": "ethernet", "virtual": True}]
    assert client.get("/api/settings/wifi/status").json()["ethernet"]["connected"] is False


def test_a_failed_disconnect_reports_instead_of_raising(client, fake):
    fake.disconnect_ok = False
    assert client.delete("/api/settings/wifi/connect").json() == \
        {"ok": False, "error": "No Wi-Fi adapter"}


# ── the platform layer's pure parts ─────────────────────────────────────────

@pytest.mark.parametrize("dbm, percent", [(-100, 0), (-50, 100), (-70, 60), (-30, 100),
                                          (-120, 0)])
def test_signal_percent_follows_windows_scale(dbm, percent):
    assert platform_wifi.signal_percent(dbm) == percent


@pytest.mark.parametrize("mhz, channel", [(2412, 1), (2437, 6), (2484, 14), (5180, 36),
                                          (5660, 132), (5955, 1), (900, 0)])
def test_channel_of_a_frequency(mhz, channel):
    assert platform_wifi.channel_of(mhz) == channel


def test_an_access_point_is_described_from_its_winrt_attributes():
    class Network:
        ssid, bssid = "Home", "58:d0:61:0a:45:52"
        network_rssi_in_decibel_milliwatts = -64.0
        channel_center_frequency_in_kilohertz = 5660000

        class security_settings:
            network_authentication_type = 8      # RSNA_PSK
    assert platform_wifi.describe(Network, "Home") == {
        "ssid": "Home", "signal": 72, "secured": True, "security": "WPA2",
        "frequency": 5660, "channel": 132, "bssid": "58:d0:61:0a:45:52", "connected": True}


def test_adapters_are_joined_with_their_addresses():
    rows = [{"Name": "Wi-Fi", "InterfaceGuid": "{6b4d}", "NdisPhysicalMedium": 9,
             "MediaConnectState": 1, "Virtual": False, "HardwareInterface": True},
            {"Name": "VMnet8", "InterfaceGuid": "{81fe}", "NdisPhysicalMedium": 14,
             "MediaConnectState": 1, "Virtual": True, "HardwareInterface": False}]
    configs = [{"SettingID": "{6B4D}", "IPAddress": ("fe80::1", "10.0.0.7"),
                "DefaultIPGateway": ("10.0.0.1",), "DNSServerSearchOrder": None,
                "MACAddress": "70:9C:D1:F2:4A:22"}]
    wifi_row, vm = platform_network.join_adapters(rows, configs)
    assert wifi_row == {"name": "Wi-Fi", "guid": "{6B4D}", "kind": "wifi", "connected": True,
                        "virtual": False, "ip": "10.0.0.7", "gateway": "10.0.0.1",
                        "dns": [], "mac": "70:9C:D1:F2:4A:22"}
    assert vm["virtual"] is True and vm["kind"] == "ethernet" and vm["ip"] == ""


def test_the_real_adapters_read_without_changing_anything():
    assert all({"name", "guid", "kind", "connected", "virtual"} <= set(a)
               for a in platform_network.adapters())
