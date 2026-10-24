"""Network adapters: which is wired, which is Wi-Fi, which is up, and their addresses.

Two WMI classes, joined on the adapter GUID: `MSFT_NetAdapter` says what the
adapter is (physical medium, connected, virtual) and
`Win32_NetworkAdapterConfiguration` carries its addresses. Replaces the
`nmcli dev show` and `con show --active` reads. Virtual adapters (Hyper-V,
WSL, VMware, VirtualBox, VPN taps) are reported with `virtual` so callers can
leave them out: their host-only addresses are not how a phone reaches the box.

Synchronous (COM): callers run it in a thread.
"""
from __future__ import annotations

from .com import wmi_query

# NDIS_PHYSICAL_MEDIUM values.
WIFI_MEDIUM = 9            # NdisPhysicalMediumNative802_11
ETHERNET_MEDIUM = 14       # NdisPhysicalMedium802_3
CONNECTED = 1              # MediaConnectStateConnected

_ADAPTER_FIELDS = ("Name", "InterfaceGuid", "NdisPhysicalMedium", "MediaConnectState",
                   "Virtual", "HardwareInterface")
_CONFIG_FIELDS = ("SettingID", "IPAddress", "DefaultIPGateway", "DNSServerSearchOrder",
                  "MACAddress")


def _ipv4(values) -> list[str]:
    return [v for v in (values or ()) if v and "." in v and ":" not in v]


def _kind(medium) -> str:
    if medium == WIFI_MEDIUM:
        return "wifi"
    if medium == ETHERNET_MEDIUM:
        return "ethernet"
    return "other"


def join_adapters(adapters: list[dict], configs: list[dict]) -> list[dict]:
    """One dict per adapter: name, guid, kind, connected, virtual, ip, gateway, dns, mac.

    Pure. A field Windows does not answer is "" (or [] for `dns`), never None,
    so the status page omits the row instead of printing a blank one.
    """
    by_guid = {str(c.get("SettingID") or "").upper(): c for c in configs}
    out = []
    for row in adapters:
        guid = str(row.get("InterfaceGuid") or "").upper()
        config = by_guid.get(guid, {})
        ips = _ipv4(config.get("IPAddress"))
        gateways = _ipv4(config.get("DefaultIPGateway"))
        out.append({
            "name": str(row.get("Name") or ""),
            "guid": guid,
            "kind": _kind(row.get("NdisPhysicalMedium")),
            "connected": row.get("MediaConnectState") == CONNECTED,
            "virtual": bool(row.get("Virtual")) or not row.get("HardwareInterface", True),
            "ip": ips[0] if ips else "",
            "gateway": gateways[0] if gateways else "",
            "dns": [d for d in (config.get("DNSServerSearchOrder") or ()) if d],
            "mac": str(config.get("MACAddress") or ""),
        })
    return out


def adapters() -> list[dict]:
    """Every network adapter Windows knows (`join_adapters`). Raises OSError."""
    rows = wmi_query(r"root\StandardCimv2", "SELECT * FROM MSFT_NetAdapter", _ADAPTER_FIELDS)
    configs = wmi_query(
        r"root\cimv2",
        "SELECT SettingID, IPAddress, DefaultIPGateway, DNSServerSearchOrder, MACAddress "
        "FROM Win32_NetworkAdapterConfiguration WHERE IPEnabled = TRUE",
        _CONFIG_FIELDS)
    return join_adapters(rows, configs)


def pick_lan_ip(route_ip: str, adapters: list[dict]) -> str:
    """The address a phone on the LAN reaches the PC by. Pure.

    `route_ip` is the source address of the default route; it is kept unless
    it belongs to a virtual adapter (a VPN or Hyper-V route), in which case the
    first connected physical adapter wins, wired before Wi-Fi.
    """
    physical = [a for a in adapters if a["connected"] and not a["virtual"] and a["ip"]]
    if not physical or any(a["ip"] == route_ip for a in physical):
        return route_ip
    physical.sort(key=lambda a: (a["kind"] != "ethernet", not a["gateway"]))
    return physical[0]["ip"]
