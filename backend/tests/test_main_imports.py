"""The backend imports on Windows without any Linux-only module.

evdev, python-xlib, pwd, grp and fcntl have no use (or no build) on Windows,
so a top-level import of any of them anywhere `backend.main` reaches would stop
the backend before it serves a single request. Run in a fresh interpreter: this
suite's own process has imported half the tree already.

Import only, never the lifespan: a child process does not get conftest.py's
stubs, and the lifespan acts on the machine's screen and power. `/api/ready`
with the lifespan running is test_boot_ready.py's, in-process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LINUX_ONLY = ["evdev", "Xlib", "pwd", "grp", "fcntl"]

_PROBE = """
import json, sys
from backend import main
print(json.dumps({"routes": len(main.app.routes),
                  "linux": sorted(set(sys.argv[1:]) & set(sys.modules))}))
"""


def test_the_app_imports_with_no_linux_module_loaded():
    """Inside the suite's throwaway roots and home: conftest.py set them in
    this environment, which the child inherits."""
    out = subprocess.run([sys.executable, "-c", _PROBE, *LINUX_ONLY],
                         cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)},
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["linux"] == [], f"imported at startup: {result['linux']}"
    assert result["routes"] > 0
