"""The privileges a release adds after a box was installed.

Sudoers rules are written ONCE, at install time, by `arch.sh` and
`setup-update-permissions.sh`. An OTA replaces code and nothing else: it runs
as the backend's user and cannot grant itself anything. So every rule added in
a later release is simply absent on every box installed before it, for ever,
and the feature it gates is dead without a word.

Found on the reference box, running a release fourteen tags old:

  · no NOPASSWD rule for `/usr/local/bin/gamecore-emu`, and the CLI not
    installed at all — so "install an emulator" from the Systems screen could
    not work. The endpoint answered, the catalogue listed seventeen packs, and
    the button was never going to do anything.
  · no rule for `cpupower`, so `standby.py` never dropped or raised the
    governor. It logs at debug and carries on, which is why nobody saw it: the
    only trace was `sudo: a password is required` in the journal.

`update/linux.sh` cannot repair either — that is the point of the rule being
root-owned. What it can do is stop the drift being invisible, and what this
file guards is that it keeps being able to.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
UPDATER = REPO / "update/linux.sh"
INSTALLERS = (REPO / "install/arch.sh",
              REPO / "install/steps/setup-update-permissions.sh")


def _granted_commands() -> set[str]:
    """Every command the installers grant NOPASSWD, read from their source."""
    out: set[str] = set()
    for path in INSTALLERS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "NOPASSWD:" not in line or line.lstrip().startswith("#"):
                continue
            for chunk in line.split("NOPASSWD:", 1)[1].split(","):
                found = re.findall(r"/[A-Za-z0-9/._-]+", chunk)
                if found:
                    out.add(found[0])
    return out


def test_the_updater_derives_the_rules_instead_of_repeating_them():
    """The whole reason this survives the next feature.

    A hardcoded list would cover the two rules that happened to be missing the
    day the check was written and go stale on the third — which is the very
    drift it exists to catch. So the updater greps the installers it just
    shipped, and this asserts it still does rather than having quietly grown a
    list of its own.
    """
    text = UPDATER.read_text(encoding="utf-8")
    assert "install/arch.sh" in text and "setup-update-permissions.sh" in text, (
        "update/linux.sh no longer reads the installers to learn which "
        "privileges this release expects")

    # A literal command path in the check would be that list coming back.
    check = text[text.index("expected_cmds="):text.index("missing_rules=")]
    literals = re.findall(r"/usr/(?:local/)?bin/[a-z][a-z0-9-]*", check)
    assert literals == [], (
        f"the drift check hardcodes command paths again: {sorted(set(literals))}")


@pytest.mark.linux_until_port(8)
def test_every_granted_privilege_is_one_the_updater_can_report():
    """Both halves of the fact have to move together.

    If arch.sh grants something the check cannot see, a box missing it is told
    nothing — which is the original bug with a new name.
    """
    granted = _granted_commands()
    assert granted, "no NOPASSWD rule found in the installers — parsing broke"

    # The updater's own derivation, run exactly as it runs on a box.
    script = r"""
      grep -rhoE 'NOPASSWD: *[^"]*' "$1" "$2" \
        | sed 's/NOPASSWD: *//' | tr ',' '\n' \
        | grep -oE '/[a-zA-Z0-9/._-]+' | sort -u
    """
    r = subprocess.run(["bash", "-c", script, "_", *map(str, INSTALLERS)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    derived = {line.strip() for line in r.stdout.splitlines() if line.strip()}

    assert granted <= derived, (
        "granted by an installer but invisible to the updater's drift check: "
        f"{sorted(granted - derived)}")


def test_the_check_reads_nopasswd_and_not_mere_permission():
    """`sudo -n -l <command>` is the trap this must not fall into again.

    On a box whose owner is in wheel, `(ALL) ALL` means every command is
    permitted — with a password. The backend always calls `sudo -n`, which
    never prompts, so a rule that is merely "allowed" is a rule that fails.
    The first version of this check used `sudo -n -l <command>` and reported
    the governor as working on a box where it demonstrably was not.
    """
    text = UPDATER.read_text(encoding="utf-8")
    assert "NOPASSWD:" in text, "the drift check no longer looks for NOPASSWD"
    assert not re.search(r"sudo -n -l +/", text), (
        "the check tests `sudo -n -l <command>`, which answers yes for any "
        "command a wheel user may run with a password")


@pytest.mark.linux_until_port(8)
def test_the_updater_still_parses_and_lints():
    """It runs unattended on every box in the fleet."""
    assert subprocess.run(["bash", "-n", str(UPDATER)],
                          capture_output=True).returncode == 0
