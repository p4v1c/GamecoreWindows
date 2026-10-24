"""Audio outputs and the master volume: Windows Core Audio, through pycaw.

Replaces `wpctl` (PipeWire). An output is a render endpoint; its id is the
endpoint id string (`{0.0.0.00000000}.{guid}`), which the interface hands back
unchanged to pick it. The default output is set for every role (console,
multimedia, communications), as the Sound settings page does.

Every call is synchronous and blocks on COM: callers run it in a thread
(`asyncio.to_thread`). Each call initialises COM on its own thread.
"""
from __future__ import annotations

import contextlib
import logging
import threading

log = logging.getLogger(__name__)

# PKEY_Device_FriendlyName: "Speakers (Realtek(R) Audio)".
_FRIENDLY_NAME = ("{a45c254e-df1c-4efd-8020-67d146a850e0}", 14)
_ERENDER = 0
_ECONSOLE = 0
_DEVICE_STATE_ACTIVE = 0x1
_STGM_READ = 0


_thread = threading.local()


@contextlib.contextmanager
def _com():
    """COM on this thread; a failed COM call surfaces as OSError, as callers expect.

    Initialised once per thread and never uninitialised: comtypes releases its
    pointers at garbage collection, and a release after CoUninitialize crashes
    the process (measured: segfault on the second call).
    """
    from _ctypes import COMError

    import comtypes

    if not getattr(_thread, "com", False):
        comtypes.CoInitialize()
        _thread.com = True
    try:
        yield
    except COMError as e:
        raise OSError(f"Core Audio refused: {e}") from e


def _enumerator():
    import comtypes
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator

    return comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)


def _friendly_name(device) -> str:
    from comtypes import GUID
    from pycaw.api.mmdeviceapi.depend.structures import PROPERTYKEY

    key = PROPERTYKEY()
    key.fmtid, key.pid = GUID(_FRIENDLY_NAME[0]), _FRIENDLY_NAME[1]
    try:
        value = device.OpenPropertyStore(_STGM_READ).GetValue(key)
        name = value.GetValue()
        value.clear()
        return str(name or "")
    except Exception:          # a COMError on one endpoint must not hide the rest
        return ""


def _endpoint_volume(device):
    from ctypes import POINTER, cast

    import comtypes
    from pycaw.api.endpointvolume import IAudioEndpointVolume

    raw = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return cast(raw, POINTER(IAudioEndpointVolume))


def _default_device(enumerator):
    try:
        return enumerator.GetDefaultAudioEndpoint(_ERENDER, _ECONSOLE)
    except OSError:            # no output at all: E_NOTFOUND
        return None


def list_outputs() -> list[dict]:
    """Active outputs, in Windows' order: `[{"id", "name", "default"}]`."""
    with _com():
        enumerator = _enumerator()
        default = _default_device(enumerator)
        default_id = default.GetId() if default is not None else ""
        collection = enumerator.EnumAudioEndpoints(_ERENDER, _DEVICE_STATE_ACTIVE)
        outputs = []
        for i in range(collection.GetCount()):
            device = collection.Item(i)
            device_id = device.GetId()
            outputs.append({"id": device_id, "name": _friendly_name(device) or device_id,
                            "default": device_id == default_id})
        return outputs


def get_volume() -> tuple[int, bool]:
    """(volume 0-100, muted) of the default output; (100, False) without one."""
    with _com():
        device = _default_device(_enumerator())
        if device is None:
            return 100, False
        volume = _endpoint_volume(device)
        return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())


def set_volume(percent: int) -> None:
    """Set the default output's volume. The mute state is left as it is."""
    with _com():
        device = _default_device(_enumerator())
        if device is None:
            raise OSError("No audio output.")
        volume = _endpoint_volume(device)
        volume.SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)


def set_default_output(device_id: str) -> None:
    """Make `device_id` the default output for every role. Raises OSError."""
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities

    with _com():
        AudioUtilities.SetDefaultDevice(
            device_id, roles=[ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
