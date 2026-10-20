"""Top-level windows of other processes: find, bring to front, borderless fullscreen.

Replaces the EWMH client messages of the Linux build (`_NET_ACTIVE_WINDOW`,
`_NET_WM_STATE_FULLSCREEN`). Windows has no window manager to ask: the backend
moves the windows itself, in the per-monitor DPI context so rectangles are in
real pixels.

Calls that send a message to the window's thread (`SetWindowLongPtrW`) block
while that thread does not answer: callers run them off the event loop, and
never on a suspended game's window.
"""
from __future__ import annotations

import contextlib
import ctypes
import time
from ctypes import wintypes

import psutil

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_GWL_STYLE = -16
_GWL_EXSTYLE = -20
_GW_OWNER = 4
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_WS_SYSMENU = 0x00080000
_FRAME_STYLES = _WS_CAPTION | _WS_THICKFRAME | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX | _WS_SYSMENU
_WS_EX_DLGMODALFRAME = 0x00000001
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_WINDOWEDGE = 0x00000100
_WS_EX_CLIENTEDGE = 0x00000200
_WS_EX_STATICEDGE = 0x00020000
_WS_EX_NOACTIVATE = 0x08000000
_FRAME_EX_STYLES = _WS_EX_DLGMODALFRAME | _WS_EX_WINDOWEDGE | _WS_EX_CLIENTEDGE | _WS_EX_STATICEDGE
_HWND_TOP = 0
_SWP_FRAMECHANGED = 0x0020
_SWP_SHOWWINDOW = 0x0040
_SWP_NOOWNERZORDER = 0x0200
# Posted, not sent: a game that does not answer cannot block the caller.
_SWP_ASYNCWINDOWPOS = 0x4000
_SW_RESTORE = 9
_MONITOR_DEFAULTTONEAREST = 2
_WM_NULL = 0x0000
_SMTO_ABORTIFHUNG = 0x0002
# How long a window gets to answer before its thread counts as frozen.
_RESPONSE_TIMEOUT_MS = 200
_SETTLE_SECS = 0.15
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = (_EnumProc, wintypes.LPARAM)
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)
_user32.IsWindow.argtypes = (wintypes.HWND,)
_user32.IsIconic.argtypes = (wintypes.HWND,)
_user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT)
_user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.BringWindowToTop.argtypes = (wintypes.HWND,)
_user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
_user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                        ctypes.POINTER(ctypes.c_size_t))
_user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
_user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo))


@contextlib.contextmanager
def _real_pixels():
    """Per-monitor DPI awareness for this thread only: without it a scaled
    display reports and accepts rectangles shrunk by its scale factor."""
    previous = _user32.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        yield
    finally:
        if previous:
            _user32.SetThreadDpiAwarenessContext(previous)


def _owner_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _is_app_window(hwnd: int) -> bool:
    """Visible, unowned, not a tool or non-activating window, not empty: what a
    player would call the game's window."""
    if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, _GW_OWNER):
        return False
    ex = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
    if ex & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE):
        return False
    left, top, right, bottom = _rect(hwnd)
    return right > left and bottom > top


def top_level_windows(pids: set[int]) -> list[int]:
    """The app windows owned by any of `pids`, in Z order (topmost first)."""
    found: list[int] = []

    @_EnumProc
    def visit(hwnd, _lparam):
        if _owner_pid(hwnd) in pids and _is_app_window(hwnd):
            found.append(hwnd)
        return True

    if pids:
        _user32.EnumWindows(visit, 0)
    return found


def windows_of_executables(names: tuple[str, ...]) -> list[int]:
    """App windows whose process image is one of `names` (case-insensitive),
    largest first."""
    wanted = {n.lower() for n in names}
    pids: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() in wanted:
            pids.add(proc.pid)

    def area(hwnd: int) -> int:
        left, top, right, bottom = _rect(hwnd)
        return (right - left) * (bottom - top)

    return sorted(top_level_windows(pids), key=area, reverse=True)


def is_foreground(hwnd: int) -> bool:
    return bool(hwnd) and _user32.GetForegroundWindow() == hwnd


def bring_to_front(hwnd: int) -> bool:
    """Make `hwnd` the foreground window. True when it is, afterwards.

    Windows refuses the foreground to a background process, which the backend
    is: when the plain call fails, it is repeated with this thread's input
    state attached to the foreground window's thread. Never when that thread
    is frozen (a suspended game): attached, the activation waits on it for as
    long as it stays frozen. Measured on the real PC: over a frozen window the
    plain call is allowed and enough.
    """
    if not _user32.IsWindow(hwnd):
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindowAsync(hwnd, _SW_RESTORE)
    foreground = _user32.GetForegroundWindow()
    if foreground == hwnd:
        return True
    if _user32.SetForegroundWindow(hwnd) and _settles_on(hwnd):
        return True
    # The window that was in front, not whatever the refused call left there
    # for a moment (often nothing).
    if foreground and _responds(foreground):
        _attached_to(foreground, hwnd)
    return _settles_on(hwnd)


def _settles_on(hwnd: int) -> bool:
    """`hwnd` is the foreground once the activation has gone through: it is
    asynchronous for a window of another thread."""
    deadline = time.monotonic() + _SETTLE_SECS
    while not is_foreground(hwnd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _responds(hwnd: int) -> bool:
    """The window's thread answers a no-op message in time (it is not frozen)."""
    result = ctypes.c_size_t()
    return bool(_user32.SendMessageTimeoutW(hwnd, _WM_NULL, 0, 0, _SMTO_ABORTIFHUNG,
                                            _RESPONSE_TIMEOUT_MS, ctypes.byref(result)))


def _attached_to(foreground: int, hwnd: int) -> bool:
    theirs = _user32.GetWindowThreadProcessId(foreground, None)
    mine = _kernel32.GetCurrentThreadId()
    attached = bool(theirs and theirs != mine and _user32.AttachThreadInput(mine, theirs, True))
    try:
        _user32.BringWindowToTop(hwnd)
        return bool(_user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            _user32.AttachThreadInput(mine, theirs, False)


def _monitor_rect(hwnd: int) -> tuple[int, int, int, int]:
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    monitor = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    _user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def covers_its_monitor(hwnd: int) -> bool:
    """No frame, and exactly the monitor's rectangle: borderless fullscreen
    (or exclusive fullscreen, which looks the same from here)."""
    with _real_pixels():
        style = _user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
        return not style & (_WS_CAPTION | _WS_THICKFRAME) and _rect(hwnd) == _monitor_rect(hwnd)


def make_borderless_fullscreen(hwnd: int) -> bool:
    """Drop the frame and stretch `hwnd` over its monitor, on top.

    Borderless rather than exclusive fullscreen: the interface's alerts are a
    topmost window, which exclusive fullscreen would hide.
    """
    if not _user32.IsWindow(hwnd):
        return False
    with _real_pixels():
        if _user32.IsIconic(hwnd):
            _user32.ShowWindowAsync(hwnd, _SW_RESTORE)
        style = _user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
        ex = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
        _user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style & ~_FRAME_STYLES)
        _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex & ~_FRAME_EX_STYLES)
        left, top, right, bottom = _monitor_rect(hwnd)
        return bool(_user32.SetWindowPos(
            hwnd, _HWND_TOP, left, top, right - left, bottom - top,
            _SWP_FRAMECHANGED | _SWP_SHOWWINDOW | _SWP_NOOWNERZORDER | _SWP_ASYNCWINDOWPOS))

