"""Every consumer must agree with the catalogue.

This is the file the gopher64 bug dies in.

The N64 slot moved from gopher64 to Rosalie's Mupen GUI in `arch.sh`,
`systems.json.dist`, `config/systems.json` and `controller_profiles.py` — and
was forgotten in four other places. `install-emu-configs.sh` deployed the
curated N64 config to `~/.var/app/io.github.gopher64.gopher64/…`, the `mkdir -p`
CREATED that phantom directory, copied the files, printed a green tick, and RMG
— which reads `~/.var/app/com.github.Rosalie241.RMG/config/RMG/` — never saw any
of it. `uninstall.sh` cleaned the same phantom, `flatpakify-systems.sh` would
have rewritten the launcher to the old app id, and `scripts/verify-emulators.py`
reported a healthy Flathub entry for an application nobody installs.

Nothing detected it because nothing compared the four maps against each other.
These tests do, against the single source: `catalog/<id>/pack.json`.

They are written to FAIL on the pre-phase-2 code and pass once each consumer
reads the catalogue instead of its own copy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.catalog import load_catalog  # noqa: E402
from backend.services.platform import filesystem  # noqa: E402

CATALOG = ROOT / "catalog"
LOCAL = ROOT / "config" / "catalog.d"


@pytest.fixture(scope="module")
def packs():
    return load_catalog(CATALOG, LOCAL)


def _flatpak_app_ids(packs) -> dict[str, str]:
    return {p.id: p.app_id for p in packs.values() if p.app_id}


# ── helpers that read a consumer's own answer ───────────────────────────────

def _catalog_query(*args: str) -> list[list[str]]:
    """Run the bridge the shell installers use, and return its records."""
    import subprocess
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/catalog-query.py"), *args,
         "--home", "/home/USER", "--gamecore-path", "/opt/GameCore",
         "--catalog", str(CATALOG), "--local", str(LOCAL)],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return [line.split("\t") for line in r.stdout.splitlines() if line]


def _python_dict(path: Path, name: str) -> dict:
    """Evaluate a literal `NAME = { … }` assignment without importing."""
    import ast
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"^{name}[^=\n]*=\s*(\{{.*?^\}})", text, re.S | re.M)
    assert m, f"{path.name}: no `{name} = {{…}}` found"
    return ast.literal_eval(m.group(1))


# ── the four sites the N64 migration missed ────────────────────────────────

def test_config_destinations_derive_from_the_installed_app_id(packs):
    """Both installers now ask the catalogue, and it cannot answer wrong.

    @FLATPAK_CONFIG@ expands from the SAME install.appIds entry the box has
    installed, so a destination under a different app id is unexpressible.
    """
    wrong = []
    for emu_id, dest, _native in _catalog_query("config-dest"):
        pack = packs[emu_id]
        if ".var/app/" in dest and f".var/app/{pack.app_id}/" not in dest:
            wrong.append(f"{emu_id}: {dest!r} vs installed {pack.app_id}")
    assert wrong == [], "\n".join(wrong)


def test_no_installer_hardcodes_a_flatpak_config_path():
    """The map that produced the phantom directory existed in FOUR files.

    install-emu-configs.sh and uninstall.sh each carried a full copy, keyed by
    hand; apply-multi-ds4.sh and controller_profiles.py carry their own for
    other reasons. The two installers must now hold none.
    """
    offenders = []
    for name in ("install/steps/install-emu-configs.sh", "install/uninstall.sh"):
        for n, line in enumerate(
                (ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # A config DESTINATION, not prose: both files legitimately mention
            # ~/.var/app/ in help text and in "save data is kept" messages.
            if ".var/app/" in line and "/config" in line:
                offenders.append(f"{name}:{n}: {line.strip()}")
    assert offenders == [], (
        "a hardcoded Flatpak config path is back:\n" + "\n".join(offenders))


def test_flatpakify_rewrites_to_the_app_id_the_installer_installs(packs):
    """A launcher rewritten to an app id nobody installs is a dead tile.

    Harmless today only because `launcher_exists("flatpak")` returns True
    first — a mine, not a protection.
    """
    text = (ROOT / "install/steps/flatpakify-systems.sh").read_text(encoding="utf-8")
    m = re.search(r"FLATPAK_MAP = \{(.*?)^\}", text, re.S | re.M)
    if not m:
        pytest.skip("FLATPAK_MAP is gone — the launcher comes from the catalogue")
    wrong = []
    for emu_id, args in re.findall(r'"([a-z0-9_]+)":\s*\("[^"]*",\s*"([^"]*)"\)',
                                   m.group(1)):
        pack = packs.get(emu_id)
        if pack is None or not pack.app_id:
            continue
        if "run " in args and pack.app_id not in args:
            wrong.append(f"{emu_id}: would launch {args!r}, "
                         f"but the installer installs {pack.app_id}")
    assert wrong == [], "\n".join(wrong)


def test_verify_emulators_checks_what_the_installer_installs(packs):
    """Green on the wrong target is worse than red.

    This is the job that would have caught the whole thing.
    """
    text = (ROOT / "scripts/verify-emulators.py").read_text(encoding="utf-8")
    m = re.search(r"FLATPAK_IDS = \[(.*?)\]", text, re.S)
    if not m:
        pytest.skip("FLATPAK_IDS is gone — the list comes from the catalogue")
    checked = set(re.findall(r'"([\w.\-]+)"', m.group(1)))
    declared = set(_flatpak_app_ids(packs).values())
    unknown = checked - declared
    assert unknown == set(), (
        f"verifies app ids no pack declares: {sorted(unknown)}")


# ── the rest of the duplicated maps ────────────────────────────────────────

def test_arch_installs_exactly_the_flatpaks_the_catalogue_declares(packs):
    text = (ROOT / "install/arch.sh").read_text(encoding="utf-8")
    m = re.search(r"declare -A EMU_FLATPAK=\((.*?)^\s*\)", text, re.S | re.M)
    if not m:
        pytest.skip("EMU_FLATPAK is gone — the list comes from the catalogue")
    declared = _flatpak_app_ids(packs)
    for emu_id, app_id in re.findall(r"\[([a-z0-9_]+)\]=(\S+)", m.group(1)):
        assert declared.get(emu_id) == app_id, (
            f"{emu_id}: arch.sh installs {app_id}, "
            f"the pack declares {declared.get(emu_id)}")


def test_rom_directories_match_the_catalogue(packs):
    text = (ROOT / "install/arch.sh").read_text(encoding="utf-8")
    m = re.search(r"^for d in ([a-z0-9 ]+); do\n\s*sudo -u .* mkdir -p "
                  r'"\$GAMECORE_PATH/emu/\$d"', text, re.M)
    if not m:
        pytest.skip("the ROM directory list comes from the catalogue")
    created = set(m.group(1).split()) - {"covers"}
    declared = {p.data["roms"]["dir"].split("/", 1)[1]
                for p in packs.values() if p.data.get("roms")}
    assert created == declared, (
        f"only in arch.sh: {sorted(created - declared)}; "
        f"only in the catalogue: {sorted(declared - created)}")


def test_scraper_platform_ids_match_the_catalogue(packs):
    from backend.services.scraper import TGDB_PLATFORM_MAP as tgdb
    for pack in packs.values():
        want = (pack.data.get("scraper") or {}).get("tgdbId")
        if want is None:
            continue
        assert tgdb.get(pack.id) == want, (
            f"{pack.id}: scraper.py says {tgdb.get(pack.id)}, pack says {want}")


def test_scraper_libretro_systems_match_the_catalogue(packs):
    """melonds diverged: one entry in systems.json.dist, two in scraper.py —
    and scraper.py is the real consumer."""
    from backend.services.scraper import PLATFORM_MAP as libretro
    mismatched = []
    for pack in packs.values():
        want = (pack.data.get("scraper") or {}).get("libretro")
        if not want:
            continue
        if libretro.get(pack.id) != want:
            mismatched.append(f"{pack.id}: scraper.py {libretro.get(pack.id)} "
                              f"vs pack {want}")
    assert mismatched == [], "\n".join(mismatched)


def test_media_aliases_match_the_catalogue(packs):
    from backend.services.gamemedia.gamemedia import EMULATOR_ALIASES as aliases
    mismatched = []
    for pack in packs.values():
        want = (pack.data.get("scraper") or {}).get("mediaAlias")
        if not want:
            continue
        got = aliases.get(pack.id)
        got = [got] if isinstance(got, str) else got
        if got != want:
            mismatched.append(f"{pack.id}: gamemedia {got} vs pack {want}")
    assert mismatched == [], "\n".join(mismatched)


def test_overlay_wm_classes_match_the_catalogue(packs):
    import json
    overlays = json.loads((ROOT / "config/overlays.json").read_text(encoding="utf-8"))
    for pack in packs.values():
        ov = pack.data.get("overlay")
        if not ov:
            continue
        assert pack.id in overlays, f"{pack.id} declares an overlay, overlays.json has none"
        assert overlays[pack.id]["wm_class"] == ov["wmClass"], pack.id


def test_frontend_colours_match_the_catalogue(packs):
    """systemColors.ts diverged on pcsx2 and rpcs3, and ignored five ids.
    Latent only because it is a fallback — it bites the moment a pack omits
    `color`, which is why the schema makes it required."""
    ts = (ROOT / "frontend/src/lib/systemColors.ts").read_text(encoding="utf-8")
    colours = dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", ts))
    mismatched = []
    for pack in packs.values():
        got = colours.get(pack.id)
        if got is None:
            mismatched.append(f"{pack.id}: missing from systemColors.ts")
        elif got.lower() != pack.data["color"].lower():
            mismatched.append(f"{pack.id}: frontend {got} vs pack {pack.data['color']}")
    assert mismatched == [], "\n".join(mismatched)


def test_the_installer_wizard_offers_the_catalogue(packs):
    sys.path.insert(0, str(ROOT / "install/installer-gui"))
    from catalog_data import EMULATORS
    offered = {e[0] for e in EMULATORS}
    # A superseded pack (dolphin, mgba) only keeps an old tile launching.
    declared = {p.id for p in packs.values() if p.kind == "emulator" and not p.superseded_by}
    assert offered == declared, (
        f"only in the wizard: {sorted(offered - declared)}; "
        f"only in the catalogue: {sorted(declared - offered)}")


# ── the regression, in its most direct form ────────────────────────────────

def test_the_n64_slot_deploys_where_rmg_actually_reads(packs):
    """The exact failure, pinned.

    Before: install-emu-configs.sh created and filled
    ~/.var/app/io.github.gopher64.gopher64/config/gopher64 while RMG read
    ~/.var/app/com.github.Rosalie241.RMG/config/RMG. Green tick, no error, and
    the curated N64 config never applied.

    The id stays `gopher64` on purpose — it is documented in arch.sh and in
    systems.json.dist, and renaming it would move the bug rather than fix it.
    """
    n64 = packs["gopher64"]
    assert n64.app_id == "com.github.Rosalie241.RMG"

    dests = {row[0]: row[1] for row in _catalog_query("config-dest")}
    # Since phase 4 the pack DOES declare where RMG's config lives — its
    # generator needs it for snapshot restore — and that destination must
    # resolve under the app id the installer installs. It still ships no
    # seed/: the emu-configs tree was gopher64-format JSON while RMG reads a
    # mupen64plus INI, so it was unusable whatever the path.
    assert "/com.github.Rosalie241.RMG/" in dests["gopher64"], dests["gopher64"]
    assert "io.github.gopher64.gopher64" not in dests["gopher64"]
    assert not (ROOT / "catalog/gopher64/seed").exists(), (
        "the N64 seed is in gopher64's own JSON format; RMG reads an INI")

    flatpaks = {row[0]: row[1] for row in _catalog_query("flatpaks")}
    assert flatpaks["gopher64"] == "com.github.Rosalie241.RMG"
    assert "io.github.gopher64.gopher64" not in flatpaks.values()


def test_the_old_n64_app_id_is_gone_from_every_consumer():
    """One grep, across every file that used to carry it."""
    offenders = []
    for name in ("install/arch.sh", "install/steps/install-emu-configs.sh",
                 "install/uninstall.sh", "install/steps/flatpakify-systems.sh",
                 "scripts/verify-emulators.py", "install/generated/systems.json.dist",
                 "backend/services/scraper.py",
                 "install/installer-gui/catalog_data.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            # A USE of the id, not prose about it: quoted, assigned, or part of
            # a path. Several of these files legitimately explain the history
            # in a comment or a docstring, and must keep being able to.
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"""["'=/]\s*io\.github\.gopher64\.gopher64""", line):
                offenders.append(f"{name}:{n}: {line.strip()}")
    assert offenders == [], (
        f"the pre-migration N64 app id is still live in: {offenders}")


