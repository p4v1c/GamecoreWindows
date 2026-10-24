"""System information — IP, storage, version, controller batteries."""
import shutil
import socket
from fastapi import APIRouter
from ..config import APP_VERSION
from ..services.paths import GAMECORE_DATA
from ..services import bios
from ..services.battery import read_batteries
from ..services.platform import network as platform_network

router = APIRouter(tags=["sysinfo"])


def _route_ip() -> str:
    """The source address of the default route (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "—"


def _primary_ip() -> str:
    """The LAN address, never a VPN's or a virtual switch's."""
    route_ip = _route_ip()
    try:
        return platform_network.pick_lan_ip(route_ip, platform_network.adapters())
    except OSError:
        return route_ip


@router.get("/sysinfo")
def get_sysinfo():
    total, used, free = shutil.disk_usage(GAMECORE_DATA)
    return {
        "ip": _primary_ip(),
        "storage_used_gb": round(used / 1e9, 1),
        "storage_total_gb": round(total / 1e9, 1),
        "storage_free_gb": round(free / 1e9, 1),
        "version": APP_VERSION,
        "controllers": read_batteries(),
        # The project has no diagnostic export; this endpoint is the nearest
        # thing to one, and a missing BIOS is the first question anyone reading
        # a support report needs answered. Two keys, no file list — the detail
        # is what /api/bios is for.
        "bios": bios.summary(),
    }
