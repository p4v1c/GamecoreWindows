#!/usr/bin/env python3
"""Move games off a superseded tile: dolphin → gamecube + wii, mgba → gb + gbc + gba,
ryujinx → switch.

    scripts/split-systems.py --data /userdata            # dry run: prints the plan
    scripts/split-systems.py --data /userdata --apply    # does it

Run by hand, as the box's user, with every game closed. Nothing calls it: not
`update/linux.sh`, not the backend, not a unit. A box that never runs it keeps
its old tiles, which go on launching as before.

Every change is a rename on the same disk: ROMs, their `.sav` and other files
sharing their name, covers, metadata, scraped media and per-game
settings. Nothing is copied, deleted or overwritten. The dry run lists each
file and where it would go; back the data root up before `--apply`
(docs/architecture/07-config-and-data.md, "Splitting a system").
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _running(needles: set[str]) -> list[str]:
    """Command lines of processes naming one of these emulators."""
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(n in cmd for n in needles) and "split-systems" not in cmd:
            found.append(cmd.strip())
    return found


def _print_plan(splits, config_notes, grid_notes) -> None:
    if not splits:
        print("Nothing to split: no superseded tile on this grid.")
        return
    for s in splits:
        print(f"\n== {s.old} -> {', '.join(s.new)}")
        for m in s.moves:
            print(f"  move {m.what:<9} {m.src}\n       {'':<9} -> {m.dst}")
        for key, sid in s.playtime:
            print(f"  playtime  {key} -> {sid}")
        for path, why in s.kept:
            print(f"  keep      {path}  ({why})")
    for note in config_notes + grid_notes:
        print(f"  {note}")
    saves = sum(m.what == "save" for s in splits for m in s.moves)
    games = sum(m.what == "rom" for s in splits for m in s.moves)
    print(f"\n{games} game(s) and {saves} .sav file(s) to move.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path, help="the data root, e.g. /userdata")
    ap.add_argument("--apply", action="store_true", help="do it; without this, only print")
    args = ap.parse_args()

    if os.geteuid() == 0:
        print("split-systems: run it as the box's user, not root — moved files "
              "would end up owned by root.", file=sys.stderr)
        return 2
    if not (args.data / "config" / "systems.json").is_file():
        print(f"split-systems: no config/systems.json under {args.data}", file=sys.stderr)
        return 2

    # Both roots before the backend is imported: paths.py reads them at import.
    os.environ["GAMECORE_PATH"] = str(ROOT)
    os.environ["GAMECORE_DATA"] = str(args.data.resolve())
    sys.path.insert(0, str(ROOT))
    from backend.services import system_split
    from backend.services.catalog import load_catalog

    packs = load_catalog(ROOT / "catalog", args.data / "config" / "catalog.d")
    splits = system_split.plan(packs)
    if not args.apply:
        _, config_notes = system_split.edit_configs(splits, packs)
        _, grid_notes = system_split.edit_grid(splits, packs) if splits else ([], [])
        _print_plan(splits, config_notes, grid_notes)
        if splits:
            print("Dry run: nothing was changed. Back up, close every game, then add --apply.")
        return 0

    needles = {a for s in splits for a in packs[s.owner].app_ids}
    needles |= {Path(p).name for s in splits
                for p in [(packs[s.owner].data["launch"].get("preferIfPresent") or {}).get("path")]
                if p}
    if busy := _running(needles):
        print("split-systems: an emulator is running — close the game first:\n  "
              + "\n  ".join(busy), file=sys.stderr)
        return 1
    for line in system_split.apply(splits, packs):
        print(line)
    print("\nDone. Restart GameCore (or reboot the box) so the grid reloads its tiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
