"""The Electron shell, exercised as JavaScript.

`electron/` is the one part of this project that no Python test can reason
about: the boot, the HUD toasts and the power commands are decisions taken in
JavaScript, against Electron's own API. So the bench next to it
(`electron/test/`) runs the real modules in a VM with Electron, the child
processes and the network replaced, and this wrapper is what makes it part of
`pytest backend/tests` rather than a file somebody has to remember to run.

Skipped, not failed, where there is no `node`: the backend's own test job does
not need one, and a skip says so where a failure would look like a defect in
the shell.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "electron" / "test"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_electron_shell_bench_passes():
    assert BENCH.is_dir(), BENCH
    files = sorted(BENCH.glob("*.test.cjs"))
    assert files, "the bench is empty — that is not a pass"
    r = subprocess.run(
        ["node", "--test", *[str(f) for f in files]],
        cwd=REPO, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
    assert r.returncode == 0, "electron/test failed — see the output above"
