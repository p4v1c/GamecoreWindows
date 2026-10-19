"""Re-running the installer keeps the record of what it created.

arch.sh writes "did this install create it" flags (USER_CREATED,
BRIDGE_VENV_CREATED, LINGER_ENABLED, INPUT_GROUP_ADDED) that uninstall.sh
reads to decide what to remove. The installer recommends running it again; on
that second run the account and the venv exist, created by the first run, and
were recorded as 0. The uninstaller then left GameCore's own user behind.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_until_port(7)

ARCH = Path(__file__).resolve().parents[2] / "install" / "arch.sh"


def _fn(name: str) -> str:
    return re.search(rf"^{name}\(\) \{{.*?^\}}\n", ARCH.read_text(encoding="utf-8"), re.M | re.S).group(0)


def _run(tmp_path: Path, calls: str) -> str:
    manifest = tmp_path / "manifest.env"
    script = (f"MANIFEST_DIR={tmp_path}; MANIFEST={manifest}\n"
              + _fn("manifest_set") + _fn("manifest_owned") + calls)
    subprocess.run(["bash", "-c", script], check=True)
    return manifest.read_text()


def test_a_second_run_does_not_disown_what_the_first_created(tmp_path):
    first = "manifest_owned USER_CREATED 1\nmanifest_owned INPUT_GROUP_ADDED 1\n"
    rerun = "manifest_owned USER_CREATED 0\nmanifest_owned INPUT_GROUP_ADDED 0\n"
    text = _run(tmp_path, first + rerun)
    assert "USER_CREATED=1" in text and "INPUT_GROUP_ADDED=1" in text
    assert "=0" not in text


def test_what_predates_the_install_stays_not_ours(tmp_path):
    assert _run(tmp_path, "manifest_owned USER_CREATED 0\n").strip() == "USER_CREATED=0"
