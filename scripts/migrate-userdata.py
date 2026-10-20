#!/usr/bin/env python3
"""Copy a box's data out of the installation and into its own tree.

    scripts/migrate-userdata.py --to /userdata                 # plan only
    scripts/migrate-userdata.py --to /userdata --i-know-what-i-am-doing

This moves somebody's ROM library, their saves' neighbours, their playtime and
their credentials. It is the most destructive thing in the repository, so it is
built to be hard to fire by accident and impossible to fire by surprise:

  · **Dry run by default.** No flag, no writes. The plan goes to the terminal
    and a human reads it before anything happens.
  · **It copies. It never deletes, and never overwrites.** The originals stay
    exactly where they are, so a migration that turns out wrong costs disk
    space and nothing else. Deleting the old copy is a separate decision, made
    later, by a person who has seen the box work.
  · **No default destination.** `--to` is required. There is no way to run this
    bare and have it do something.
  · **Nothing calls it.** Not `update/linux.sh`, not an `install/steps/` script
    the OTA invokes, not the backend at startup, not a systemd unit. It is a
    command a human types, and `backend/tests/test_migration.py` fails the
    build if that ever stops being true.

Why it does not finish the job
------------------------------
Copying the bytes is not the migration. The box only starts reading the new
tree once `GAMECORE_DATA` is set in the backend's systemd unit, and this script
does not touch that either — it prints what to do and stops. Two reversible
steps with a human between them, rather than one irreversible one.

That ordering is deliberate: the pointer moved first (the release that split
the paths defaults `GAMECORE_DATA` to the installation, so nothing changed),
and the bytes move here, later, by hand.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.paths import _LAYOUT  # noqa: E402

# The sections worth carrying over, in the order a reader wants them: the small
# irreplaceable things first, the enormous re-downloadable ones last.
#
# Read out of paths._LAYOUT rather than retyped — that table is what the
# backend resolves against, and a migration that disagreed with it would put
# the data somewhere the box does not look.
_SECTIONS = ["config", "roms", "logos", "addons"]


def _human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


class Section:
    def __init__(self, name: str, src: Path, dest: Path):
        self.name, self.src, self.dest = name, src, dest
        self.files: list[Path] = []      # relative to src
        self.collisions: list[Path] = []
        # Symlinks are NOT copied and are named rather than skipped in
        # silence. A ROM directory that is really a link to another disk, or
        # a `volumes/` entry, would otherwise vanish from the new tree with
        # nothing in the plan to say so — and `rglob` does not descend into a
        # linked directory either, so its contents are not in `files` at all.
        self.symlinks: list[Path] = []
        self.bytes = 0
        if not src.is_dir():
            return
        for p in sorted(src.rglob("*")):
            if p.is_symlink():
                self.symlinks.append(p.relative_to(src))
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(src)
            self.files.append(rel)
            self.bytes += p.stat().st_size
            if (dest / rel).exists():
                self.collisions.append(rel)

    @property
    def present(self) -> bool:
        return self.src.is_dir()


def plan(src_root: Path, dest_root: Path) -> list[Section]:
    """What would be copied. Reads only — this is the dry run's whole job."""
    out = []
    for name in _SECTIONS:
        rel = _LAYOUT[name]
        out.append(Section(name, src_root / rel, dest_root / rel))
    return out


