"""External disks: which drive letters are removable, and safe removal.

Replaces `lsblk` and `udisksctl`. Windows mounts a disk itself and gives it a
drive letter, so "plugged in" and "mounted" are the same state here; a disk
without a letter is not listed. Removable = Windows says so (a USB stick, an
SD card) or the disk sits on a USB, SD, MMC or FireWire bus (a USB hard drive
reports itself as fixed). The system drive is never listed.

`eject()` is Windows' "Safely remove": a PnP eject request on the disk's
parent device, which flushes and dismounts, and is vetoed while a process
holds a file open on it.
"""
from __future__ import annotations

import ctypes
import os
import struct
import uuid
from ctypes import wintypes

_DRIVE_REMOVABLE, _DRIVE_FIXED = 2, 3
# STORAGE_BUS_TYPE values that mean "can be unplugged".
_EXTERNAL_BUSES = frozenset({4, 7, 12, 13})        # 1394, USB, SD, MMC
_IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
_IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
_BUS_TYPE_OFFSET = 28                              # in STORAGE_DEVICE_DESCRIPTOR
_OPEN_EXISTING = 3
_FILE_SHARE_READ_WRITE = 0x1 | 0x2
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_GUID_DEVINTERFACE_DISK = "53f56307-b6bf-11d0-94f2-00a0c91efb8b"
_DIGCF_PRESENT, _DIGCF_DEVICEINTERFACE = 0x2, 0x10
_CR_SUCCESS = 0
_VETO_REASONS = {6: "a program is using it", 5: "Windows is using it",
                 4: "a driver refused", 1: "Windows refused"}


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # A HANDLE is pointer-sized: the default int return would truncate it.
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _open(path: str):
    handle = _kernel32().CreateFileW(path, 0, _FILE_SHARE_READ_WRITE, None, _OPEN_EXISTING, 0,
                                     None)
    return None if handle in (None, _INVALID_HANDLE) else handle


def _open_volume(letter: str):
    return _open(f"\\\\.\\{letter}")


def _ioctl(handle, code: int, in_buffer: bytes, out_size: int) -> bytes | None:
    out = ctypes.create_string_buffer(out_size)
    returned = wintypes.DWORD()
    source = ctypes.create_string_buffer(in_buffer, len(in_buffer)) if in_buffer else None
    ok = _kernel32().DeviceIoControl(handle, code, source, len(in_buffer), out, out_size,
                                     ctypes.byref(returned), None)
    return out.raw[:returned.value] if ok else None


def bus_type(letter: str) -> int:
    """STORAGE_BUS_TYPE of the disk behind `letter`; 0 when Windows will not say."""
    handle = _open_volume(letter)
    if handle is None:
        return 0
    try:
        query = struct.pack("<II4x", 0, 0)         # StorageDeviceProperty, standard query
        raw = _ioctl(handle, _IOCTL_STORAGE_QUERY_PROPERTY, query, 1024)
    finally:
        _kernel32().CloseHandle(handle)
    if not raw or len(raw) < _BUS_TYPE_OFFSET + 4:
        return 0
    return struct.unpack_from("<I", raw, _BUS_TYPE_OFFSET)[0]


def is_external(drive_type: int, bus: int) -> bool:
    """Removable as the player means it: a stick, a card, or a disk on an external bus."""
    return drive_type == _DRIVE_REMOVABLE or (drive_type == _DRIVE_FIXED and bus in _EXTERNAL_BUSES)


def human_size(size: int) -> str:
    """`500107862016` → `465.8G`, the way lsblk wrote sizes for this screen."""
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return ""


def _volume_information(root: str) -> tuple[str, str, str]:
    label = ctypes.create_unicode_buffer(261)
    fs_name = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    if not _kernel32().GetVolumeInformationW(root, label, 261, ctypes.byref(serial), None, None,
                                             fs_name, 261):
        return "", "", ""
    s = serial.value
    return label.value, f"{s >> 16:04X}-{s & 0xFFFF:04X}", fs_name.value


