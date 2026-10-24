"""A shipped theme that changed must say so in its version, or no box gets it.

`update/finish.py` replaces a bundled theme only when the release declares a
strictly newer `version` than the one already on the box — its own table says
"both, same or older → left alone". That rule is deliberate and right: it is
what stops an update from stamping on a theme somebody edited in place.

The cost is that editing a theme and forgetting the bump is **silent**. The
release builds, the CI is green, the files reach GitHub, and every installed box
keeps the old copy. Nothing anywhere reports it. That is exactly what happened
to the launch-ceremony and settings-menu fixes: shipped in v1.0.114, present in
the archive, and not on the box, because shelf still said 1.3.0 and summer still
said 1.1.0.

So the bump is checked here rather than remembered. The question asked is the
useful one — "did this theme's files change since the last release, and if so
did its version change too" — which is why it is anchored on the latest tag
rather than on the previous commit: a theme edited across five commits of one
release needs one bump, not five.

Skipped, never failed, when the answer cannot be known: no git, no tags, or a
shallow clone. A test that cannot see history must not invent a verdict.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
THEMES = ROOT / "config" / "themes"


def _git(*args):
    """(ok, stdout). Never raises: absence of git is a skip, not a failure."""
    try:
        r = subprocess.run(("git", "-C", str(ROOT)) + args,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return r.returncode == 0, r.stdout.strip()


def _shipped_themes():
    """Every directory the update loop ships — which is not the same set as the
    themes a player can choose.

    `_`-prefixed directories used to be excluded here, on the reasoning that
    `list_themes()` skips them so nobody runs them. That reasoning was about the
    PICKER and this test is about DELIVERY, and `update/finish.py` walks every
    directory under config/themes without caring about the prefix. The gap was
    not theoretical: `_shared/` held the settings screen both themes imported,
    its code was fixed, its version was not bumped, and the fix sat in the
    release while every box kept the broken copy — exactly the failure this file
    was written to make impossible, walking in through the one door left open.

    That directory is gone: the screen is host code now, shipped in the bundle,
    with no version for anyone to forget. The rule stays anyway. It cost two
    releases to learn and it costs nothing to keep, and the next `_`-prefixed
    directory somebody adds under config/themes will be delivered by the same
    loop for the same reason.

    Only `.`-prefixed directories stay out: `.prev/` is the updater's own
    snapshot of what it replaced, not something it ships.
    """
    if not THEMES.is_dir():
        return []
    return sorted(
        d.name for d in THEMES.iterdir()
        if d.is_dir() and not d.name.startswith(".")
        and (d / "theme.json").is_file()
    )


def _version_at(ref, theme):
    ok, out = _git("show", f"{ref}:config/themes/{theme}/theme.json")
    if not ok:
        return None                      # the theme did not exist at that tag
    try:
        return json.loads(out).get("version")
    except json.JSONDecodeError:
        return None


@pytest.fixture(scope="module")
def last_tag():
    ok, _ = _git("rev-parse", "--git-dir")
    if not ok:
        pytest.skip("not a git checkout")
    ok, tag = _git("describe", "--tags", "--abbrev=0")
    if not ok or not tag:
        pytest.skip("no release tag to compare against")
    return tag


@pytest.mark.parametrize("theme", _shipped_themes())
def test_a_changed_theme_declares_a_new_version(theme, last_tag):
    rel = f"config/themes/{theme}"
    # --quiet exits 1 when there is a difference. Anything else (a bad ref, a
    # shallow clone that cannot reach the tag) is unknown, not a failure.
    try:
        r = subprocess.run(
            ("git", "-C", str(ROOT), "diff", "--quiet", last_tag, "--", rel),
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git diff unavailable")
    if r.returncode not in (0, 1):
        pytest.skip(f"cannot diff {rel} against {last_tag}")
    if r.returncode == 0:
        return                            # untouched since the release

    was = _version_at(last_tag, theme)
    if was is None:
        return                            # new theme: nothing to bump from

    now = json.loads((THEMES / theme / "theme.json").read_text()).get("version")
    assert now != was, (
        f"{rel} changed since {last_tag} but theme.json still says version "
        f"{now!r}. update/finish.py only replaces a bundled theme when the "
        f"release declares a NEWER version, so this edit would reach GitHub "
        f"and never reach an installed box — silently, with a green build."
    )