def report(sections: list[Section], src_root: Path, dest_root: Path) -> None:
    print(f"\n  {src_root}  →  {dest_root}\n")
    print(f"  {'section':<10} {'files':>7} {'size':>11}   source")
    print("  " + "-" * 68)
    for s in sections:
        if not s.present:
            print(f"  {s.name:<10} {'—':>7} {'absent':>11}   {s.src}")
            continue
        print(f"  {s.name:<10} {len(s.files):>7} {_human(s.bytes):>11}   {s.src}")
    total_files = sum(len(s.files) for s in sections)
    total_bytes = sum(s.bytes for s in sections)
    print("  " + "-" * 68)
    print(f"  {'TOTAL':<10} {total_files:>7} {_human(total_bytes):>11}")

    collisions = [(s, rel) for s in sections for rel in s.collisions]
    if collisions:
        print(f"\n  {len(collisions)} file(s) already exist at the destination "
              "and will be LEFT ALONE:")
        for s, rel in collisions[:20]:
            print(f"    {s.dest / rel}")
        if len(collisions) > 20:
            print(f"    … and {len(collisions) - 20} more")

    symlinks = [(s, rel) for s in sections for rel in s.symlinks]
    if symlinks:
        print(f"\n  *** {len(symlinks)} symlink(s) will NOT be copied — recreate them "
              "by hand at the destination: ***")
        for s, rel in symlinks[:20]:
            try:
                target = os.readlink(s.src / rel)
            except OSError:
                target = "?"
            print(f"    {s.src / rel}  ->  {target}")
        if len(symlinks) > 20:
            print(f"    … and {len(symlinks) - 20} more")

    # A database is the one file whose copy can be wrong while every byte of
    # it is present: SQLite writes in place, and a copy taken mid-transaction
    # is a file that opens and then loses the last thing written. Small, so
    # the copy is instantaneous — but "small" is not "atomic".
    dbs = [s.src / rel for s in sections for rel in s.files
           if rel.suffix in (".db", ".sqlite", ".sqlite3")]
    if dbs:
        print(f"\n  {len(dbs)} database file(s) — stop the backend for the copy "
              "so none is caught mid-write:")
        for db in dbs[:10]:
            print(f"    {db}")
        print("    sudo systemctl stop gamecore-ui gamecore-backend    "
              "(and start them again after)")

    try:
        anchor = _existing_ancestor(dest_root)
        free = shutil.disk_usage(anchor).free
        print(f"\n  Free at the destination: {_human(free)}")
        same_fs = os.stat(src_root).st_dev == os.stat(anchor).st_dev
        if same_fs:
            # copy_file_range → a reflink on btrfs and XFS: the copy shares
            # the source's extents and costs no space until one side changes.
            # Measured on the reference box (btrfs): 321 GiB copied, 0 written.
            print("  Same filesystem as the source: on btrfs or XFS the copy is a "
                  "reflink and\n  costs no space; elsewhere it costs the size above.")
        else:
            print("  A different filesystem from the source: this is a full copy "
                  "of every byte.")
        if free < total_bytes:
            print("  *** NOT ENOUGH SPACE for a full copy — on a reflink-capable "
                  "filesystem it will\n  still succeed; anywhere else it fails "
                  "partway through. ***")
    except OSError as e:
        print(f"\n  Could not measure free space at {dest_root}: {e}")


def _existing_ancestor(p: Path) -> Path:
    """disk_usage needs something that exists; the destination may not yet."""
    for candidate in [p, *p.parents]:
        if candidate.exists():
            return candidate
    return Path("/")


