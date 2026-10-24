#!/usr/bin/env python3
"""Build the release archive: `gamecore-ota.zip` and its `.sha256`.

    usage:  package-release.py --version v0.1.0 [--out release]

The archive is the code root as install/steps/copy-code-root.ps1 lays it out,
so the same file serves an update (update/windows.py unpacks it beside the
code root and swaps) and a fresh install (unpack, run install\\install.ps1):

- every tracked file under the shipped directories, sources included: a box
  left with sources older than the build it runs rolls its UI back the first
  time anything rebuilds there;
- the build outputs, which are not tracked: `frontend/dist/` and the packed
  shell, `electron/dist/win-unpacked/` stored as `shell/`;
- `VERSION` holding the tag, not the tracked file (v0.0.0, a checkout).

Never in it: what the installer puts in the code root (Python, the venv,
Caddy), dependency trees, caches. Run from a checkout after `npm run build`
(frontend) and `npm run pack` (electron).
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ARCHIVE = "gamecore-ota.zip"
SHIPPED_DIRS = ("backend", "frontend", "electron", "config", "assets", "catalog",
                "scripts", "update", "install")
SHIPPED_FILES = ("LICENSE", "README.md", "CHANGELOG.md", "pyproject.toml")
SKIPPED_PARTS = {"node_modules", "__pycache__", ".pytest_cache"}
# Build output -> where it lives in the code root.
BUILT_TREES = (("frontend/dist", "frontend/dist"), ("electron/dist/win-unpacked", "shell"))


class PackageError(Exception):
    """The tree is not ready to be released."""


def tracked_files(source: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=source, capture_output=True, check=True)
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def _skipped(rel: str) -> bool:
    parts = rel.split("/")
    return bool(SKIPPED_PARTS.intersection(parts)) or rel.endswith(".pyc")


def collect(source: Path, tracked: list[str]) -> dict[str, Path]:
    """Archive name -> file on disk. Raises PackageError when a build output is missing."""
    entries: dict[str, Path] = {}
    for rel in tracked:
        top = rel.split("/", 1)[0]
        if ((top in SHIPPED_DIRS and "/" in rel) or rel in SHIPPED_FILES) and not _skipped(rel):
            if rel.startswith("electron/dist/") or rel.startswith("frontend/dist/"):
                continue
            path = source / rel
            if path.is_file():
                entries[rel] = path
    for built, target in BUILT_TREES:
        tree = source / built
        if not tree.is_dir():
            raise PackageError(f"{built} is missing: build the frontend and pack the shell first")
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            entries[f"{target}/{path.relative_to(tree).as_posix()}"] = path
    if "shell/GameCore.exe" not in entries:
        raise PackageError("electron/dist/win-unpacked has no GameCore.exe")
    return entries


def write_archive(entries: dict[str, Path], version: str, out: Path) -> Path:
    """The zip, then `<zip>.sha256` in the `sha256sum` format update/windows.py reads."""
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ARCHIVE
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in sorted(entries):
            z.write(entries[name], name)
        z.writestr("VERSION", version + "\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / (ARCHIVE + ".sha256")).write_text(f"{digest}  {ARCHIVE}\n", encoding="ascii", newline="\n")
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="the tag, e.g. v0.1.0")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=Path("release"))
    args = parser.parse_args(argv)
    try:
        entries = collect(args.source, tracked_files(args.source))
    except PackageError as e:
        print(f"package-release: {e}", file=sys.stderr)
        return 1
    archive = write_archive(entries, args.version, args.out)
    print(f"{archive}: {len(entries) + 1} files, {archive.stat().st_size // (1024 * 1024)} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
