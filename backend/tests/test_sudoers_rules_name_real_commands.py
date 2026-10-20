"""Every command a sudoers rule grants must be one this repo actually runs.

`visudo -cf` is the only check the installers had, and it validates SYNTAX. A
rule can parse perfectly and still match nothing, because sudo compares a
command line ARGUMENT BY ARGUMENT: one stray space splits an argument in two
and the rule silently stops applying to anything.

The reference box carries exactly that, granting

    /usr/bin/systemctl is-active gamecore backend.service

— two arguments where the code passes one, `gamecore-backend.service`. Its
`-ui` twin is correct, so half the pair worked and the other half never did.
It fails closed, which is why nobody noticed: sudo simply refuses, and a
refusal is indistinguishable from a feature nobody used.

**No rule in this repository writes that line** (see the phase report), so this
guard is not what would have caught it — nothing in-tree produced it. It exists
because the class is invisible to `visudo`, and the next such rule may well be
written here. What it checks: every argument a rule grants has to appear, as a
whole token, in something this repo actually executes. `backend.service` occurs
nowhere outside `gamecore-backend.service`, and that is the whole tell.

Documentation is deliberately not evidence — a rule quoted in a README is not a
call site — and neither is another sudoers line, or a rule would justify itself.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# sudoers is the Linux installer's (`arch.sh`, removed at port step 7). The
# Windows shell no longer runs `sudo systemctl poweroff|reboot`, so the rule
# granting them is already orphaned on this tree.
pytestmark = pytest.mark.linux_until_port(7)

# The granted command list of a sudoers rule: everything after NOPASSWD:, up to
# the end of the line or the quote the installer wrapped it in.
NOPASSWD_RE = re.compile(r"NOPASSWD:\s*(?P<spec>[^\"'\n]+)")

# Prose, not call sites. A rule documented in a README must not vouch for itself.
DOC_SUFFIXES = {".md", ".rst", ".txt", ".json"}

# Shell and template variables — `$USER_NAME`, `${GC_USER}` — name the operator,
# not a command argument, and there is nothing in the tree for them to match.
VARIABLE_RE = re.compile(r"[$@]")


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [ROOT / line for line in out.stdout.splitlines() if line]


def _rules() -> list[tuple[Path, str]]:
    """(file, command spec) for every NOPASSWD rule this repo writes."""
    found = []
    for path in _tracked():
        if path.suffix in DOC_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in NOPASSWD_RE.finditer(text):
            spec = match.group("spec").strip()
            # A rule GRANTS a command, so it starts with an absolute path or
            # with the variable holding one. Everything else mentioning
            # NOPASSWD is machinery that reads rules rather than writing them
            # — update/linux.sh greps `sudo -n -l` for drift, and its search
            # patterns would otherwise be parsed as rules of their own.
            if not (spec.startswith("/") or spec.startswith("$")):
                continue
            found.append((path, spec))
    return found


def _code_tokens() -> set[str]:
    """Every whole word in everything this repo executes.

    Lines granting sudo rights are skipped: the point is to find the CALL, and
    a rule that matched only itself would make this test pass by construction.
    """
    tokens: set[str] = set()
    word = re.compile(r"[A-Za-z0-9._/-]+")
    for path in _tracked():
        if path.suffix in DOC_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "NOPASSWD" in line:
                continue
            tokens.update(word.findall(line))
    return tokens


def _commands(spec: str) -> list[list[str]]:
    """A rule's comma-separated commands, each split into its arguments."""
    return [cmd.split() for cmd in spec.split(",") if cmd.split()]


def test_there_are_rules_to_check():
    """Guards the guard: a parser that silently matches nothing passes anything."""
    rules = _rules()
    assert len(rules) >= 4, f"only found {rules} — the rule parser stopped working"


@pytest.mark.parametrize("path, spec", _rules(),
                         ids=lambda v: v.name if isinstance(v, Path) else v[:40])
def test_every_granted_argument_exists_somewhere_in_the_code(path, spec):
    """Each argument of each granted command must be a token the code uses.

    This is the check `visudo` cannot make. A unit name split by a stray space
    leaves an orphan — `backend.service` — that appears nowhere on its own,
    while the real `gamecore-backend.service` appears in the launcher, the
    installer and the update script.
    """
    tokens = _code_tokens()
    for command in _commands(spec):
        for argument in command[1:]:
            if argument.startswith("-") or VARIABLE_RE.search(argument):
                continue            # a flag, or the operator's name
            assert argument in tokens, (
                f"{path.relative_to(ROOT)} grants `{' '.join(command)}`, but "
                f"nothing in this repo ever runs `{argument}` as an argument. "
                "sudo matches argument by argument, so this rule can never "
                "apply — check for a space where a hyphen belongs.")
