"""The OTA updater, after the swap: deliver the release's themes and merge its catalogue.

Run by update/apply-update.ps1 with the new release's own venv, once the new
code root is in place and before GameCore starts again. Both steps write the
data root only, and neither failure stops the update: the code is already
installed, and the grid and the themes the box had still work.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

Say = Callable[[str], None]


def _version_key(version: str) -> tuple[int, int, int]:
    """Tolerant: a version written by a theme author must not raise ('1.2.0-beta', 'v3', '')."""
    nums = [int(n) for n in re.findall(r"\d+", version or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))       # type: ignore[return-value]


def theme_version(theme: Path) -> str:
    """'' means unreadable, never a version: such a theme is left alone."""
    try:
        return str(json.loads((theme / "theme.json").read_text(encoding="utf-8")).get("version", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def _replace_theme(source: Path, dest: Path, prev_dir: Path) -> None:
    """Stage beside the live copy, then swap by rename: a theme is never half-written or absent.

    The previous copy goes to `.prev/<id>` (skipped by list_themes()).
    """
    stage = dest.with_name(".incoming-" + dest.name)
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(source, stage)
    prev_dir.mkdir(parents=True, exist_ok=True)
    kept = prev_dir / dest.name
    shutil.rmtree(kept, ignore_errors=True)
    dest.rename(kept)
    try:
        stage.rename(dest)
    except OSError:
        kept.rename(dest)
        shutil.rmtree(stage, ignore_errors=True)
        raise


def deliver_themes(shipped: Path, installed: Path, say: Say) -> dict[str, int]:
    """Install the release's themes that are missing, replace the ones it ships newer.

    A theme on the box and not in the release is never touched (one the player
    wrote is theirs); same or older versions and unreadable manifests are left.
    """
    counts = {"installed": 0, "updated": 0, "kept": 0}
    if not shipped.is_dir():
        return counts
    installed.mkdir(parents=True, exist_ok=True)
    for source in sorted(p for p in shipped.iterdir() if p.is_dir()):
        dest = installed / source.name
        try:
            if not dest.exists():
                shutil.copytree(source, dest)
                counts["installed"] += 1
                continue
            have, want = theme_version(dest), theme_version(source)
            if not want or not have or _version_key(want) <= _version_key(have):
                counts["kept"] += 1
                continue
            _replace_theme(source, dest, installed / ".prev")
            say(f"Theme {source.name}: {have} -> {want} (previous kept in themes/.prev/{source.name})")
            counts["updated"] += 1
        except OSError as e:
            say(f"WARNING: theme {source.name} left as it was: {e}")
            counts["kept"] += 1
    return counts


def merge_catalogue(root: Path, data: Path, say: Say) -> None:
    """The shipped catalogue into the player's systems.json (services/catalog/merge.py)."""
    from backend.services.catalog import load_catalog
    from backend.services.catalog.merge import merge_file

    config = data / "config"
    notes = merge_file(config / "systems.json", load_catalog(root / "catalog", config / "catalog.d"),
                       root, data_root=data, only_present=True)
    for note in notes or ["nothing to change."]:
        say(f"  {note}")


def finish(root: Path, data: Path, say: Say) -> int:
    """Both steps against the new code root; always 0, a failure is reported, not fatal."""
    from_themes = root / "config" / "themes"
    # The backend's modules read both roots from the environment at import.
    os.environ["GAMECORE_PATH"], os.environ["GAMECORE_DATA"] = str(root), str(data)
    sys.path.insert(0, str(root))
    from backend.services import paths

    paths.use_roots(root, data)
    counts = deliver_themes(from_themes, paths.themes_dir(), say)
    say(f"Themes: {counts['installed']} installed, {counts['updated']} updated, {counts['kept']} left untouched.")
    try:
        merge_catalogue(root, data, say)
    except Exception as e:  # noqa: BLE001 - any failure leaves the grid as it was
        say(f"WARNING: catalogue merge failed, the grid is unchanged: {e}")
    return 0