# ── launcher tokens, resolved at read time ─────────────────────────────────

def test_a_launcher_token_is_resolved_when_the_grid_is_read():
    """Found by actually starting the backend.

    `install/arch.sh` substitutes @HOME@ when it copies install/generated/apps.json.dist
    into config/, and that was the ONLY place it happened. A config/apps.json
    that arrived any other way — restored from a backup, copied out of the
    repository, written by hand — kept the literal, and the YouTube tile
    launched `firefox --profile '@HOME@/.mozilla/firefox/youtube-tv'`.

    Which fails in the way that is hardest to read: Firefox starts, finds no
    such profile, and the tile looks broken for no visible reason.
    """
    from pathlib import Path as _P

    from backend.services.systems import _expand

    rows = _expand([{"id": "youtube", "path": "firefox",
                     "args": "--profile '@HOME@/.mozilla/firefox/youtube-tv'"}])
    assert "@HOME@" not in rows[0]["args"]
    assert str(_P.home()) in rows[0]["args"]


def test_an_absolute_path_already_in_the_grid_is_untouched():
    """A box that predates the token must be unaffected."""
    from backend.services.systems import _expand

    original = {"id": "youtube", "path": "firefox",
                "args": "--profile '/home/someone/.mozilla/firefox/youtube-tv'"}
    assert _expand([dict(original)])[0] == original


