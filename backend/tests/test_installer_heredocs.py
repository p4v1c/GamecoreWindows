"""No command runs from inside a file the installer writes.

An unquoted heredoc (`<<EOF`) expands `$(...)` and backticks. The sudoers file
arch.sh writes carried comments quoting `udevadm control` and
`desktop --restart-dm` between backticks: bash ran both, as root, during every
install, and wrote empty gaps where the words were. Variables are why those
heredocs stay unquoted, so the rule is on their bodies: no backtick.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "install" / "steps").glob("*.sh"))
OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]+)\1")


def _unquoted_bodies(path: Path):
    lines = path.read_text(encoding="utf-8").split("\n")
    i = 0
    while i < len(lines):
        m = OPEN.search(lines[i])
        if m and "<<<" not in lines[i]:
            j = i + 1
            while j < len(lines) and lines[j].strip() != m.group(2):
                j += 1
            if not m.group(1):
                yield from ((i + 2 + k, line) for k, line in enumerate(lines[i + 1:j]))
            i = j
        i += 1


def test_no_backtick_in_an_unquoted_heredoc():
    bad = [f"{p.relative_to(ROOT)}:{n}: {line.strip()}"
           for p in SCRIPTS if p.exists() for n, line in _unquoted_bodies(p) if "`" in line]
    assert bad == [], "backticks run as commands here:\n" + "\n".join(bad)
