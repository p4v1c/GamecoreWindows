"""Which SDL an emulator really loads, and asking that SDL about a pad.

Each Flatpak bundles its own SDL; the same pad has a different GUID in each
(see controllers.py). These helpers find the library a given app uses and run
a short subprocess probe against it. Every result is cached; only successes
are cached for location lookups.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from ..platform import gamepads as platform_gamepads

log = logging.getLogger(__name__)


# The two variables SDL reads a mapping table from. Named once because two
# places care about them for opposite reasons: this file's name lookup wants
# the served table, and every SUBPROCESS PROBE wants none of it — see
# `probe_env` below.
_MAPPING_ENV = ("SDL_GAMECONTROLLERCONFIG_FILE", "SDL_GAMECONTROLLERCONFIG")


def probe_env() -> dict[str, str]:
    """The environment an SDL probe subprocess runs in: this one, minus any
    mapping table.

    A probe asks SDL what IT knows about a pad. A mapping file on the way in
    makes it answer with what the file knows instead — and SDL keeps the LAST
    line it reads for a GUID, so a table containing the owner's capture wins
    over SDL's built-in every time. The answer is then evdev indices wearing
    the name of the driver's.

    Scrubbed here rather than trusted to be absent: the backend is not the only
    thing that can put it there, and a probe whose answer depends on who
    started the process is a probe that gives one box two configs.
    """
    env = dict(os.environ)
    for name in _MAPPING_ENV:
        env.pop(name, None)
    return env


# ── asking a specific SDL2 ────────────────────────────────────────────────

_SDL2_PROBE = (
    "import ctypes,os,sys\n"
    "os.environ['SDL_VIDEODRIVER']='dummy'\n"
    "v=int(sys.argv[1],16);p=int(sys.argv[2],16)\n"
    "lib=sys.argv[3] if len(sys.argv)>3 and sys.argv[3] else 'libSDL2-2.0.so.0'\n"
    "try: s=ctypes.CDLL(lib)\n"
    "except OSError: sys.exit(0)\n"
    "class G(ctypes.Structure): _fields_=[('data',ctypes.c_uint8*16)]\n"
    "s.SDL_GameControllerMappingForDeviceIndex.restype=ctypes.c_char_p\n"
    "s.SDL_GameControllerMappingForDeviceIndex.argtypes=[ctypes.c_int]\n"
    "s.SDL_JoystickGetDeviceGUID.restype=G\n"
    "s.SDL_JoystickGetDeviceGUID.argtypes=[ctypes.c_int]\n"
    "s.SDL_JoystickGetDeviceVendor.restype=ctypes.c_uint16\n"
    "s.SDL_JoystickGetDeviceProduct.restype=ctypes.c_uint16\n"
    "s.SDL_JoystickGetDeviceVendor.argtypes=[ctypes.c_int]\n"
    "s.SDL_JoystickGetDeviceProduct.argtypes=[ctypes.c_int]\n"
    "s.SDL_JoystickOpen.restype=ctypes.c_void_p\n"
    "s.SDL_JoystickOpen.argtypes=[ctypes.c_int]\n"
    "s.SDL_JoystickNumAxes.restype=ctypes.c_int\n"
    "s.SDL_JoystickNumAxes.argtypes=[ctypes.c_void_p]\n"
    "s.SDL_JoystickClose.argtypes=[ctypes.c_void_p]\n"
    "if s.SDL_Init(0x2000)!=0: sys.exit(0)\n"
    "for i in range(s.SDL_NumJoysticks()):\n"
    " if s.SDL_JoystickGetDeviceVendor(i)==v and s.SDL_JoystickGetDeviceProduct(i)==p:\n"
    "  print('GUID '+bytes(s.SDL_JoystickGetDeviceGUID(i).data).hex())\n"
    "  j=s.SDL_JoystickOpen(i)\n"
    "  if j: print('AXES '+str(max(0,s.SDL_JoystickNumAxes(j)))); s.SDL_JoystickClose(j)\n"
    "  m=s.SDL_GameControllerMappingForDeviceIndex(i)\n"
    "  print('MAP '+m.decode()) if m else None\n"
    "  break\n"
    "s.SDL_Quit()\n"
)

_sdl2_cache: dict[tuple[str, str, str], tuple[float, dict[str, str]]] = {}
_bundled_sdl_cache: dict[str, str] = {}
_bundled_sdl3_cache: dict[str, str] = {}
_flatpak_loc_cache: dict[str, str] = {}
_runtime_loc_cache: dict[str, str] = {}


# Where `flatpak run` finds an app's current branch: user installation first.
_INSTALLATIONS = (Path.home() / ".local/share/flatpak", Path("/var/lib/flatpak"))


def _current_branch(app_id: str) -> str:
    """The branch `flatpak run <app_id>` starts, from its `current` link, or "".

    Needed only when two branches are installed (a locally built Eden next to
    Flathub's): `flatpak info <app_id>` then refuses with "multiple branches",
    and every SDL lookup below fell back to the host's library in silence.
    """
    for root in _INSTALLATIONS:
        link = root / "app" / app_id / "current"
        if link.is_symlink():
            return Path(os.readlink(link)).name
    return ""


def _flatpak_info(flag: str, ref: str) -> str:
    """`flatpak info <flag> <ref>`'s answer, or "" on any failure."""
    try:
        r = subprocess.run(["flatpak", "info", flag, ref],
                           capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _app_info(flag: str, app_id: str) -> str:
    """`flatpak info` about an app, retried on its current branch."""
    out = _flatpak_info(flag, app_id)
    if not out and (branch := _current_branch(app_id)):
        out = _flatpak_info(flag, f"{app_id}//{branch}")
    return out


def flatpak_location(app_id: str) -> str:
    """Deploy directory of an installed flatpak, or "".

    **Only a SUCCESS is cached**, and that is the difference between a bad
    minute and a bad session. A `flatpak info` can fail transiently — a cold
    boot with the repo lock still held, an OTA settling, a system that has not
    finished mounting the user installation — and caching that "" pinned it for
    the whole life of the backend process. Every consumer downstream then took
    the branch meant for "this emulator is not installed": `Pad.guid_for`
    refused for ever, and `bundled_sdl3` answered "" so its caller fell through
    to the host's library.

    Measured cost of one such minute: an RMG profile written from the HOST's
    libSDL3 while RMG reads the runtime's, and those two do not agree about a
    DualSense's serial (see `bundled_sdl3`). The pad was bound to nothing, in
    silence, until the backend was restarted.

    A miss costs one `flatpak info`, measured at 14 ms on the reference box.
    That is not a price worth paying a session of wrong configs to avoid.
    """
    if _flatpak_loc_cache.get(app_id):
        return _flatpak_loc_cache[app_id]
    out = _app_info("--show-location", app_id)
    if out:
        _flatpak_loc_cache[app_id] = out
    return out


def flatpak_runtime_location(app_id: str) -> str:
    """Deploy directory of the RUNTIME an installed flatpak links against.

    An app that ships no SDL of its own is not an app with no SDL: it links its
    runtime's, and that library is on this filesystem and can be asked.

    Only a success is cached, for the reason `flatpak_location` sets out: a
    transient failure pinned here is a session of configs written from the
    wrong library.
    """
    if _runtime_loc_cache.get(app_id):
        return _runtime_loc_cache[app_id]
    runtime = _app_info("--show-runtime", app_id)
    out = _flatpak_info("--show-location", f"runtime/{runtime}") if runtime else ""
    if out:
        _runtime_loc_cache[app_id] = out
    return out


# Where a flatpak runtime keeps its shared libraries. One entry today, and a
# list rather than a constant because the path is the runtime's convention and
# not ours.
_RUNTIME_LIB_DIRS = ("files/lib/x86_64-linux-gnu", "files/lib")


def bundled_sdl2(app_id: str) -> str:
    """Absolute path of the SDL2 a flatpak'd emulator really uses, or "".

    Measured on the reference box, same physical DualShock 4, same instant:

        host libSDL2-2.0.so.0 (sdl2-compat over SDL3)
            05008fe54c050000cc09000000006800   bus 0x0005, Bluetooth
        Ryujinx's bundled libSDL2.so (real SDL 2.30.0)
            03008fe54c050000cc09000000006800   bus 0x0003, USB

    One byte, the bus type: SDL3 reports the transport, SDL2 2.30 reports USB
    for anything HIDAPI drives, Bluetooth included. That one byte is the
    difference between Ryujinx binding the pad and its
    `_gamepadsIds.IndexOf(id)` returning -1 and disposing the slot in silence.

    **The runtime is checked when the app ships nothing, and it is not a
    nicety.** Ryujinx bundles `files/bin/libSDL2.so`; azahar, melonDS and RMG
    ship none and link `org.kde.Platform`'s. Returning "" for those meant "ask
    the host", and the host is sdl2-compat over SDL3 — a different library that
    answers differently. Measured, one DualShock 4, same instant:

        host sdl2-compat 2.32.70    dpup:h0.1  dpdown:h0.4  …  touchpad:b11
        org.kde.Platform 6.9's
        real SDL 2.32.10            dpup:b11   dpdown:b12  …  touchpad:b15

    That second line is not a curiosity: `snapshots.py` records azahar writing
    `button_up = 11` for this exact pad and calls it unexplainable next to SDL's
    own mapping "which claims a hat and calls button 11 the touchpad". Both are
    true, and this is why — two SDL2 builds, two answers, and azahar's is the
    one in the runtime. Asking the host for an azahar binding produces a hat
    where azahar wants button 11: a config full of plausible numbers binding the
    wrong things, which is the failure the whole package is arranged to avoid.

    App first, runtime second, host never: the order is the specificity order,
    and an app that bundles its own SDL is not affected by any of this.
    """
    if app_id in _bundled_sdl_cache:
        return _bundled_sdl_cache[app_id]
    path = ""
    loc = flatpak_location(app_id)
    if loc:
        lib = Path(loc) / "files" / "bin" / "libSDL2.so"
        if lib.is_file():
            path = str(lib)
    if not path:
        runtime = flatpak_runtime_location(app_id)
        for rel in _RUNTIME_LIB_DIRS if runtime else ():
            lib = Path(runtime) / rel / "libSDL2-2.0.so.0"
            if lib.is_file():
                path = str(lib)
                break
    _bundled_sdl_cache[app_id] = path
    return path


def sdl2_probe(vendor: str, product: str, lib: str = "") -> dict[str, str]:
    """What SDL2 itself says about a connected pad: its raw 32-hex GUID and its
    GameController mapping. `{}` when SDL2 cannot be asked.

    **Itself** is the load-bearing word, and `probe_env` is what enforces it:
    the probe runs with no mapping table in its environment, so the mapping
    that comes back is SDL's own built-in one for the driver it really uses.
    Handed the served database instead, it returns the owner's capture — evdev
    indices for a pad SDL reads through HIDAPI — and the caller has no way to
    tell the two apart.

    `lib` picks WHICH SDL2 answers. Empty means the backend's own SDL2.dll
    (`platform/gamepads.py`). Pass an emulator's bundled one whenever the
    answer is going into that emulator's config.

    Run in a SUBPROCESS because the backend has already loaded SDL3 into its
    own address space, and because these answers must come from the same SDL2
    the emulators use.
    """
    key = (vendor.lower(), product.lower(), lib)
    ts, cached = _sdl2_cache.get(key, (0.0, {}))
    if cached and time.monotonic() - ts <= 5.0:
        return cached
    try:
        r = subprocess.run([sys.executable, "-c", _SDL2_PROBE, vendor, product,
                            lib or platform_gamepads.sdl2_library()],
                           capture_output=True, text=True, timeout=8,
                           env=probe_env())
    except (OSError, subprocess.SubprocessError) as e:
        # Two different facts used to leave here as the same empty dict: "SDL
        # ran, and this pad is not among its joysticks" and "SDL was never
        # asked". Only the first is an answer about the pad.
        #
        # The second is what the reference box hit — three times over several
        # days, each at the instant a Bluetooth pad connected — and it reached
        # the player as "SDL2 would not report a GUID": a claim about SDL's
        # answer, for a probe that never produced one. It sent the diagnosis
        # to gamecontrollerdb and to /dev/input permissions, neither of which
        # was involved; measured afterwards, that same SDL2 returned the GUID
        # ten times out of ten in 0.85 s.
        #
        # The exception was discarded as well, so the journal could not tell a
        # timeout from a failure to spawn — which is why the errno behind those
        # three lines is now unrecoverable. `error` carries the class to the
        # caller, the log line carries the detail to whoever reads it.
        log.warning("configgen: SDL2 probe for %s:%s could not be run (%s: %s)",
                    vendor, product, e.__class__.__name__, e)
        # Deliberately not cached: a probe that never ran is not a finding
        # about this pad, and the monitor's retry must be free to ask again.
        return {"error": e.__class__.__name__}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        tag, _, value = line.partition(" ")
        if tag in ("GUID", "MAP", "AXES") and value:
            out[tag.lower()] = value.strip()
    if out:
        _sdl2_cache[key] = (time.monotonic(), out)
    return out


# ── asking a specific SDL3 who a pad IS ───────────────────────────────────

# RMG-Input identifies a pad by three strings at once and compares them by
# equality (Source/RMG-Input/main.cpp:647):
#
#     deviceName == profile->DeviceName && devicePath == profile->DevicePath
#                                       && deviceSerial == profile->DeviceSerial
#
# read from SDL_GetGamepadName / Path / Serial. One field wrong and the profile
# does not attach — silently, the way a wrong GUID silently disposes Ryujinx's
# slot. The name has a source of its own (`resolve_name`, which is already what
# every SDL3-name consumer uses); the other two are what this probe is for.
#
# **They are not derivable from the pad's ids, and not interchangeable.**
# Measured on the reference box, same instant:
#
#     054c:09cc DualShock 4    path /dev/hidraw0     serial 40:1b:5f:b9:ea:8d
#     045e:02fd Xbox Wireless  path /dev/input/event14   serial ""
#
# SDL names the node of whichever driver it reads the pad through — hidraw for
# the HIDAPI families, the evdev node otherwise — and only a HIDAPI-driven pad
# has a serial at all. So `_hidraw_for(serial)` can answer for the first pad
# and can never answer for the second, and inventing an empty path for it
# writes a profile RMG will not match.
_SDL3_IDENTITY_PROBE = (
    "import ctypes,os,sys\n"
    "os.environ['SDL_VIDEODRIVER']='dummy'\n"
    "os.environ.setdefault('SDL_NO_SIGNAL_HANDLERS','1')\n"
    "v=int(sys.argv[1],16);p=int(sys.argv[2],16)\n"
    "lib=sys.argv[3] if len(sys.argv)>3 and sys.argv[3] else 'libSDL3.so.0'\n"
    "try: s=ctypes.CDLL(lib)\n"
    "except OSError: sys.exit(0)\n"
    "s.SDL_InitSubSystem.restype=ctypes.c_bool\n"
    "s.SDL_InitSubSystem.argtypes=[ctypes.c_uint32]\n"
    "s.SDL_GetGamepads.restype=ctypes.POINTER(ctypes.c_uint32)\n"
    "s.SDL_GetGamepads.argtypes=[ctypes.POINTER(ctypes.c_int)]\n"
    "s.SDL_GetGamepadVendorForID.restype=ctypes.c_uint16\n"
    "s.SDL_GetGamepadProductForID.restype=ctypes.c_uint16\n"
    "s.SDL_GetGamepadVendorForID.argtypes=[ctypes.c_uint32]\n"
    "s.SDL_GetGamepadProductForID.argtypes=[ctypes.c_uint32]\n"
    "s.SDL_GetGamepadPathForID.restype=ctypes.c_char_p\n"
    "s.SDL_GetGamepadPathForID.argtypes=[ctypes.c_uint32]\n"
    "s.SDL_OpenGamepad.restype=ctypes.c_void_p\n"
    "s.SDL_OpenGamepad.argtypes=[ctypes.c_uint32]\n"
    "s.SDL_GetGamepadSerial.restype=ctypes.c_char_p\n"
    "s.SDL_GetGamepadSerial.argtypes=[ctypes.c_void_p]\n"
    "s.SDL_CloseGamepad.argtypes=[ctypes.c_void_p]\n"
    "if not s.SDL_InitSubSystem(0x2000): sys.exit(0)\n"
    "n=ctypes.c_int(0)\n"
    "ids=s.SDL_GetGamepads(ctypes.byref(n))\n"
    "for i in range(n.value):\n"
    " j=ids[i]\n"
    " if s.SDL_GetGamepadVendorForID(j)!=v: continue\n"
    " if s.SDL_GetGamepadProductForID(j)!=p: continue\n"
    " q=s.SDL_GetGamepadPathForID(j)\n"
    " print('PATH '+(q.decode() if q else ''))\n"
    " g=s.SDL_OpenGamepad(j)\n"
    " if g:\n"
    "  r=s.SDL_GetGamepadSerial(g)\n"
    "  print('SERIAL '+(r.decode() if r else ''))\n"
    "  s.SDL_CloseGamepad(g)\n"
    " break\n"
    "s.SDL_Quit()\n"
)

_sdl3_identity_cache: dict[tuple[str, str, str], tuple[float, dict[str, str]]] = {}


def bundled_sdl3(app_id: str) -> str:
    """Absolute path of the libSDL3 a flatpak'd emulator really uses, or "".

    `bundled_sdl2`'s twin, and same order — app first, runtime second — for the
    same reason: an app that ships its own library is not answered by anybody
    else's. RMG ships none and links `org.kde.Platform`'s.

    **"Host never", exactly like its twin — and this docstring used to say the
    opposite.** It claimed the host was an acceptable last resort, on the
    strength of one DualShock 4 answering identically from both builds. A
    second pad settled it, same instant, same box:

        pad                    host libSDL3 3.4.12   org.kde.Platform 6.10's
                                                     libSDL3 3.2.30 (RMG's)
        DualShock 4 054c:09cc  40:1b:5f:b9:ea:8d     40:1b:5f:b9:ea:8d
        DualSense   054c:0ce6  50-ee-32-32-88-2d     50:ee:32:32:88:2d
                               ^^ dashes            ^^ colons

    The two builds format a DualSense's serial differently, and RMG-Input
    compares `DeviceSerial` by string equality. A profile written from the
    host's answer names a pad RMG will never recognise — the port stays empty
    and nothing says why. The DualShock 4 kept working throughout, which is
    what made a one-pad measurement look like a rule.

    So a caller that has an `app_id` must treat "" as a REFUSAL and write
    nothing, never as permission to ask the host. That is what `guid_for`
    already does for the bus byte, and this is the same lesson reached by a
    different road: a string is only comparable to itself if both sides came
    from the same library.
    """
    if _bundled_sdl3_cache.get(app_id):
        return _bundled_sdl3_cache[app_id]
    path = ""
    loc = flatpak_location(app_id)
    if loc:
        lib = Path(loc) / "files" / "lib" / "libSDL3.so.0"
        if lib.is_file():
            path = str(lib)
    if not path:
        runtime = flatpak_runtime_location(app_id)
        for rel in _RUNTIME_LIB_DIRS if runtime else ():
            lib = Path(runtime) / rel / "libSDL3.so.0"
            if lib.is_file():
                path = str(lib)
                break
    if path:
        _bundled_sdl3_cache[app_id] = path
    return path


def sdl3_identity(vendor: str, product: str, lib: str = "") -> dict[str, str]:
    """`{"path": …, "serial": …}` for a connected pad, or `{}`.

    A present key with an empty value is an ANSWER — "SDL read this pad through
    a driver that gives it no serial" — and `{}` is the absence of one. The
    caller must be able to tell those apart: writing `DeviceSerial = ""` is
    correct for an Xbox pad and is a guess for a pad SDL never reported.

    Run in a SUBPROCESS, like `sdl2_probe` and for one more reason besides. The
    backend has libSDL3 loaded in its own address space already, so a second
    build cannot be dlopen'd next to it; and reading a serial means OPENING the
    gamepad, which hands the pad to SDL's HIDAPI driver for the duration. Doing
    that inside the process that is also enumerating pads on a three-second
    loop is a side effect nobody asked for.

    `probe_env()` rather than the served mapping table, and the distinction is
    the one `inputs.py` draws: a mapping table can rename a pad, so a NAME is
    taken from `resolve_name`, which enumerates WITH the table exactly as the
    emulators will. A path and a serial come from the device and no table can
    speak into them.
    """
    key = (vendor.lower(), product.lower(), lib)
    ts, cached = _sdl3_identity_cache.get(key, (0.0, {}))
    if cached and time.monotonic() - ts <= 5.0:
        return cached
    try:
        r = subprocess.run(
            [sys.executable, "-c", _SDL3_IDENTITY_PROBE, vendor, product, lib],
            capture_output=True, text=True, timeout=8, env=probe_env())
    except (OSError, subprocess.SubprocessError) as e:
        # Same distinction `sdl2_probe` makes, and not cached for the same
        # reason: a probe that never ran said nothing about this pad.
        log.warning("configgen: SDL3 identity probe for %s:%s could not be run "
                    "(%s: %s)", vendor, product, e.__class__.__name__, e)
        return {}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        tag, _sep, value = line.partition(" ")
        if tag in ("PATH", "SERIAL"):
            out[tag.lower()] = value.strip()
    if out:
        _sdl3_identity_cache[key] = (time.monotonic(), out)
    return out
