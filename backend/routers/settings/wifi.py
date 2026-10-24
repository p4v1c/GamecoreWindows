"""Wi-Fi: scan, join, leave, radio. The WinRT and WMI calls are in services/platform/."""
import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ...services import wifi_radio
from ...services.platform import network as platform_network
from ...services.platform import wifi as platform_wifi

router = APIRouter(prefix="/settings/wifi", tags=["wifi"])
log = logging.getLogger(__name__)

# Long enough for a join that is merely slow (a 5 GHz association plus DHCP on
# a busy access point runs past 30 s), short enough that the screen is not left
# saying nothing.
CONNECT_TIMEOUT = 45.0

_NO_LINK = {"gateway": "", "dns": [], "mac": ""}


def best_per_ssid(rows: list[dict]) -> list[dict]:
    """One entry per SSID, connected first then strongest; the shape `/networks` serves.

    A scan lists one row per access point, so a mesh on one SSID appears
    several times: the connected one wins, otherwise the strongest signal.
    """
    best: dict[str, dict] = {}
    for row in rows:
        ssid = row["ssid"]
        entry = {"ssid": ssid, "signal": row["signal"], "secured": row["secured"],
                 "connected": row["connected"]}
        existing = best.get(ssid)
        if existing is None or (row["connected"] and not existing["connected"]):
            best[ssid] = entry
        elif not existing["connected"] and row["signal"] > existing["signal"]:
            existing["signal"] = row["signal"]
    networks = list(best.values())
    networks.sort(key=lambda n: (not n["connected"], -n["signal"]))
    return networks


def band_of(mhz: int) -> str:
    """`5180` → `5 GHz`. Empty when the frequency is not a Wi-Fi band."""
    if mhz >= 5925:
        return "6 GHz"
    if mhz >= 4900:
        return "5 GHz"
    if mhz >= 2400:
        return "2.4 GHz"
    return ""


def details_per_ssid(rows: list[dict]) -> list[dict]:
    """Security, channel, band per SSID, from its strongest access point.

    Kept apart from `/networks`: that endpoint's shape is what the default UI
    and two shipped themes read. `rate` stays in the shape and is "": WinRT
    reports no per-network link rate.
    """
    out: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: -r["signal"]):
        out.setdefault(row["ssid"], {
            "ssid": row["ssid"], "security": row["security"], "channel": row["channel"],
            "band": band_of(row["frequency"]), "rate": "",
        })
    return list(out.values())


async def _scan(rescan: bool) -> list[dict]:
    try:
        return await platform_wifi.scan(rescan)
    except OSError as e:
        log.warning("wifi: scan failed: %s", e)
        return []


@router.get("/networks")
async def scan_networks():
    return best_per_ssid(await _scan(rescan=True))


@router.get("/details")
async def wifi_details():
    """The radio detail behind each SSID: security, channel, band. Additive to `/networks`."""
    return details_per_ssid(await _scan(rescan=False))


async def _adapters() -> list[dict]:
    try:
        return await asyncio.to_thread(platform_network.adapters)
    except OSError as e:
        log.warning("wifi: adapters unreadable: %s", e)
        return []


def ethernet_status(adapters: list[dict]) -> dict:
    """The first connected physical wired adapter: lets the UI skip the Wi-Fi list."""
    for a in adapters:
        if a["kind"] == "ethernet" and a["connected"] and not a["virtual"]:
            return {"connected": True, "iface": a["name"], "ip": a["ip"]}
    return {"connected": False, "iface": "", "ip": ""}


@router.get("/status")
async def wifi_status():
    """Connected SSID and IP, wired status, the radio, and the link's gateway, DNS, MAC.

    The same keys whether connected or not: a screen reading `body.gateway`
    must not render `undefined` because the box was offline when it asked.
    `radio` is true/false, or null on a PC with no Wi-Fi to switch.
    """
    adapters = await _adapters()
    ethernet = ethernet_status(adapters)
    radio = await wifi_radio.radio_enabled()
    try:
        ssid, guid = await platform_wifi.connected_ssid(), await platform_wifi.adapter_guid()
    except OSError as e:
        log.warning("wifi: status unreadable: %s", e)
        ssid, guid = "", ""
    link = next((a for a in adapters if a["guid"] == guid), None) if guid else None
    info = {k: link[k] for k in _NO_LINK} if link else dict(_NO_LINK)
    if ssid:
        return {"connected": True, "ssid": ssid, "ip": link["ip"] if link else "",
                "iface": link["name"] if link else "", "ethernet": ethernet,
                "radio": radio, **info}
    return {"connected": False, "ssid": "", "ip": "", "iface": "", "ethernet": ethernet,
            "radio": radio, **info}


class RadioRequest(BaseModel):
    enabled: bool


@router.post("/radio")
async def set_radio(req: RadioRequest):
    """Turn the Wi-Fi radio on or off. Logic: services/wifi_radio.py."""
    return await wifi_radio.set_radio(req.enabled)


class ConnectRequest(BaseModel):
    ssid: str
    password: str = ""


async def _forget(ssid: str) -> None:
    """Drop the saved profile: its key is the one just replaced, or just refused.

    A saved profile with an old key would be retried instead of the new one;
    only called when the player typed a password, i.e. said what the key is.
    """
    try:
        guid = await platform_wifi.adapter_guid()
    except OSError:
        return
    if guid:
        await asyncio.to_thread(platform_wifi.forget, ssid, guid)


@router.post("/connect")
async def connect_wifi(req: ConnectRequest):
    if req.password:
        await _forget(req.ssid)
    try:
        ok, wrong_pass, error = await asyncio.wait_for(
            platform_wifi.connect(req.ssid, req.password), CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        if req.password:
            await _forget(req.ssid)
        log.warning("wifi: connect to %r timed out after %ss", req.ssid, CONNECT_TIMEOUT)
        return {"ok": False, "wrong_password": False, "error": "Connection timed out"}
    except OSError as e:
        ok, wrong_pass, error = False, False, str(e) or "Connection failed"
    if not ok:
        if req.password:
            await _forget(req.ssid)
        # Answered 200 with `ok: false` (the shape the UI parses), so a failed
        # join is invisible in the logs unless written here.
        log.warning("wifi: connect to %r failed: %s", req.ssid, error)
        return {"ok": False, "wrong_password": wrong_pass, "error": error}
    log.info("wifi: connected to %r", req.ssid)
    return {"ok": True, "wrong_password": False}


@router.delete("/connect")
async def disconnect_wifi():
    try:
        ok = await platform_wifi.disconnect()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": ok, "error": "" if ok else "No Wi-Fi adapter"}
