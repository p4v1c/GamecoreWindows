r"""Make a Linux box's data root (`/userdata`) the data root of GameCore for Windows.

    scripts\import-linux-data.py D:\userdata --to D:\GameCoreData            # plan only
    scripts\import-linux-data.py D:\userdata --to D:\GameCoreData --apply    # copy

Run it with the installed venv (`<code>\.venv\Scripts\python.exe`) or a
checkout's. The source is the Linux tree as Windows sees it: the NTFS data
partition the box used, or a copy of it.

Rules, each one there so that nothing is lost:

  * plan by default; `--apply` copies. The source is only ever read;
  * the layout is the same on both systems (`backend/services/paths.py`), so
    ROMs, saves beside them, covers, scraped media, the LaunchBox index,
    profiles, per-game settings and `playtime.db` keep their relative path;
  * a file that already exists in the destination is never overwritten, and
    what the Windows edition cannot use (Flatpak launchers, Linux addons,
    Linux logs) is not made live: both go to `<DEST>\linux-import\` instead;
  * a name Windows refuses is renamed, a link is not followed, an unreadable
    file is skipped; each is listed in the report, which `--apply` also writes
    to `<DEST>\logs\import\`.

Point the installer at the destination afterwards
(`install\install.ps1 -DataRoot <DEST>`). Nothing calls this script: it is a
command a human types once.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.paths import _LAYOUT  # noqa: E402
from backend.utils import atomic_write_json, fmt_size  # noqa: E402

ASIDE_DIR = "linux-import"
# Linux-only state: carried over, never made live. Relative to the data root.
ASIDE = {
    "config/systems.json": "the Linux packs' tiles (Flatpak launchers); Windows packs bring their own",
    "config/apps.json": "the Linux packs' app tiles; Windows packs bring their own",
    "config/session.json": "a Linux game session (process group); meaningless on Windows",
    "config/addons.json": "Linux addons (systemd units); reinstall them with gamecore-addon",
    "config/catalog.d": "local overrides of the Linux packs",
    "config/controller-autoconfig.json": "controller bindings keyed by Linux SDL GUIDs",
    "addons": "Linux addon state and venvs; reinstall the addons, then copy what you need",
    "logs": "Linux logs",
    "volumes": "links to Linux mount points",
    "emu/lutris": "Lutris launchers written on Linux",
}
SOURCE_MARKERS = (_LAYOUT["config"], _LAYOUT["roms"])
PLAYTIME_DB = f"{_LAYOUT['config']}/playtime.db"
REPORT_SAMPLE = 10
LINUX_PATH_PREFIXES = ("/home/", "/opt/", "/usr/", "/var/", "/etc/", "/run/", "/mnt/", "/media/")
WINDOWS_BAD_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


def windows_name(name: str) -> str:
    """`name` as Windows accepts it: bad characters to `_`, no trailing dot or space, no device name."""
    fixed = "".join("_" if c in WINDOWS_BAD_CHARS else c for c in name).rstrip(" .") or "_"
    stem, dot, ext = fixed.partition(".")
    if stem.upper() in WINDOWS_RESERVED:
        fixed = f"{stem}_{dot}{ext}"
    return fixed


def long_path(path: Path) -> str:
    r"""`\\?\` form: a ROM folder plus a long game name passes 260 characters."""
    text = str(path.absolute())
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


@dataclass
class Plan:
    copies: list[tuple[str, str]] = field(default_factory=list)   # (source rel, destination rel)
    bytes: int = 0
    aside: dict[str, str] = field(default_factory=dict)           # source rel -> reason
    renamed: list[tuple[str, str]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def _aside_reason(rel: str) -> str | None:
    for prefix, reason in ASIDE.items():
        if rel == prefix or rel.startswith(prefix + "/"):
            return reason
    return None


def _is_link(entry: os.DirEntry) -> bool:
    return entry.is_symlink() or bool(getattr(entry, "is_junction", lambda: False)())


def _walk(source: Path, rel: PurePosixPath, out_rel: PurePosixPath, plan: Plan, dest: Path) -> None:
    try:
        entries = sorted(os.scandir(long_path(source / rel)), key=lambda e: e.name)
    except OSError:
        plan.unreadable.append(str(rel))
        return
    taken: set[str] = set()
    for entry in entries:
        src_rel = rel / entry.name
        if _is_link(entry):
            plan.links.append(str(src_rel))
            continue
        name = windows_name(entry.name)
        stem, ext = os.path.splitext(name)
        n = 2
        while name.casefold() in taken:   # two Linux names, one NTFS name
            name, n = f"{stem} ({n}){ext}", n + 1
        taken.add(name.casefold())
        dst_rel = out_rel / name
        if name != entry.name:
            plan.renamed.append((str(src_rel), str(dst_rel)))
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            size = 0 if is_dir else entry.stat(follow_symlinks=False).st_size
        except OSError:
            plan.unreadable.append(str(src_rel))
            continue
        if is_dir:
            _walk(source, src_rel, dst_rel, plan, dest)
            continue
        reason = _aside_reason(str(src_rel))
        if reason is None and os.path.lexists(long_path(dest / dst_rel)):
            reason = "a file of that name is already in the destination; it was kept"
        if reason is not None:
            plan.aside[str(src_rel)] = reason
            dst_rel = PurePosixPath(ASIDE_DIR) / dst_rel
        plan.copies.append((str(src_rel), str(dst_rel)))
        plan.bytes += size


def make_plan(source: Path, dest: Path) -> Plan:
    plan = Plan()
    _walk(source, PurePosixPath(), PurePosixPath(), plan, dest)
    return plan


def rewrite_paths(value, linux_root: str, dest: Path, found: list[str]):
    """Strings under `linux_root` become paths under `dest`; other Linux paths are listed in `found`."""
    if isinstance(value, dict):
        return {k: rewrite_paths(v, linux_root, dest, found) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_paths(v, linux_root, dest, found) for v in value]
    if not isinstance(value, str):
        return value
    root = linux_root.rstrip("/") + "/"
    if value.startswith(root):
        return str(dest.joinpath(*PurePosixPath(value[len(root):]).parts))
    if value.startswith(LINUX_PATH_PREFIXES):
        found.append(value)
    return value


def _fix_json(dest: Path, rel: str, linux_root: str, report: list[str]) -> None:
    target = dest / rel
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    foreign: list[str] = []
    fixed = rewrite_paths(data, linux_root, dest, foreign)
    for path in foreign:
        report.append(f"  {rel}: Linux path left as it is: {path}")
    if fixed == data:
        return
    keep = dest / ASIDE_DIR / rel
    keep.parent.mkdir(parents=True, exist_ok=True)
    if not keep.exists():
        shutil.copy2(target, keep)
    atomic_write_json(target, fixed, indent=2, ensure_ascii=False)
    report.append(f"  {rel}: paths under {linux_root} now point into {dest} (original in {ASIDE_DIR}\\)")


def apply(source: Path, dest: Path, plan: Plan, linux_root: str) -> list[str]:
    """Copy what the plan says. Returns the lines for the report's "after" section."""
    notes: list[str] = []
    copied: list[str] = []
    for src_rel, dst_rel in plan.copies:
        target = dest / dst_rel
        os.makedirs(long_path(target.parent), exist_ok=True)
        if os.path.lexists(long_path(target)):
            notes.append(f"  {dst_rel}: already there (an earlier run?); kept, not overwritten")
            continue
        try:
            shutil.copy2(long_path(source / src_rel), long_path(target))
        except OSError as e:
            notes.append(f"  {src_rel}: NOT copied ({e.strerror or e})")
            continue
        if os.path.getsize(long_path(target)) != os.path.getsize(long_path(source / src_rel)):
            notes.append(f"  {dst_rel}: size differs from the source; copy it again by hand")
        copied.append(dst_rel)
    for dst_rel in copied:
        if dst_rel.startswith(_LAYOUT["config"] + "/") and dst_rel.endswith(".json"):
            _fix_json(dest, dst_rel, linux_root, notes)
    db = dest / PLAYTIME_DB
    if db.is_file():
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
        try:
            verdict = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
        notes.append(f"  {PLAYTIME_DB}: integrity check {verdict}")
    return notes


def _grouped(aside: dict[str, str]) -> list[str]:
    """One line per reason with its first files: an addon venv alone is thousands of them."""
    by_reason: dict[str, list[str]] = {}
    for rel, why in aside.items():
        by_reason.setdefault(why, []).append(rel)
    rows = []
    for why, rels in by_reason.items():
        rows.append(f"  {why} ({len(rels)} file(s)):")
        rows += [f"    {rel}" for rel in rels[:REPORT_SAMPLE]]
        if len(rels) > REPORT_SAMPLE:
            rows.append(f"    and {len(rels) - REPORT_SAMPLE} more")
    return rows


def render(source: Path, dest: Path, plan: Plan, applied: bool, notes: list[str]) -> str:
    live = len(plan.copies) - len(plan.aside)
    lines = [f"GameCore import: {source} -> {dest}",
             f"{'Copied' if applied else 'Would copy'}: {live} file(s) to their place, "
             f"{len(plan.aside)} to {ASIDE_DIR}\\, {fmt_size(plan.bytes)} in all."]
    sections = [
        (f"Kept aside in {ASIDE_DIR}, not used by the Windows edition", _grouped(plan.aside)),
        ("Renamed (Windows refuses the name). A ROM renamed loses its playtime and favourite: "
         "they are keyed by the file name", [f"  {a} -> {b}" for a, b in plan.renamed]),
        ("Links, not followed (copy their target by hand if it matters)", [f"  {r}" for r in plan.links]),
        ("Unreadable, skipped", [f"  {r}" for r in plan.unreadable]),
        ("After the copy", notes),
    ]
    for title, rows in sections:
        if rows:
            lines += ["", f"{title}:", *rows]
    lines += ["", "Next: install GameCore with this data root:",
              f"  .\\install\\install.ps1 -DataRoot \"{dest}\""]
    if not applied:
        lines.insert(1, "Plan only: nothing was written. Run again with --apply to copy.")
    return "\n".join(lines) + "\n"


def check_roots(source: Path, dest: Path) -> str | None:
    """Why the pair is refused, or None."""
    src, dst = source.resolve(), dest.resolve()
    if not src.is_dir():
        return f"{source} is not a folder"
    if not any((src / marker).is_dir() for marker in SOURCE_MARKERS):
        return f"{source} has no {' or '.join(SOURCE_MARKERS)} folder: not a GameCore data root"
    if src == dst or src in dst.parents or dst in src.parents:
        return "the source and the destination must not contain each other"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path, help="the Linux data root, e.g. D:\\userdata")
    ap.add_argument("--to", dest="dest", type=Path, required=True, help="the Windows data root")
    ap.add_argument("--apply", action="store_true", help="copy (without it, print the plan)")
    ap.add_argument("--linux-root", default="/userdata",
                    help="where the source lived on Linux, to rewrite paths in config files")
    args = ap.parse_args(argv)
    refusal = check_roots(args.source, args.dest)
    if refusal:
        print(f"import-linux-data: {refusal}", file=sys.stderr)
        return 1
    plan = make_plan(args.source, args.dest)
    if not args.apply:
        print(render(args.source, args.dest, plan, False, []), end="")
        return 0
    args.dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(args.dest).free
    if free < plan.bytes:
        print(f"import-linux-data: {fmt_size(plan.bytes)} to copy, {fmt_size(free)} free", file=sys.stderr)
        return 1
    report = render(args.source, args.dest, plan, True, apply(args.source, args.dest, plan, args.linux_root))
    log = args.dest / _LAYOUT["logs"] / "import" / f"linux-import-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(report, encoding="utf-8", newline="\n")
    print(report + f"Report saved to {log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
