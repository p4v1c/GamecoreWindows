#!/usr/bin/env python3
"""Every repo path the documentation names must still exist — or be declared.

This does NOT check that the docs are *right*. No script can: a document can
name every file correctly and still explain the system backwards. What it
catches is the failure mode that actually happens here — a file gets renamed or
moved, and the document naming it keeps saying the old thing, confidently, for
months. Doc rot is silent and the rest of the baseline is blind to it.

Two things are verified:

  1. inline-code tokens that look like repo paths (`backend/services/paths.py`,
     with an optional `:123` or `:symbol()` citation suffix) resolve on disk;
  2. relative Markdown links between documents resolve.

The allowlists below are the interesting part
----------------------------------------------
The first version of this script flagged 24 references and **every single one
was a false positive**, in two distinct ways. Both are worth naming, because
both are things this repository does on purpose:

Existence is resolved against `git ls-files`, **not against the disk.** The first
version asked the filesystem and so passed on this machine and failed in CI:
`frontend/dist` and `lib/` exist once you have built and installed, and exist
nowhere in a fresh clone. A checker whose answer depends on which machine runs it
reports the machine, not the docs.

`NOT_IN_GIT` / `RUNTIME_GENERATED` — build output, and the box's own state.
`config/` is the box's identity: it is not in git and several of its files are
written on first boot, so a checkout legitimately has no `config/auth_secret`.
Skipping all of `config/` would have been the easy fix and the wrong one: then a
document could invent `config/setttings.json` and nothing would notice. So each
is listed by name, and a new one has to be added deliberately.

`HISTORICAL` — this repo documents *what failure produced a rule*, which means
its best paragraphs name files that no longer exist:

    "a refactor deleted `install/firefox-profiles/` while `arch.sh` still
     read it. That install died at 66 %, on a fresh machine, months later."

That sentence is correct precisely because the directory is gone. A checker that
forces such prose to be deleted would strip the documentation of the only part
that cannot be recovered by reading the code. So those paths are listed too,
with the reason.

`NOT_YET` — a file a document names *because it is missing*: the catalogue
signing key, whose absence is what keeps the update channel off. Delete the entry
when the file lands, and the normal check takes over — the doc is then verified
rather than excused.

If you add an entry to any of the three, say why. A list of bare paths becomes a
place to silence this script, which is the opposite of the point.

Run:  python3 scripts/check-docs.py [--all]
      --all also scans docs/*.md, not just docs/architecture/.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]

# A token only counts as a repo path if it starts with one of these. Anything
# else is prose that happens to contain a slash.
TOP_LEVEL = {
    "assets", "backend", "catalog", "config", "distribution", "docs",
    "electron", "frontend", "install", "lib", "scripts", "update",
}

# Not in git: build output, or written on a real box. See the module docstring
# for why these are listed by name rather than skipped by directory.
NOT_IN_GIT = {
    # Build output. The docs name it a lot, and the OTA discussion turns on the
    # difference between shipping it and shipping frontend/ whole.
    "frontend/dist",
    # `/lib/` is gitignored. lib/xenia is downloaded by the full installer, and
    # is the subject of issue #36 — the one data directory inside the code root.
    "lib",
    "lib/xenia",
}

# Written on a box at runtime — a subset of the above, kept separate because
# these are box *state* rather than build output.
RUNTIME_GENERATED = {
    "config/auth.json",       # shared-password hash, written on first setup
    "config/auth_secret",     # session-signing secret, 0600
    "config/session.json",    # which session the box boots into
    "config/theme.json",      # the selected theme
    "config/standby.json",    # sleep/wake schedule
    "config/addons.json",     # installed addons registry
    "config/display.json",    # the confirmed display mode, written by /confirm
    "config/ui-scale.json",   # the interface zoom chosen in Display
    "config/profiles/profiles.json",  # who plays on this box
}

# Named on purpose although not there YET: a document describing how to turn a
# feature on has to name the file whose absence keeps it off. Delete the entry
# when the file lands — at that point the normal check takes over and the doc is
# verified rather than excused.
NOT_YET = {
    # The Ed25519 public key for the catalogue update channel. Its absence is
    # what keeps the channel off, and 10-catalog-and-install.md says so.
    "catalog/_ota/catalog-signing.pub",
}

# Named on purpose although gone — the failure they describe is the point.
HISTORICAL = {
    # Deleted by a refactor while install/arch.sh still read it; the install
    # died at 66 % on a fresh machine, months later. It is the reason
    # test_installer_applier.py exists, and 10/TESTING both tell that story.
    "install/firefox-profiles/",
    # The Linux installer and console session, removed at port step 7
    # (install/install.ps1 replaced them). The architecture docs still describe
    # the Linux box's mechanisms they set up, as history and for the import.
    "install/arch.sh",
    "install/uninstall.sh",
    "install/steps/setup-gamecore-session.sh",
    # The Linux OTA, replaced at port step 8 by update/windows.py and
    # update/apply-update.ps1. Docs still cite it for the Linux box's history
    # (rsync excludes, mtimes, the security notes) and for the import.
    "update/linux.sh",
}

INLINE_CODE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# `paths.py:111` and `main.py:lifespan()` are citations, not part of the path.
CITATION_SUFFIX = re.compile(r":(\d+(-\d+)?|[A-Za-z_][\w.]*\(\))$")

# `<id>`, `@GAMECORE_PATH@`, `logos|overlays`, globs: a shape, not a file.
PLACEHOLDER = re.compile(r"[<>@*?\[\]{}|\s]")


def is_candidate_path(tok: str) -> bool:
    if PLACEHOLDER.search(tok):
        return False
    if tok.startswith(("/", "~", "http://", "https://", ".venv")):
        return False
    if "/" not in tok:
        return False
    return tok.split("/", 1)[0] in TOP_LEVEL


def _tracked() -> set[str]:
    """Every path git tracks, plus every directory implied by one.

    **Resolved against git, not against the disk, and that is the whole point.**
    The first version of this script asked the filesystem, so it passed here and
    failed in CI: `frontend/dist` and `lib/` exist on a machine that has built
    and installed, and exist nowhere in a fresh clone. A checker whose answer
    depends on which machine runs it does not check anything — it reports the
    machine. Asking git makes the answer identical everywhere.
    """
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    paths: set[str] = set()
    for line in out.splitlines():
        paths.add(line)
        # Posix: git prints `/`, and a Windows Path would join parents with `\`.
        parent = PurePosixPath(line).parent
        while str(parent) != ".":
            paths.add(str(parent))
            parent = parent.parent
    return paths


TRACKED = _tracked()


def exists(clean: str) -> bool:
    # `backend/config` names the module; backend/config.py is the file.
    return clean in TRACKED or f"{clean}.py" in TRACKED


def _excused(tok: str) -> bool:
    return (tok in NOT_IN_GIT or tok in RUNTIME_GENERATED
            or tok in HISTORICAL or tok in NOT_YET)


def check_file(md: Path) -> list[str]:
    problems: list[str] = []
    text = md.read_text(encoding="utf-8")
    rel_md = md.relative_to(ROOT)

    for lineno, line in enumerate(text.splitlines(), 1):
        for tok in INLINE_CODE.findall(line):
            tok = tok.strip()
            if not is_candidate_path(tok):
                continue
            if _excused(tok):
                continue
            clean = CITATION_SUFFIX.sub("", tok).rstrip("/")
            if _excused(clean):
                continue
            if not exists(clean):
                problems.append(f"{rel_md}:{lineno}: `{tok}` does not exist")

        for target in LINK.findall(line):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).exists():
                problems.append(f"{rel_md}:{lineno}: link -> {target} is dead")

    return problems


def main() -> int:
    scan_all = "--all" in sys.argv
    docs = sorted((ROOT / "docs" / "architecture").glob("*.md"))
    if scan_all:
        docs += sorted((ROOT / "docs").glob("*.md"))

    problems: list[str] = []
    for md in docs:
        problems += check_file(md)

    if problems:
        print(f"check-docs: {len(problems)} stale reference(s)\n")
        for p in problems:
            print("  " + p)
        print("\nIf a path is absent on purpose and the prose is about that,")
        print("add it to HISTORICAL or NOT_YET in this script, with the reason.")
        return 1

    print(f"check-docs: {len(docs)} document(s), every path and link resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