def apply(sections: list[Section]) -> int:
    """Copy, verify, and leave every source in place. Returns files copied."""
    copied = 0
    for s in sections:
        if not s.present:
            continue
        skip = set(s.collisions)
        print(f"\n  {s.name}: {len(s.files) - len(skip)} file(s) → {s.dest}")
        for rel in s.files:
            if rel in skip:
                continue
            src, dest = s.src / rel, s.dest / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # copy2 keeps mtime and mode. The temp-name-then-rename is what
            # makes an interrupted run leave no half-file that a later pass
            # would mistake for an already-migrated one and skip.
            tmp = dest.with_name(dest.name + ".migrating")
            shutil.copy2(src, tmp)
            expected = src.stat().st_size
            got = tmp.stat().st_size
            if got != expected:
                tmp.unlink(missing_ok=True)
                raise SystemExit(
                    f"\nERROR: {src} copied as {got} bytes, expected {expected}."
                    "\nNothing was deleted; the source is untouched. Fix the "
                    "destination and run again.")
            tmp.replace(dest)
            copied += 1
    return copied


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Copy GameCore's data out of the installation.",
        epilog="Dry run unless --i-know-what-i-am-doing is given. "
               "Sources are never deleted or overwritten.")
    ap.add_argument("--from", dest="src", metavar="DIR",
                    default=os.environ.get("GAMECORE_PATH", "/opt/GameCore"),
                    help="the installation (default: $GAMECORE_PATH or /opt/GameCore)")
    ap.add_argument("--to", dest="dest", metavar="DIR", required=True,
                    help="the new data root, e.g. /userdata")
    ap.add_argument("--i-know-what-i-am-doing", dest="write", action="store_true",
                    help="actually copy. Without this, nothing is written.")
    args = ap.parse_args(argv)

    src_root = Path(args.src).resolve()
    dest_root = Path(args.dest).resolve()

    if not src_root.is_dir():
        print(f"ERROR: no installation at {src_root}", file=sys.stderr)
        return 2
    if src_root == dest_root:
        print("ERROR: --from and --to are the same directory.", file=sys.stderr)
        return 2
    # Nesting either way would have the copy walking into its own output, or
    # writing into the tree it is reading.
    if dest_root in src_root.parents or src_root in dest_root.parents:
        print(f"ERROR: {src_root} and {dest_root} are nested — refusing.",
              file=sys.stderr)
        return 2

    # The destination has to be somewhere this user can create files, and a
    # data root at `/userdata` is not — its parent is `/`, root's. Refusing
    # here, with the commands, beats a PermissionError traceback out of the
    # first `mkdir` after the plan was read and approved. The subvolume is
    # what `install/arch.sh` (provision_userdata) creates on a fresh install:
    # it snapshots and takes quota independently of the root, which is the
    # point of separating the data at all.
    anchor = _existing_ancestor(dest_root)
    creatable = os.access(anchor, os.W_OK)
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "<user>"
    how_to_create = (
        f"{anchor} is not writable by {user}, so {dest_root} cannot be created "
        "from here.\n"
        "  Create it first, owned by the user that runs GameCore — a btrfs "
        "subvolume when the\n  filesystem is btrfs (it snapshots and takes "
        "quota on its own), a directory otherwise:\n"
        f"    sudo btrfs subvolume create {dest_root} "
        f"&& sudo chown {user}:{user} {dest_root}\n"
        f"    # or:  sudo install -d -o {user} -g {user} {dest_root}")
    # Only the write refuses. A dry run against a destination that does not
    # exist yet is the normal way to look before deciding — `/userdata` is
    # under `/`, which is root's, and nobody should have to create it just to
    # read a plan. But once the flag is given, refusing up front with the
    # commands beats a PermissionError traceback out of the first mkdir after
    # the plan was read and approved.
    if args.write and not creatable:
        print(f"ERROR: {how_to_create}\nthen run this again.", file=sys.stderr)
        return 2

    sections = plan(src_root, dest_root)
    report(sections, src_root, dest_root)

    if not args.write:
        print("\n  DRY RUN — nothing was written.")
        if not creatable:
            print(f"\n  *** Before carrying it out: {how_to_create}\n")
        print("  Read the plan above. To carry it out:")
        print(f"    {sys.argv[0]} --from {src_root} --to {dest_root} "
              "--i-know-what-i-am-doing\n")
        return 0

    copied = apply(sections)
    print(f"\n  Copied {copied} file(s). Every source is still in place.\n")
    print(switch_over(src_root, dest_root))
    return 0


