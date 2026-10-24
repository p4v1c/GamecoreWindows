"""COM on worker threads, and WMI queries over it.

Core Audio, WMI and the device queries are COM calls, made from
`asyncio.to_thread` workers. COM is initialised once per thread and never
released: comtypes and pywin32 free their pointers at garbage collection, and
a release after `CoUninitialize` crashes the process (measured: a segfault on
the second Core Audio call).
"""
from __future__ import annotations

import threading

_thread = threading.local()


def ensure_com() -> None:
    """Initialise COM (single-threaded apartment) on this thread, once."""
    if getattr(_thread, "com", False):
        return
    import pythoncom

    pythoncom.CoInitialize()
    _thread.com = True


def wmi_query(namespace: str, wql: str, fields: tuple[str, ...]) -> list[dict]:
    """Rows of a WQL query as dicts of `fields`. Raises OSError when WMI refuses.

    `namespace` is e.g. `root\\cimv2`. Values come back as Python types: a
    string, a number, a bool, a tuple for array properties, or None.
    """
    import pywintypes
    import win32com.client

    ensure_com()
    try:
        service = win32com.client.GetObject(f"winmgmts:\\\\.\\{namespace}")
        return [{field: getattr(row, field, None) for field in fields}
                for row in service.ExecQuery(wql)]
    except pywintypes.com_error as e:
        raise OSError(f"WMI refused {wql!r}: {e}") from e