# ── local media: the format is data, the parser is code ────────────────────

def test_every_declared_local_media_format_has_a_parser(packs):
    """The schema enum and the registry are one fact in two files.

    A pack may only name a format `local_media.py` can actually read — the
    enum exists to make anything else unwritable, and this is what keeps the
    enum honest when a value is added to it and the parser forgotten. The
    symptom would be silent: covers quietly stop being exact for that system.
    """
    from backend.services.local_media import _FORMATS
    unknown = []
    for pack in packs.values():
        block = pack.data.get("localMedia")
        if block and block["format"] not in _FORMATS:
            unknown.append(f"{pack.id}: declares format {block['format']!r}")
    assert unknown == [], "\n".join(unknown)


def test_the_schema_enum_and_the_parser_registry_agree():
    """Both directions. A parser with no enum entry is unreachable: no pack can
    ask for it, so it is dead code that looks live."""
    import json as _json

    from backend.services.local_media import _FORMATS
    schema = _json.loads(
        (CATALOG / "_schema" / "pack.schema.json").read_text(encoding="utf-8"))
    declared = set(schema["properties"]["localMedia"]["properties"]["format"]["enum"])
    assert declared == set(_FORMATS), (
        f"only in the schema: {sorted(declared - set(_FORMATS))}; "
        f"only in local_media.py: {sorted(set(_FORMATS) - declared)}")


