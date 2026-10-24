"""Tests of Linux code the Windows port has not replaced yet.

`@pytest.mark.linux_until_port(step)` (or `pytestmark = ...`) marks a test
whose code under test stays until port `step`; conftest.py skips it on
Windows with that step named. `linux_until_port_module(step)` does the same
for a module that cannot even be imported on Windows (`pwd`, `fcntl` at import
time): call it before those imports. The step that replaces the code deletes
the test with it. Every use is listed in docs/dev-log/windows-port.md.
"""
from __future__ import annotations

import sys

import pytest

# Port steps that remove Linux code (docs/dev-log/windows-port.md, Status).
# 11 is not a port step: the Linux packs, rewritten for Windows after the port.
STEPS = range(3, 12)
PACKS = 11


def reason(step: int) -> str:
    """The skip reason, or UsageError for a step that removes no Linux code."""
    if step not in STEPS:
        raise pytest.UsageError(
            f"linux_until_port needs a port step in {STEPS.start}-{STEPS.stop - 1}, got {step!r}")
    if step == PACKS:
        return "Linux pack code, replaced when the packs are rewritten for Windows"
    return f"Linux code, replaced at Windows port step {step}"


def linux_until_port_module(step: int) -> None:
    """Skip the calling test module on Windows."""
    message = reason(step)
    if sys.platform == "win32":
        pytest.skip(message, allow_module_level=True)
