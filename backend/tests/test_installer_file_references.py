"""Every file an installer copies must exist where the installer looks.

The failure this exists to stop, in the order it happened:

  1. `refactor(install): un dossier par nature de fichier` moved
     `gamecore-restart.service` into `install/system/` and `gamecore-emu` into
     `install/bin/`.
  2. `install/steps/setup-update-permissions.sh` kept looking for both beside
     itself, as `${HERE}/…`.
  3. It has no `set -e`, so the failed `install` printed one line and carried
     on, wrote a sudoers file holding only half its rules, and **exited 0**.
  4. `arch.sh` runs it as `… && ok "OTA restart permissions installed."`, so
     the installer announced success.

Result: every box built after that commit came up with no
`gamecore-restart.service` — the OTA cannot restart itself — and no
`/usr/local/bin/gamecore-emu`, so "install an emulator" from the Systems screen
does nothing at all. Both reported as installed.

Nothing could see it. The scripts are not executed by CI, shellcheck does not
resolve paths, and the only symptom appears on a fresh box weeks later. So the
references are resolved statically here instead.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Every shell step that copies files into place. setup-update-permissions.sh
# is the Linux OTA's and still names the console-session helpers that left with
# port step 7; it is checked by the step-8 tests below until it goes too.
PERMISSIONS_STEP = REPO / "install/steps/setup-update-permissions.sh"
INSTALLERS = sorted(p for p in (REPO / "install/steps").glob("*.sh") if p != PERMISSIONS_STEP)

# `install -m 644 "$SRC" /etc/…` and `cp … "$SRC" …`: the source is what has to
# exist. Only variables the script itself defines are resolvable, so the check
# is deliberately limited to those — a path built from $GAMECORE_PATH refers to
# the deployed tree, not this checkout, and is not this test's business.
_ASSIGN_RE = re.compile(r'^\s*([A-Z_][A-Z0-9_]*)="([^"]*)"', re.M)
# `install(1)` as the COMMAND of a line, and its first quoted argument.
#
# Two mistakes are pinned into this pattern, both of which made the file check
# nothing while passing:
#   · options carry values (`-m 644`, `-o root`), so a pattern skipping only
#     `-flag` shapes stopped at the `644` and matched nothing at all;
#   · unanchored and spanning newlines, it matched `gamecore-provider.py
#     install \` continued onto `--select "$EMULATORS"` — where `install` is a
#     SUBCOMMAND and the quoted value is not a path.
# Anchoring to the start of a line answers both.
_SRC_RE = re.compile(r'^\s*install\b(?:\s+[^"\s]+)*\s+"([^"]+)"')


def _sources(script: Path) -> list[str]:
    """The copy sources in a script, comments excluded.

    Comments are stripped first: `setup-update-permissions.sh` explains that
    there is "deliberately no `install from this URL` anywhere in the chain",
    and prose about installing is not a file. A source must also look like a
    path — a variable reference or something containing a separator.
    """
    out = []
    for line in script.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*install\s+-d\b", line):
            continue  # directory creation has no source file
        m = _SRC_RE.match(line)
        if m and (m.group(1).startswith("$") or "/" in m.group(1)):
            out.append(m.group(1))
    return out


def _resolve(script: Path, raw: str) -> Path | None:
    """Expand a source path against the variables the script sets itself."""
    text = script.read_text(encoding="utf-8")
    variables = dict(_ASSIGN_RE.findall(text))
    # The two anchors every step computes from its own location, by command
    # substitution — which the assignment regex cannot read, so they are set
    # here and must WIN over anything it thought it captured.
    variables["HERE"] = str(script.parent)
    variables["INSTALL_ROOT"] = str(script.parent.parent)

    value = raw
    for _ in range(6):
        before = value
        # A value may itself be written in terms of another variable
        # (UNIT_SRC="${INSTALL_ROOT}/system/…"), so substitution repeats until
        # it settles. Skipping any value that starts with `$` — as this did at
        # first — makes exactly those references unresolvable, and the test
        # silently checks nothing: it passed against the very paths whose
        # breakage it was written for.
        for name, replacement in variables.items():
            value = value.replace(f"${{{name}}}", replacement).replace(f"${name}", replacement)
        if value == before:
            break
    if "$" in value:                        # runtime-only (GAMECORE_PATH, HOME…)
        return None
    return Path(value)


@pytest.mark.parametrize("script", INSTALLERS, ids=lambda p: p.name)
def test_every_file_the_installer_copies_exists(script):
    """`install "$SRC" <dest>` with a source this checkout does not have."""
    missing = []
    for raw in _sources(script):
        resolved = _resolve(script, raw)
        if resolved is not None and not resolved.exists():
            missing.append(f"{raw} → {resolved}")
    assert missing == [], (
        f"{script.relative_to(REPO)} copies files that are not there:\n  "
        + "\n  ".join(missing))


@pytest.mark.linux_until_port(8)
def test_the_permissions_step_installs_both_of_its_artefacts():
    """The two specific files, pinned by name.

    The generic check above only proves the paths resolve. These two are what
    the step exists FOR: without the unit an OTA cannot restart the services,
    and without the CLI the catalogue screen's install button cannot work.
    """
    step = REPO / "install/steps/setup-update-permissions.sh"
    text = step.read_text(encoding="utf-8")

    for name in ("gamecore-restart.service", "gamecore-emu"):
        assert name in text, f"{step.name} no longer installs {name}"

    for raw in _sources(step):
        resolved = _resolve(step, raw)
        assert resolved is not None and resolved.is_file(), f"{raw} does not resolve"


def test_a_failed_install_is_fatal_rather_than_reported_as_success():
    """arch.sh reads this step's exit code as the truth.

    It runs it as `… && ok "OTA restart permissions installed."`. Exiting 0
    after a failed copy is what turned a broken step into a green tick, so the
    step must stop instead of carrying on.
    """
    text = (REPO / "install/steps/setup-update-permissions.sh").read_text(encoding="utf-8")
    unit_install = next(
        (ln for ln in text.splitlines() if "install -m 644" in ln), "")
    assert unit_install, "the unit is no longer installed at all"
    guarded = "||" in unit_install or "\\" in unit_install or "set -e" in text
    assert guarded, (
        "a failed `install` of the unit no longer stops the step — arch.sh "
        "would report success for a box with no gamecore-restart.service")
