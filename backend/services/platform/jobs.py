"""A game's whole process tree as one Windows Job Object.

Replaces the Linux process group (`start_new_session=True`, `os.killpg`,
SIGSTOP/SIGCONT/SIGKILL). The game starts suspended, joins its job, then
runs: every process it starts after that is born inside the job, so
suspending, resuming and killing reach the whole tree, launcher wrappers and
helper processes included.

No `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: a backend that restarts (update,
crash) closes its handles, and the game must survive that to be adopted
(`docs/architecture/09-gotchas.md`, "Process and lifecycle"). Killing is an
explicit `terminate()`.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import uuid
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

CREATE_SUSPENDED = 0x00000004
# Its own console process group, so a Ctrl+C in the backend's console never
# reaches the game: the job's answer to `start_new_session=True`.
CREATE_NEW_PROCESS_GROUP = 0x00000200
# A console program gets a hidden console instead of a black window over the TV.
CREATE_NO_WINDOW = 0x08000000

_PROCESS_TERMINATE = 0x0001
_PROCESS_DUP_HANDLE = 0x0040
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SUSPEND_RESUME = 0x0800
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_JOB_OBJECT_ALL_ACCESS = 0x1F001F
_DUPLICATE_SAME_ACCESS = 0x2

_JOB_BASIC_ACCOUNTING = 1
_JOB_BASIC_PROCESS_ID_LIST = 3
# More than any emulator starts; a longer list is cut, never an error.
_MAX_LISTED_PIDS = 512
# A process started while a pass suspends the others is caught by the next one.
_SUSPEND_PASSES = 4
# What a killed game exits with, like 128 + SIGKILL on Linux.
KILLED_EXIT_CODE = 137
#: Names the session's job to the processes inside it.
JOB_ENV = "GAMECORE_JOB"

_kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.OpenJobObjectW.restype = wintypes.HANDLE
_kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.TerminateJobObject.restype = wintypes.BOOL
_kernel32.QueryInformationJobObject.argtypes = (
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD))
_kernel32.QueryInformationJobObject.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.DuplicateHandle.argtypes = (
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.DuplicateHandle.restype = wintypes.BOOL
_ntdll.NtSuspendProcess.argtypes = (wintypes.HANDLE,)
_ntdll.NtSuspendProcess.restype = ctypes.c_long
_ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
_ntdll.NtResumeProcess.restype = ctypes.c_long


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]


class _PidList(ctypes.Structure):
    _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * _MAX_LISTED_PIDS)]


def _check(ok) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _open_process(pid: int, rights: int) -> int | None:
    """A handle to `pid`, or None when it has exited or is not ours to open."""
    return _kernel32.OpenProcess(rights, False, pid) or None


class GameJob:
    """One launched game: its job, its first process, and the job's name.

    The name (`Local\\GameCore-<uuid>`) is what `session.json` keeps, so a
    restarted backend opens the same job again (`open_job`).
    """

    def __init__(self, handle: int, name: str, leader_pid: int,
                 popen: subprocess.Popen | None = None):
        self._handle = handle
        self.name = name
        self.leader_pid = leader_pid
        self._popen = popen

    def _accounting(self) -> _Accounting | None:
        if not self._handle:
            return None
        info = _Accounting()
        ok = _kernel32.QueryInformationJobObject(
            self._handle, _JOB_BASIC_ACCOUNTING, ctypes.byref(info),
            ctypes.sizeof(info), None)
        return info if ok else None

    def alive(self) -> bool:
        """At least one process of the tree is still running (or frozen)."""
        info = self._accounting()
        return info is not None and info.ActiveProcesses > 0

    def pids(self) -> list[int]:
        """Every process in the job right now."""
        if not self._handle:
            return []
        listed = _PidList()
        ok = _kernel32.QueryInformationJobObject(
            self._handle, _JOB_BASIC_PROCESS_ID_LIST, ctypes.byref(listed),
            ctypes.sizeof(listed), None)
        # ERROR_MORE_DATA still fills the list up to its size.
        if not ok and ctypes.get_last_error() != 234:
            return []
        count = min(listed.NumberOfProcessIdsInList, _MAX_LISTED_PIDS)
        return [int(pid) for pid in listed.ProcessIdList[:count]]

    def suspend(self) -> bool:
        """Freeze every process of the job. All or nothing: on a failure the
        ones already frozen are thawed again and False is returned."""
        done: list[int] = []
        for _ in range(_SUSPEND_PASSES):
            fresh = [pid for pid in self.pids() if pid not in done]
            if not fresh:
                break
            for pid in fresh:
                outcome = _nt_call(_ntdll.NtSuspendProcess, pid)
                if outcome is False:
                    for frozen in done:
                        _nt_call(_ntdll.NtResumeProcess, frozen)
                    return False
                if outcome:
                    done.append(pid)
        return bool(done)

    def resume(self) -> bool:
        """Thaw every process of the job. True when at least one was thawed and
        none refused."""
        thawed = False
        for pid in self.pids():
            outcome = _nt_call(_ntdll.NtResumeProcess, pid)
            if outcome is False:
                return False
            thawed = thawed or bool(outcome)
        return thawed

    def terminate(self) -> bool:
        """Kill the whole tree at once, frozen processes included. Never a
        polite close: emulators answer one with a dialog no pad can click."""
        if not self._handle:
            return False
        return bool(_kernel32.TerminateJobObject(self._handle, KILLED_EXIT_CODE))

    def close(self) -> None:
        """Let go of the job. The processes are not touched."""
        if self._popen is not None:
            self._popen.poll()
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = 0


def _nt_call(function, pid: int) -> bool | None:
    """Suspend or resume one process: True done, None gone, False refused."""
    handle = _open_process(pid, _PROCESS_SUSPEND_RESUME)
    if handle is None:
        # Exited between the list and here (ERROR_INVALID_PARAMETER) is not a
        # failure; access denied is.
        return None if ctypes.get_last_error() == 87 else False
    try:
        return function(handle) >= 0
    finally:
        _kernel32.CloseHandle(handle)


def _keep_name_alive(job: int, process: int) -> None:
    """Give the game's first process a handle to its own job.

    A job's name dies with its last handle. Without this one, a backend that
    restarts closes the only handle and can never open the job again; with it,
    the name lives as long as the game's first process does.
    """
    target = wintypes.HANDLE()
    _check(_kernel32.DuplicateHandle(
        _kernel32.GetCurrentProcess(), job, process, ctypes.byref(target),
        0, False, _DUPLICATE_SAME_ACCESS))


def spawn(cmd: list[str], *, env: dict, stdout, cwd: str | None = None) -> GameJob:
    """Start `cmd` inside a new job. Raises OSError as Popen does.

    The process is created suspended and only runs once it is in the job, so
    nothing it starts can be born outside it. `JOB_ENV` in its environment
    names the job, so a launcher can take a running single-instance app into
    it (catalog/steam/files/steam_session.py).
    """
    name = f"Local\\GameCore-{uuid.uuid4().hex}"
    env = {**(os.environ if env is None else env), JOB_ENV: name}
    job = _kernel32.CreateJobObjectW(None, name)
    _check(job)
    try:
        popen = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=stdout,
            stderr=subprocess.STDOUT, env=env, cwd=cwd,
            creationflags=CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
    except OSError:
        _kernel32.CloseHandle(job)
        raise
    process = _open_process(popen.pid, _PROCESS_SET_QUOTA | _PROCESS_TERMINATE
                            | _PROCESS_DUP_HANDLE | _PROCESS_SUSPEND_RESUME)
    try:
        _check(process)
        _check(_kernel32.AssignProcessToJobObject(job, process))
        _keep_name_alive(job, process)
        if _ntdll.NtResumeProcess(process) < 0:
            raise OSError(f"could not start {cmd[0]}: it stayed suspended")
    except OSError:
        popen.kill()
        popen.wait()
        _kernel32.CloseHandle(job)
        raise
    finally:
        if process:
            _kernel32.CloseHandle(process)
    return GameJob(job, name, popen.pid, popen)


def open_job(name: str, leader_pid: int) -> GameJob | None:
    """The job a previous backend started, or None when it is gone."""
    if not name.startswith("Local\\GameCore-"):
        return None
    handle = _kernel32.OpenJobObjectW(_JOB_OBJECT_ALL_ACCESS, False, name)
    return GameJob(handle, name, leader_pid) if handle else None
