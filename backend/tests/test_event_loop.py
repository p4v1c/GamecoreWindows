"""The event loop the backend runs on can start subprocesses.

On Windows only the Proactor loop implements subprocesses; the Selector loop
raises NotImplementedError. uvicorn picks Proactor unless it runs with
`--reload` or `--workers` (`use_subprocess`). Games do not depend on it
(`platform/jobs.py` uses `subprocess.Popen`), but other services
(`routers/update.py`, `rest_mode.py`) use `asyncio.create_subprocess_exec`.
"""
from __future__ import annotations

import asyncio
import sys

from uvicorn.loops.asyncio import asyncio_loop_factory


def test_uvicorn_runs_the_backend_on_the_proactor_loop():
    assert asyncio_loop_factory(use_subprocess=False) is asyncio.ProactorEventLoop


def test_a_subprocess_runs_on_the_default_loop():
    async def run() -> int:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        return await proc.wait()

    assert asyncio.run(run()) == 0
