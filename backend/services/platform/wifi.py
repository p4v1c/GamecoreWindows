"""Wi-Fi: scan, connect, disconnect, through WinRT `Windows.Devices.WiFi`.

Replaces `nmcli dev wifi`. WinRT and not `netsh wlan`: netsh prints its
answers in the Windows display language, so a parser written against English
reads nothing on the owner's French Windows. WinRT returns typed values.

Coroutines: a WinRT operation is awaited on the running loop. The adapter is
the first one Windows lists; a box has one. Windows 11 24H2 and later ask for
location access before a desktop app may scan; without it the scan is empty
(`access()` says so).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# NetworkAuthenticationType value → the label the details page shows.
_SECURITY = {0: "Open", 2: "Open", 3: "WEP", 4: "WPA Enterprise", 5: "WPA",
             7: "WPA2 Enterprise", 8: "WPA2", 10: "WPA3 Enterprise", 11: "WPA3",
             12: "OWE", 13: "WPA3 Enterprise"}
# No password to type: open, and Opportunistic Wireless Encryption.
_NO_PASSWORD = frozenset({0, 2, 12})

# WiFiConnectionStatus values.
SUCCESS = 1
INVALID_CREDENTIAL = 3
_FAILURES = {0: "Connection failed", 2: "Access to this network was revoked",
             4: "The network is out of range", 5: "Connection timed out",
             6: "This network's sign-in method is not supported"}

_WLAN_CLIENT_VERSION = 2
_ERROR_NOT_FOUND = 1168


def signal_percent(rssi_dbm: float) -> int:
    """dBm → 0-100, the way Windows' own list does: -100 dBm is 0, -50 is 100."""
    return max(0, min(100, round(2 * (rssi_dbm + 100))))


def channel_of(mhz: int) -> int:
    """The Wi-Fi channel of a centre frequency; 0 when it is not a Wi-Fi band."""
    if mhz == 2484:
        return 14
    if 2412 <= mhz < 2484:
        return (mhz - 2407) // 5
    if 5150 <= mhz < 5925:
        return (mhz - 5000) // 5
    if 5925 <= mhz <= 7125:
        return (mhz - 5950) // 5
    return 0


def describe(network, connected_ssid: str) -> dict:
    """One scanned access point as plain values (pure on its attributes)."""
    auth = int(network.security_settings.network_authentication_type)
    mhz = int(network.channel_center_frequency_in_kilohertz) // 1000
    return {
        "ssid": network.ssid,
        "signal": signal_percent(network.network_rssi_in_decibel_milliwatts),
        "secured": auth not in _NO_PASSWORD,
        "security": _SECURITY.get(auth, "Secured"),
        "frequency": mhz,
        "channel": channel_of(mhz),
        "bssid": network.bssid,
        "connected": bool(connected_ssid) and network.ssid == connected_ssid,
    }


async def _adapter():
    from winrt.windows.devices.wifi import WiFiAdapter

    adapters = await WiFiAdapter.find_all_adapters_async()
    return adapters[0] if adapters else None


async def access() -> bool:
    """Whether Windows lets this process use the Wi-Fi adapter (location, policy)."""
    from winrt.windows.devices.wifi import WiFiAccessStatus, WiFiAdapter

    return await WiFiAdapter.request_access_async() == WiFiAccessStatus.ALLOWED


async def connected_ssid(adapter=None) -> str:
    """The SSID the adapter is joined to, "" when none (or no adapter)."""
    adapter = adapter or await _adapter()
    if adapter is None:
        return ""
    profile = await adapter.network_adapter.get_connected_profile_async()
    if profile is None or not profile.is_wlan_connection_profile:
        return ""
    return profile.wlan_connection_profile_details.get_connected_ssid() or ""


async def adapter_guid() -> str:
    """The Wi-Fi adapter's interface GUID, upper case with braces, or ""."""
    adapter = await _adapter()
    if adapter is None:
        return ""
    return "{" + str(adapter.network_adapter.network_adapter_id).upper() + "}"


async def scan(rescan: bool = True) -> list[dict]:
    """Every access point in range, one row per BSSID (`describe`). [] without an adapter."""
    if not await access():
        log.warning("wifi: Windows refused access to the Wi-Fi adapter (location access?)")
        return []
    adapter = await _adapter()
    if adapter is None:
        return []
    if rescan:
        await adapter.scan_async()
    joined = await connected_ssid(adapter)
    return [describe(n, joined) for n in adapter.network_report.available_networks if n.ssid]


async def connect(ssid: str, password: str) -> tuple[bool, bool, str]:
    """Join `ssid`. Returns (ok, wrong_password, error)."""
    from winrt.windows.devices.wifi import WiFiReconnectionKind
    from winrt.windows.security.credentials import PasswordCredential

    adapter = await _adapter()
    if adapter is None:
        return False, False, "No Wi-Fi adapter"
    await adapter.scan_async()
    matches = [n for n in adapter.network_report.available_networks if n.ssid == ssid]
    if not matches:
        return False, False, "The network is out of range"
    network = max(matches, key=lambda n: n.network_rssi_in_decibel_milliwatts)
    if password:
        credential = PasswordCredential()
        credential.password = password
        result = await adapter.connect_with_password_credential_async(
            network, WiFiReconnectionKind.AUTOMATIC, credential)
    else:
        result = await adapter.connect_async(network, WiFiReconnectionKind.AUTOMATIC)
    status = int(result.connection_status)
    if status == SUCCESS:
        return True, False, ""
    if status == INVALID_CREDENTIAL:
        return False, True, "Wrong password"
    return False, False, _FAILURES.get(status, "Connection failed")


async def disconnect() -> bool:
    """Leave the current network. False when there is no adapter."""
    adapter = await _adapter()
    if adapter is None:
        return False
    adapter.disconnect()
    return True


def forget(ssid: str, interface_guid: str) -> bool:
    """Delete the saved profile named `ssid` on the Wi-Fi interface.

    Native Wifi `WlanDeleteProfile`: WinRT has no profile API, and netsh exits
    0 whether or not it found the profile. Profiles WinRT creates are named
    after the SSID; an older profile named otherwise is left alone. False when
    nothing was deleted.
    """
    import ctypes
    import uuid
    from ctypes import wintypes

    wlan = ctypes.WinDLL("wlanapi")
    handle, version = wintypes.HANDLE(), wintypes.DWORD()
    if wlan.WlanOpenHandle(_WLAN_CLIENT_VERSION, None, ctypes.byref(version),
                           ctypes.byref(handle)) != 0:
        return False
    try:
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(interface_guid).bytes_le)
        code = wlan.WlanDeleteProfile(handle, ctypes.byref(guid), ctypes.c_wchar_p(ssid), None)
    finally:
        wlan.WlanCloseHandle(handle, None)
    if code not in (0, _ERROR_NOT_FOUND):
        log.warning("wifi: could not delete the profile for %r (error %d)", ssid, code)
    return code == 0