def switch_over(src_root: Path, dest_root: Path) -> str:
    """The steps that make the box read the new tree — printed, never run.

    Every consumer of the data root has to learn about the move, and they do
    not all learn the same way. The two systemd units read it from a drop-in;
    the addon CLI reads it from the backend's unit; each addon's own unit is
    rewritten by `gamecore-addon update` from what the CLI knows; the CLI
    itself is a root-owned copy in /usr/local/bin that only the installer
    refreshes; and every Flatpak emulator's sandbox is a list of literal
    paths that the installer wrote with the install root in it. Miss one and
    the box is split: the interface reads the new tree, ROM uploads land in
    the old one, an emulator is handed a path it cannot see — and nothing on
    screen says which. The last one was found the hard way, on the reference
    box, the evening it moved: every launch answered "ROM not found".
    """
    d = str(dest_root)
    return f"""  The box does NOT use the new tree yet. To switch it over, in this order:

    1. Point both units at it — a drop-in each, so the installer's own files
       and any THEGAMESDB key in override.conf are left exactly as they are:
         sudo mkdir -p /etc/systemd/system/gamecore-backend.service.d \\
                       /etc/systemd/system/gamecore-ui.service.d
         printf '[Service]\\nEnvironment=GAMECORE_DATA={d}\\n' \\
           | sudo tee /etc/systemd/system/gamecore-backend.service.d/userdata.conf
         printf '[Service]\\nEnvironment=GAMECORE_DATA={d}\\n' \\
           | sudo tee /etc/systemd/system/gamecore-ui.service.d/userdata.conf

    2. The addon CLI in /usr/local/bin is a copy only the installer refreshes.
       Put the one this release ships there (the old one does not know
       GAMECORE_DATA and would bake the old root into every addon's unit):
         sudo install -m 755 {src_root}/install/bin/gamecore-addon /usr/local/bin/gamecore-addon

    3. Let every Flatpak emulator SEE the new tree. Their sandboxes list the
       install root and nothing else — the installer wrote it that way — so a
       launch would hand them a path under {d} and they would report the game
       missing while it sits right there. No root needed:
         cd {src_root} && python3 scripts/catalog-query.py flatpaks \\
           | while IFS=$'\\t' read -r id app; do
               flatpak info "$app" >/dev/null 2>&1 && flatpak override --user --filesystem={d} "$app"
             done

    4. Restart the core so the units take effect:
         sudo systemctl daemon-reload
         sudo systemctl restart gamecore-backend gamecore-ui

    5. Re-run the addon installers: the CLI now reads GAMECORE_DATA from the
       backend's unit and rewrites each addon's unit with it —
         gamecore-addon update

    6. Check, before playing anything:
         systemctl show gamecore-backend -p Environment --value | tr ' ' '\\n' | grep GAMECORE_DATA
         grep GAMECORE_DATA ~/.config/systemd/user/gamecore-addon-*.service
         flatpak override --user --show net.rpcs3.RPCS3 | grep filesystems
       All must name {d}. Then the library, the settings, an
       upload from ROM Manager, and ONE GAME PER EMULATOR, on the TV.

    7. Only then, and only by hand, delete the old copies under
         {src_root}
       (on btrfs the copy was a reflink: deleting the old side frees nothing
       until the new side diverges — read `btrfs filesystem du`, not `du`).
       Before that, know that the emulators' OWN settings still name the old
       tree — game-list folders, recent files, and Ryujinx's DLC/update paths —
       and keep working only because it is still there:
         grep -rl "{src_root}/emu" ~/.var/app/*/config ~/.config 2>/dev/null
       Repoint those inside each emulator, or leave the old tree in place.

  The way back, at any point before step 7:
         sudo rm /etc/systemd/system/gamecore-backend.service.d/userdata.conf \\
                 /etc/systemd/system/gamecore-ui.service.d/userdata.conf
         sudo systemctl daemon-reload && sudo systemctl restart gamecore-backend gamecore-ui
         gamecore-addon update
  and the box reads {src_root} again, exactly as before — nothing there was touched.
"""


if __name__ == "__main__":
    raise SystemExit(main())