def test_local_media_names_no_system_of_its_own(packs):
    """The chain of `if sid == "rpcs3" / "shadps4" / …` this replaced.

    It was six branches across three functions for one fact, and duckstation
    and pcsx2 differed by a single string. Which parser reads which system is
    the pack's to say; a system id back in this file means it is being said
    twice again.
    """
    import ast
    source = (ROOT / "backend/services/local_media.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Prose may name them, and does: the module header explains which
    # emulators the old chain covered, and that history is the documentation.
    # Comments never reach the AST; docstrings are the string constants that
    # open a module, class or function body, so they are excluded by identity.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    ids = {p.id for p in packs.values()}
    offenders = [f"line {node.lineno}: {node.value!r}"
                 for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and id(node) not in docstrings and node.value in ids]
    assert offenders == [], (
        "local_media.py names a pack id in live code again:\n" + "\n".join(offenders))


# ── the grid's images ──────────────────────────────────────────────────────

def test_every_tile_logo_is_served_at_the_path_the_grid_asks_for():
    """Found by looking at the actual UI: every tile was blank.

    The grid requests the `iconPath` recorded in systems.json —
    `assets/logos/3ds.png` — which used to be answered by a StaticFiles mount
    on assets/logos/. Moving the logos into the packs emptied that directory,
    and `serve_logo` was registered under /api, so nothing served them: 404 on
    all seventeen.

    An OTA-upgraded box would not have noticed — assets/logos/ is excluded from
    the rsync and kept its old files — so this would have shipped and only ever
    broken FRESH installs. Which is the worst place for it to hide.
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    blank = []
    for item in client.get("/api/systems").json():
        icon = item.get("iconPath")
        if not icon:
            blank.append(f"{item['id']}: no iconPath at all")
            continue
        r = client.get("/" + icon)
        if r.status_code != 200 or not r.content:
            blank.append(f"{item['id']}: GET /{icon} -> {r.status_code}")
    assert blank == [], "tiles with no image:\n" + "\n".join(blank)


# ── the prune must understand @APPID@ ──────────────────────────────────────

def _flatpakify_box(tmp_path, tiles, installed):
    """A throwaway GAMECORE_PATH, and a `flatpak` that answers from a fixture.

    The real script is run, not a re-implementation of it: the failure this
    guards lives in the prune's own parsing, so a copy of that parsing in the
    test would agree with itself and prove nothing. Nothing outside tmp_path is
    written, and no Flatpak is installed, removed or queried for real.
    """
    import json
    import os
    import subprocess

    for name in ("backend", "catalog"):
        filesystem.link_dir(tmp_path / name, ROOT / name)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "systems.json").write_text(json.dumps(tiles))

    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "flatpak"
    stub.write_text("#!/bin/sh\nprintf '%s\\n' " +
                    " ".join(f"'{a}'" for a in installed) + "\n" if installed
                    else "#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)

    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    r = subprocess.run(["bash", str(ROOT / "install/steps/flatpakify-systems.sh"),
                        str(tmp_path)],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads((tmp_path / "config" / "systems.json").read_text()), r.stdout


@pytest.mark.linux_until_port(7)
def test_the_prune_resolves_the_token_instead_of_reading_it_as_an_app_id(packs, tmp_path):
    """The regression that would have emptied every fresh install's grid.

    The prune asked `is args.split()[1] in the installed set`. Against
    `run @APPID@ -f` that question is "is the literal string @APPID@ an
    installed Flatpak", the answer is no for every tile at once, and the
    guard that keeps a tile when the probe looks unreliable does NOT fire —
    the probe succeeded, it just answered a question nobody meant to ask.

    Thirteen emulators, all silently dropped, on a box where all thirteen were
    correctly installed.

    A native tile is added alongside, and it is what makes this test able to
    fail. The prune skips itself entirely when it would empty the grid — a
    sound guard, and one that hides this exact bug: with only Flatpak tiles the
    broken prune drops every one of them, the grid comes out empty, the guard
    fires and puts them all back. The bug is invisible until ONE tile survives
    on its own merits.
    """
    flatpak_packs = [p for p in packs.values() if p.kind == "emulator" and p.app_ids][:2]
    native = {"id": "native", "type": "emulator", "label": "native",
              "platform": "native", "color": "#000000", "path": "/bin/sh",
              "args": "", "romsPath": "emu/native/", "extensions": []}
    tiles = [native] + [
        {"id": p.id, "type": "emulator", "label": p.id, "platform": p.id,
         "color": "#000000", "path": "flatpak", "args": "run @APPID@ -f",
         "romsPath": f"emu/{p.id}/", "extensions": []}
        for p in flatpak_packs]

    kept, out = _flatpakify_box(tmp_path, tiles,
                                [p.app_ids[0] for p in flatpak_packs])

    assert [t["id"] for t in kept] == [t["id"] for t in tiles], (
        f"tiles were pruned although every candidate is installed — the token "
        f"was read as an application id.\n{out}")


@pytest.mark.linux_until_port(7)
def test_the_prune_still_drops_a_tile_whose_whole_list_is_absent(packs, tmp_path):
    """The other direction: teaching the prune about the token must not turn it
    into a prune that never prunes. A tile that cannot launch is worse than no
    tile — that is why this pass exists at all.

    Two tiles, because one would not test the prune: dropping the last tile on
    the grid trips the "every launcher looks missing, the probe is unreliable"
    guard and the whole pass is skipped. That guard is right, and it is exactly
    what would hide a prune that had stopped working.
    """
    flatpak_packs = [p for p in packs.values() if p.kind == "emulator" and p.app_ids]
    survivor, doomed = flatpak_packs[0], flatpak_packs[1]

    def tile(pack):
        return {"id": pack.id, "type": "emulator", "label": pack.id,
                "platform": pack.id, "color": "#000000", "path": "flatpak",
                "args": "run @APPID@ -f", "romsPath": f"emu/{pack.id}/",
                "extensions": []}

    kept, out = _flatpakify_box(tmp_path, [tile(survivor), tile(doomed)],
                                [survivor.app_ids[0]])

    assert [t["id"] for t in kept] == [survivor.id], (
        f"expected only {survivor.id} to survive — {doomed.id} declares "
        f"{doomed.app_ids} and none is installed.\n{out}")


# ── the weekly upstream check must be able to fire ─────────────────────────

def _run_verify(monkeypatch, alive):
    """Run verify_emulators.main() with Flathub and GitHub replaced.

    Offline: `alive` is the set of app ids the fake Flathub still knows. The
    real module is imported by path — it lives at the repo root, not in a
    package — and its module-level catalogue read is the thing under test, so
    it is reloaded rather than cached from another test.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_emulators",
                                                  ROOT / "scripts/verify-emulators.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "check_flatpak",
                        lambda app_id: (app_id in alive,
                                        "OK" if app_id in alive else "Not Found"))
    monkeypatch.setattr(mod, "check_github_release_asset",
                        lambda repo, pattern: (True, "Found"))
    monkeypatch.setattr(mod, "check_url", lambda url: (True, "OK"))
    return mod


