"""Point an emulator at one profile's saves, and back, from what its pack declares.

`profileSaves` in pack.json, as an object, says where the emulator keeps its
saves, in two ways an emulator can be told:

- `keys`: options in one of its config files (`savefile_directory`, a memory
  card folder). The profile's value is written over the option, and the
  option's own value is put back for the primary profile.
- `dirs`: folders it has no option for (PPSSPP's SAVEDATA, Eden's
  nand/user/save). The folder is renamed `<name>.gamecore-primary` and a
  directory link (a junction, `services/platform/filesystem.py`) to the
  profile's folder takes its place; the primary profile gets
  its folder back under its own name. A rename in place, never a copy: the
  saves themselves are not touched.

What was there before a profile took over is kept in `.primary.json` under the
profile saves root, written BEFORE the emulator's file changes, so a power cut
between the two leaves something to restore from. Entries are keyed by the
file and option, or the folder, not by pack: gb, gbc and gba share one mGBA
config, and a key the gba launch remembered must be the one the gb launch puts
back.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..utils import atomic_write, atomic_write_json
from .errors import ServiceError
from .platform import filesystem

SAVES_TOKEN = "@SAVES@"
STORE_NAME = ".primary.json"
PRIMARY_SUFFIX = ".gamecore-primary"


def _store_path(root: Path) -> Path:
    return root / STORE_NAME


def _load(root: Path) -> dict:
    try:
        data = json.loads(_store_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(root: Path, store: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_store_path(root), store, indent=2, ensure_ascii=False)


# ── options in a config file ─────────────────────────────────────────────────

def _line_re(key: str) -> re.Pattern:
    return re.compile(rf"^(\s*){re.escape(key)}(\s*=\s*)(.*)$")


def _bounds(lines: list[str], section: str | None) -> tuple[int, int] | None:
    """[start, end) of `section`'s lines, the whole file for None, or None."""
    if section is None:
        return 0, len(lines)
    head = f"[{section}]"
    for i, line in enumerate(lines):
        if line.strip() == head:
            end = next((j for j in range(i + 1, len(lines))
                        if lines[j].lstrip().startswith("[")), len(lines))
            return i + 1, end
    return None


def read_key(text: str, section: str | None, key: str) -> str | None:
    """The raw value after `=`, or None when the option is absent."""
    lines = text.splitlines()
    span = _bounds(lines, section)
    if span is None:
        return None
    pat = _line_re(key)
    for line in lines[span[0]:span[1]]:
        if m := pat.match(line):
            return m.group(3)
    return None


def write_key(text: str, section: str | None, key: str, raw: str | None) -> str:
    """`key = raw` in `section`, added when missing; raw None removes the line."""
    lines = text.splitlines()
    span = _bounds(lines, section)
    pat = _line_re(key)
    if span is not None:
        for i in range(*span):
            if m := pat.match(lines[i]):
                if raw is None:
                    del lines[i]
                else:
                    lines[i] = f"{m.group(1)}{key}{m.group(2)}{raw}"
                return "\n".join(lines) + "\n"
    if raw is None:
        return text
    if span is None:
        lines += ["", f"[{section}]"] if section else []
        lines.append(f"{key} = {raw}")
    elif section is None:
        # RetroArch reads `#include` last: an option after it could lose to it.
        at = next((i for i, line in enumerate(lines) if line.startswith("#include")), len(lines))
        lines.insert(at, f"{key} = {raw}")
    else:
        at = span[1]
        while at > span[0] and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, f"{key} = {raw}")
    return "\n".join(lines) + "\n"


def _raw_value(entry: dict, folder: Path) -> str:
    value = str(entry["value"]).replace(SAVES_TOKEN, str(folder))
    return f'"{value}"' if entry.get("quote") else value


def _key_id(file: Path, entry: dict) -> str:
    return f"key:{file}:{entry.get('section') or ''}:{entry['key']}"


def apply_keys(entries: list[tuple[Path, dict]], folder: Path | None, root: Path) -> None:
    """Profile values over the options (`folder`), or the remembered ones back (None)."""
    store = _load(root)
    by_file: dict[Path, list[dict]] = {}
    for file, entry in entries:
        by_file.setdefault(file, []).append(entry)
    for file, group in by_file.items():
        if not file.is_file():
            if folder is None:
                continue
            # Never run yet (RetroArch writes its .cfg on exit): a file with
            # only the save options is what the emulator would read anyway,
            # defaults for everything else. The owner's values are "absent".
            file.parent.mkdir(parents=True, exist_ok=True)
            text = new = ""
        else:
            text = new = file.read_text(encoding="utf-8")
        if folder is not None:
            # Remember the owner's values once, before the first write.
            fresh = {_key_id(file, e): read_key(text, e.get("section"), e["key"])
                     for e in group if _key_id(file, e) not in store}
            if fresh:
                store.update(fresh)
                _save(root, store)
            for e in group:
                new = write_key(new, e.get("section"), e["key"], _raw_value(e, folder))
        else:
            for e in group:
                kid = _key_id(file, e)
                if kid in store:
                    new = write_key(new, e.get("section"), e["key"], store[kid])
        if new != text:
            atomic_write(file, new)
        if folder is None:
            gone = [_key_id(file, e) for e in group if _key_id(file, e) in store]
            if gone:
                for kid in gone:
                    store.pop(kid)
                _save(root, store)


# ── folders with no option ───────────────────────────────────────────────────

def _backup(path: Path) -> Path:
    return path.with_name(path.name + PRIMARY_SUFFIX)


def _is_ours(path: Path, root: Path) -> bool:
    """A link this module made: it points into the profile saves root."""
    return filesystem.is_link(path) and filesystem.link_target(path).is_relative_to(root)


def apply_dir(path: Path, target: Path | None, root: Path) -> None:
    """`path` becomes a link to `target`, or the primary's folder again (None).
    A link the owner made himself (saves on another disk) is his folder."""
    backup = _backup(path)
    taken = backup.exists() or filesystem.is_link(backup)
    if target is not None:
        if not path.parent.is_dir():
            raise ServiceError(500, f"{path.parent} is missing: start the emulator once first.")
        target.mkdir(parents=True, exist_ok=True)
        if _is_ours(path, root):
            path.unlink()
        elif path.exists() or filesystem.is_link(path):
            if taken:
                raise ServiceError(500, f"{path} and {backup.name} both exist: sort them out by hand.")
            path.rename(backup)
        filesystem.link_dir(path, target)
        return
    if _is_ours(path, root):
        path.unlink()
    elif path.exists() or filesystem.is_link(path):
        if taken:
            # The emulator replaced the link with a real folder: whose saves
            # those are cannot be told, so neither side is overwritten.
            raise ServiceError(500, f"{path} is a folder and {backup.name} too: sort them out by hand.")
        return
    if taken:
        backup.rename(path)
