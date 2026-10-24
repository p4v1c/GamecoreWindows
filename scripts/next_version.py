#!/usr/bin/env python3
"""The next release tag, under a bounded version scheme.

    usage:  next_version.py [previous-tag]

Reads the previous tag from the argument, or from `git describe` when none is
given, and prints the tag the next release should carry. With no tag at all,
that is the first release of the Windows edition, `v0.1.0` (dev log D2).

## The scheme

Three fields, each with a ceiling: **major ≤ 99, minor ≤ 9, patch ≤ 99**, so the
highest version this project can express is `99.9.99`. Every release bumps the
patch; when a field would pass its ceiling it carries into the one above, the
way an odometer does.

The point is that a version stays four or five characters wide and stays
readable on a television at three metres. `v1.0.182` was heading for `v1.0.1000`
— a number nobody can compare at a glance, in a UI where the version is a line
in a settings screen rather than something you look up.

## Ordering is preserved, which is the part that matters

Every consumer of these numbers compares them as a tuple of three integers —
`routers/update.py:_version_int`, `update/windows.py:version_key`, the theme delivery
loop. A carry keeps that ordering: `1.0.182` normalises to `1.1.82`, and
`(1, 1, 82) > (1, 0, 182)` because the comparison is left to right, not by
magnitude of the last field. So a box running the old numbering still sees the
first new-scheme release as an update, with no migration and no special case.

## The first run migrates by itself

`next()` normalises the previous tag before bumping. When normalising alone
already produces a greater version — which is exactly the case for a tag left
over from the unbounded scheme — that is the next tag, and no bump is added on
top. `v1.0.182` therefore becomes `v1.1.82` rather than `v1.1.83`, and every
run after that behaves ordinarily.

## Overflow is refused, not wrapped

At `99.9.99` there is no next version, and this exits non-zero rather than
inventing `100.0.0` — a number the scheme cannot express, that would sort below
everything after a two-digit parse, and that would silently stop every box in
the field from ever seeing another update. It is roughly 100 000 releases away;
the check costs nothing and the failure mode it prevents is the worst one here.
"""
from __future__ import annotations

import re
import subprocess
import sys

#: major, minor, patch — the highest version this scheme can express.
CEILING = (99, 9, 99)
#: What each field carries at. One more than its ceiling.
BASE = (100, 10, 100)

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
#: The first release when the repository has no tag yet (dev log D1, D2).
FIRST_VERSION = "v0.1.0"


def parse(tag: str) -> tuple[int, int, int]:
    """`v1.0.182` → `(1, 0, 182)`. Raises on anything else.

    Deliberately strict, unlike the readers in `routers/update.py` and
    `update/windows.py`: those parse whatever GitHub hands them and must never
    raise on a malformed remote tag. This one names the tag this repository is
    about to create, and a version nobody meant is worth stopping for.
    """
    m = _TAG_RE.match((tag or "").strip())
    if not m:
        raise ValueError(f"not a version tag: {tag!r}")
    return tuple(int(g) for g in m.groups())          # type: ignore[return-value]


def normalise(v: tuple[int, int, int]) -> tuple[int, int, int]:
    """Carry each field into the one above it until all are within range."""
    major, minor, patch = v
    minor += patch // BASE[2]
    patch %= BASE[2]
    major += minor // BASE[1]
    minor %= BASE[1]
    if major > CEILING[0]:
        raise OverflowError(
            f"{major}.{minor}.{patch} is past {'.'.join(map(str, CEILING))} — "
            "this scheme has no next version"
        )
    return (major, minor, patch)


def next_version(previous: str) -> str:
    """The tag the next release should carry."""
    prev = parse(previous)
    carried = normalise(prev)
    # A tag left over from the unbounded scheme: normalising is already a step
    # forward, so it IS the next version. Bumping on top would skip a number
    # for no reason and make the migration harder to read.
    if carried > prev:
        return "v%d.%d.%d" % carried
    return "v%d.%d.%d" % normalise((prev[0], prev[1], prev[2] + 1))


def _latest_tag() -> str | None:
    """The newest tag reachable from HEAD; None when there is none.

    `git describe` rather than the API: this runs in CI right after a checkout
    with `fetch-depth: 0`, so the tags are local, and asking GitHub would make
    the version depend on a network call that can rate-limit.
    """
    out = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


def main(argv: list[str]) -> int:
    try:
        previous = argv[1] if len(argv) > 1 else _latest_tag()
        print(next_version(previous) if previous else FIRST_VERSION)
    except (ValueError, OverflowError) as e:
        print(f"next_version: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