def test_the_weekly_check_is_green_while_every_app_id_lives(packs, monkeypatch, capsys):
    """The half that keeps the job usable. A check that cries wolf every Monday
    is a check whose issue nobody opens."""
    alive = {a for p in packs.values() for a in p.app_ids}
    mod = _run_verify(monkeypatch, alive)
    assert mod.main() == 0
    assert "verified successfully" in capsys.readouterr().out


def test_the_weekly_check_fails_when_a_pack_has_no_surviving_app_id(packs, monkeypatch,
                                                                    capsys):
    """A dead pack. `.github/workflows/verify-catalog.yml` opens an issue on
    this exit code — a non-zero that never happens is a job that reports
    nothing, and the whole value here is a week's warning before a player finds
    out."""
    doomed = next(p for p in packs.values() if p.app_ids)
    alive = {a for p in packs.values() for a in p.app_ids} - set(doomed.app_ids)

    mod = _run_verify(monkeypatch, alive)
    assert mod.main() == 1
    assert doomed.id in capsys.readouterr().out


def test_a_spent_fallback_is_reported_even_though_nothing_is_broken(monkeypatch,
                                                                    capsys):
    """The finding that would otherwise be invisible.

    A pack with two candidates whose FIRST is gone still installs, still
    launches, and still passes every other check on this box. It is also one
    disappearance from having nothing left, and there will be no second
    warning. Silence here is how a list quietly becomes a string again.
    """
    mod = _run_verify(monkeypatch, {"org.example.Fallback"})
    monkeypatch.setattr(mod, "FLATPAK_PACKS",
                        [("probe", ["org.example.Gone", "org.example.Fallback"])])
    monkeypatch.setattr(mod, "GITHUB_ASSETS", [])

    assert mod.main() == 1, "a spent fallback passed silently"
    out = capsys.readouterr().out
    assert "DEGRADED" in out and "org.example.Gone" in out


@pytest.mark.linux_until_port(7)
def test_the_shell_side_sandbox_grants_the_data_root_too():
    """arch.sh reads its sandbox flags from here, so the shell installer has
    to grant the data root exactly as `providers.sandbox_flags` does — or a
    box installed by hand with a separate /userdata launches every Flatpak
    emulator against a directory its sandbox cannot see."""
    moved = {row[0]: row[1] for row in
             _catalog_query("sandbox", "--gamecore-data", "/userdata")}
    assert "--filesystem=/opt/GameCore --filesystem=/userdata" in moved["net.rpcs3.RPCS3"]
    # A pack with its own policy is not touched.
    assert "/userdata" not in moved["com.stremio.Stremio"]
    # And with the data still inside the install, byte-identical to before.
    same = {row[0]: row[1] for row in
            _catalog_query("sandbox", "--gamecore-data", "/opt/GameCore")}
    assert same["net.rpcs3.RPCS3"] == "--filesystem=/opt/GameCore --device=all --socket=x11"

