"""Which devices may wake the PC: Windows' "Allow this device to wake the computer".

Replaces sysfs `power/wakeup` (the Linux `gamecore-wake-setup` helper) and
BlueZ's `WakeAllowed`. WMI `root\\wmi` lists one `MSPower_DeviceWakeEnable`
per device able to wake the PC, named by its PnP instance id plus `_0`.
Reading and writing it need an elevated process, which the backend is (dev-log
D8); unelevated, WMI lists nothing. Synchronous (COM).
"""
from __future__ import annotations

from .com import ensure_com, wmi_query

_NAMESPACE = r"root\wmi"
_WAKE_QUERY = "SELECT InstanceName, Enable FROM MSPower_DeviceWakeEnable"
_PNP_QUERY = "SELECT DeviceID, PNPClass FROM Win32_PnPEntity"


def instance_id(wmi_name: str) -> str:
    """The PnP instance id under a WMI instance name (`..._0`). Pure."""
    head, sep, tail = wmi_name.rpartition("_")
    return head if sep and tail.isdigit() else wmi_name


def wake_devices() -> list[dict]:
    """[{instance, wmi_name, enabled, pnp_class}] of every device able to wake the PC.

    Raises OSError when WMI refuses.
    """
    rows = wmi_query(_NAMESPACE, _WAKE_QUERY, ("InstanceName", "Enable"))
    if not rows:
        return []
    classes = {str(r["DeviceID"] or "").upper(): str(r["PNPClass"] or "")
               for r in wmi_query(r"root\cimv2", _PNP_QUERY, ("DeviceID", "PNPClass"))}
    out = []
    for row in rows:
        name = str(row["InstanceName"] or "")
        instance = instance_id(name)
        out.append({"instance": instance, "wmi_name": name, "enabled": bool(row["Enable"]),
                    "pnp_class": classes.get(instance.upper(), "")})
    return out


def set_wake(wmi_name: str, allowed: bool) -> None:
    """Allow or forbid one device (`wmi_name` from `wake_devices`) to wake the PC.

    Raises OSError when WMI refuses (not elevated, device gone).
    """
    import pywintypes
    import win32com.client

    ensure_com()
    escaped = wmi_name.replace("\\", "\\\\").replace("'", "\\'")
    try:
        service = win32com.client.GetObject(f"winmgmts:\\\\.\\{_NAMESPACE}")
        found = list(service.ExecQuery(
            f"SELECT * FROM MSPower_DeviceWakeEnable WHERE InstanceName = '{escaped}'"))
        if not found:
            raise OSError(f"no wake setting for {wmi_name}")
        for row in found:
            row.Enable = bool(allowed)
            row.Put_()
    except pywintypes.com_error as e:
        raise OSError(f"WMI refused the wake setting of {wmi_name}: {e}") from e
