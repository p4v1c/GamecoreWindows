"""`allow-bluetooth-wake`: the radio and Bluetooth HID devices may wake the PC, noted for uninstall.

WMI is a fake that notes what it is asked (backend/tests/powershell_harness.py).
"""
from __future__ import annotations

from backend.tests.powershell_harness import calls, needs_powershell, run_ps

pytestmark = needs_powershell

RADIO = r"USB\VID_8087&PID_0AAA\5&150EE2AD&0&14"
PAD_HID = r"BTHENUM\{00001124-0000-1000-8000-00805F9B34FB}_VID&0002054C_PID&09CC\7&253C164E&0&401B5FB9EA8D_C00000000"
NIC = r"PCI\VEN_10EC&DEV_8168\4&1"


def wmi_fakes(rows: list[tuple[str, str, bool]], readable: bool = True) -> str:
    """MSPower_DeviceWakeEnable rows (instance, PnP class, enabled) and Set-CimInstance."""
    wake = ",".join(f"[pscustomobject]@{{ InstanceName = '{i}_0'; Enable = ${str(e).lower()} }}"
                    for i, _c, e in rows)
    pnp = ",".join(f"[pscustomobject]@{{ DeviceID = '{i}'; PNPClass = '{c}' }}" for i, c, _e in rows)
    refuse = "" if readable else "if ($Namespace) { throw 'Access denied' }"
    return rf"""
        function Get-CimInstance {{ param($Namespace, $ClassName)
            {refuse}
            if ($ClassName -eq 'Win32_PnPEntity') {{ @({pnp}) }} else {{ @({wake}) }} }}
        function Set-CimInstance {{ param($InputObject, $Property)
            Note 'wake' "$($InputObject.InstanceName)=$($Property.Enable)" }}
    """


def test_the_radio_and_bluetooth_hid_are_allowed_and_nothing_else(tmp_path):
    rows = [(RADIO, "Bluetooth", False), (PAD_HID, "HIDClass", False), (NIC, "Net", False)]
    result = run_ps(tmp_path, wmi_fakes(rows) + "Install-GcBluetoothWake")
    assert sorted(calls(result, "wake")) == sorted([f"{RADIO}_0=True", f"{PAD_HID}_0=True"])
    assert result["manifest"]["allow-bluetooth-wake"] == {f"{RADIO}_0": False, f"{PAD_HID}_0": False}


def test_a_device_already_allowed_is_left_and_not_noted(tmp_path):
    result = run_ps(tmp_path, wmi_fakes([(RADIO, "Bluetooth", True)]) + "Install-GcBluetoothWake")
    assert calls(result, "wake") == []
    assert "allow-bluetooth-wake" not in result["manifest"]


def test_unreadable_wake_settings_change_nothing_and_do_not_fail(tmp_path):
    result = run_ps(tmp_path, wmi_fakes([(RADIO, "Bluetooth", False)], readable=False)
                    + "Install-GcBluetoothWake")
    assert calls(result, "wake") == [] and result["changes"] == []


def test_a_dry_run_changes_nothing(tmp_path):
    result = run_ps(tmp_path, wmi_fakes([(RADIO, "Bluetooth", False)]) + "Install-GcBluetoothWake",
                    dry_run=True)
    assert calls(result, "wake") == []
    assert result["changes"] == [f"allow {RADIO} to wake the PC"]


def test_uninstall_forbids_again_what_the_install_allowed(tmp_path):
    record = f"@{{ '{RADIO}_0' = $false }}"
    result = run_ps(tmp_path, wmi_fakes([(RADIO, "Bluetooth", True)])
                    + f"Uninstall-GcBluetoothWake {record}")
    assert calls(result, "wake") == [f"{RADIO}_0=False"]