def _size(root: str) -> int:
    total = ctypes.c_ulonglong()
    if not _kernel32().GetDiskFreeSpaceExW(root, None, ctypes.byref(total), None):
        return 0
    return total.value


def list_volumes() -> list[dict]:
    """Every external disk with a drive letter, as dicts `storage.Volume` takes."""
    k32 = _kernel32()
    system = os.environ.get("SystemDrive", "C:").upper()
    mask = k32.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = f"{chr(ord('A') + i)}:"
        root = letter + "\\"
        drive_type = k32.GetDriveTypeW(root)
        if letter == system or drive_type not in (_DRIVE_REMOVABLE, _DRIVE_FIXED):
            continue
        if not is_external(drive_type, bus_type(letter)):
            continue
        label, serial, fs_name = _volume_information(root)
        if not fs_name:
            continue                               # a card reader with no card in it
        out.append({"name": letter, "path": letter, "label": label, "uuid": serial,
                    "fstype": fs_name.lower(), "mountpoint": root, "removable": True,
                    "size": human_size(_size(root))})
    return out


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", ctypes.c_byte * 16),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_size_t)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", ctypes.c_byte * 16),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_size_t)]


def _device_number(path: str) -> tuple[int, int] | None:
    """(device type, device number) of a disk or volume path."""
    handle = _open(path)
    if handle is None:
        return None
    try:
        raw = _ioctl(handle, _IOCTL_STORAGE_GET_DEVICE_NUMBER, b"", 12)
    finally:
        _kernel32().CloseHandle(handle)
    return struct.unpack_from("<II", raw) if raw and len(raw) >= 8 else None


def _disk_instance(letter: str) -> int | None:
    """The PnP device instance of the disk holding `letter`."""
    wanted = _device_number(f"\\\\.\\{letter}")
    if wanted is None:
        return None
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(_GUID_DEVINTERFACE_DISK).bytes_le)
    devs = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE)
    if devs in (None, _INVALID_HANDLE):
        return None
    try:
        index = 0
        while True:
            iface = _SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA))
            if not setupapi.SetupDiEnumDeviceInterfaces(ctypes.c_void_p(devs), None,
                                                        ctypes.byref(guid), index,
                                                        ctypes.byref(iface)):
                return None
            index += 1
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W: a DWORD cbSize (8 on x64), then the path.
            detail = ctypes.create_string_buffer(1024)
            struct.pack_into("<I", detail, 0, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            info = _SP_DEVINFO_DATA(cbSize=ctypes.sizeof(_SP_DEVINFO_DATA))
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    ctypes.c_void_p(devs), ctypes.byref(iface), detail, 1024, None,
                    ctypes.byref(info)):
                continue
            path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
            if _device_number(path) == (wanted[0], wanted[1]):
                return info.DevInst
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(devs))


def eject(letter: str) -> tuple[bool, str]:
    """Safely remove the disk holding `letter`. (ok, message)."""
    instance = _disk_instance(letter)
    if instance is None:
        return False, f"{letter} is not a disk Windows can eject"
    cfgmgr = ctypes.WinDLL("cfgmgr32")
    parent = wintypes.DWORD()
    target = instance if cfgmgr.CM_Get_Parent(ctypes.byref(parent), instance, 0) else parent.value
    veto_type = ctypes.c_int()
    veto_name = ctypes.create_unicode_buffer(260)
    code = cfgmgr.CM_Request_Device_EjectW(target, ctypes.byref(veto_type), veto_name, 260, 0)
    if code == _CR_SUCCESS and veto_type.value == 0:
        return True, "Safe to unplug"
    reason = _VETO_REASONS.get(veto_type.value, f"Windows refused (code {code})")
    return False, f"{letter} is busy: {reason}. Close what is reading it, then eject again."
