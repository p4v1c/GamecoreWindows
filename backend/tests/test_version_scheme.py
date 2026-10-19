"""The bounded version scheme: major ≤ 99, minor ≤ 9, patch ≤ 99.

Two things are tested, and the second is the one that would go wrong quietly.

  1. The arithmetic — carries, the migration step, and the refusal at the top.
  2. **That ordering survives a carry.** Every consumer of these numbers
     compares three integers left to right: `routers/update.py:_version_int`,
     `key()` in `update/linux.sh`, and the theme delivery loop. A carry turns
     `1.0.182` into `1.1.82`, whose last field is *smaller* — and if any of
     those comparisons treated the version as one number, every box in the
     field would decide it was already up to date and never take another
     update. That is the failure this file exists to make impossible.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from next_version import CEILING, next_version, normalise, parse   # noqa: E402

from backend.routers.update import _version_int                    # noqa: E402


# ── the arithmetic ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("prev,expected", [
    # The migration: a tag from the unbounded scheme normalises into range, and
    # that alone is the step forward — no bump on top of it.
    ("v1.0.182", "v1.1.82"),
    # Ordinary.
    ("v1.1.82", "v1.1.83"),
    # Patch carries into minor.
    ("v1.1.99", "v1.2.0"),
    # And minor into major.
    ("v1.9.99", "v2.0.0"),
    ("v0.0.99", "v0.1.0"),
    # A theme version that was already past the minor ceiling.
    ("v2.12.0", "v3.2.0"),
    ("v99.9.98", "v99.9.99"),
])
def test_the_next_tag(prev, expected):
    assert next_version(prev) == expected


def test_a_normalised_tag_is_bumped_rather_than_repeated():
    """Normalising is only a step when it actually moves. Twice in a row must
    not hand out the same tag — a release that reuses its predecessor's number
    overwrites it."""
    once = next_version("v1.0.182")
    assert next_version(once) != once


def test_there_is_no_version_after_the_ceiling():
    """Refused, not wrapped. `100.0.0` is not expressible here, and a two-digit
    parse would sort it below everything — which would stop every box in the
    field from ever seeing an update again."""
    with pytest.raises(OverflowError):
        next_version("v%d.%d.%d" % CEILING)


def test_a_tag_nobody_meant_stops_the_release():
    """Strict on purpose, unlike the readers that parse GitHub's answers: this
    one names the tag this repository is about to create."""
    for bad in ("", "1.0", "v1.0.0-rc1", "latest", "v1.0.0.1"):
        with pytest.raises(ValueError):
            parse(bad)


# ── ordering, which the carry must not break ────────────────────────────────

def test_a_carry_still_reads_as_newer_to_the_updater():
    """`1.1.82` has a smaller patch than `1.0.182` and must still win.

    Asserted against the real comparator the box uses, not a re-implementation
    of it: that function is what decides whether an installed machine offers
    the update at all.
    """
    assert _version_int("v1.1.82") > _version_int("v1.0.182")


def test_every_step_of_a_long_run_is_strictly_increasing():
    """Walked across three carries, including a minor rollover, because a carry
    is exactly where an ordering bug would hide."""
    tag = "v1.0.182"
    seen = [tag]
    for _ in range(400):
        tag = next_version(tag)
        assert _version_int(tag) > _version_int(seen[-1]), (
            f"{tag} does not read as newer than {seen[-1]}"
        )
        seen.append(tag)
    # 400 releases from 1.1.82 crosses into 1.5.x — several carries, not one.
    assert parse(seen[-1])[1] > parse(seen[0])[1]


def test_the_shell_updater_agrees_with_the_backend():
    """`update/linux.sh` carries its own comparator. Two implementations of
    "is this newer" that disagree is a box that takes an update the API says it
    does not need, or refuses one it does."""
    src = (REPO / "update" / "linux.sh").read_text()
    m = re.search(r'def key\(v\):\n(.*?)\nsys\.exit', src, re.S)
    assert m, "the version key function moved in update/linux.sh"
    ns: dict = {"re": re}
    exec("import re\ndef key(v):\n" + m.group(1).split("def key(v):\n")[-1], ns)
    assert ns["key"]("1.1.82") > ns["key"]("1.0.182")


# ── nothing in the repository declares a version outside the scheme ─────────

def _declared_versions() -> list[tuple[str, str]]:
    out = [("VERSION", (REPO / "VERSION").read_text().strip())]
    for manifest in sorted((REPO / "config" / "themes").glob("*/theme.json")):
        try:
            v = json.loads(manifest.read_text()).get("version")
        except json.JSONDecodeError:
            continue
        if v:
            out.append((f"themes/{manifest.parent.name}", v))
    return out


@pytest.mark.parametrize("where,version", _declared_versions(),
                         ids=lambda x: x if isinstance(x, str) else "")
def test_a_declared_version_fits_the_scheme(where, version):
    """One rule for the whole repository, themes included.

    A theme's version is not cosmetic: `update/linux.sh` compares it to decide
    whether to deliver the directory at all, so it is the same kind of number
    as the release tag and lives under the same ceiling.
    """
    fields = parse(version)
    # Field by field, NOT as a tuple: `(2, 12, 0) <= (99, 9, 99)` is true —
    # tuple comparison stops at the first element and 2 < 99 settles it, so a
    # minor of 12 would pass a ceiling check written the obvious way.
    over = [f"{n}={v} (max {c})"
            for n, v, c in zip(("major", "minor", "patch"), fields, CEILING) if v > c]
    assert not over, f"{where} declares {version}: " + ", ".join(over)
    major, minor, patch = fields
    assert (major, minor, patch) == normalise((major, minor, patch)), (
        f"{where} declares {version}, which is not in normal form"
    )


# ── the release pipeline can use it ─────────────────────────────────────────

def test_the_script_is_shipped_to_the_runner():
    """It is invoked from the repository checkout, so it has to be tracked."""
    assert (REPO / "scripts" / "next_version.py").is_file()
