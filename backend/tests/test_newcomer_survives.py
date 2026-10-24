"""A pack nobody anticipated must come out of every consumer that reads the
catalogue.

Three bugs of the same shape were found and fixed in one afternoon, and none of
them was visible from anywhere:

  · `scripts/gen-catalog.py` built the grid with
    `[tile(packs[i]) for i in SYSTEM_ORDER if i in packs]` — a pack absent from
    that list produced no tile at all;
  · the same file did the same thing for the installer's tick boxes, so a new
    system could not even be selected at install time;
  · `backend/services/configgen/__init__.py` walked a `STEP_ORDER` tuple of ten
    ids — an emulator missing from it was NEVER profiled, so its pad did
    nothing, on a real box, and not one line of output said so.

All three were found by asking "what happens to a brand new pack?". Nothing
asked that question systematically: there were three point tests where one
general one was needed. This is that test.

**What it checks, and what it deliberately does not.** Every consumer is fed a
catalogue containing all the real packs plus one fictional newcomer, and must
either emit the newcomer or drop it for a reason the pack itself DECLARES —
`strategy: none`, no `install` block, the wrong `kind`. "Not in a list in this
file" is never an acceptable reason, and that is the only failure mode this
file exists to catch.

The newcomer deliberately declares no `order` and no `controllers.order`: those
are exactly the fields whose absence used to mean "dropped". A pack that forgets
them must be placed badly, never be missing.

Re-introduce a hardcoded id list in any consumer below and this file goes red.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.catalog import load_catalog  # noqa: E402
from backend.services.platform import filesystem  # noqa: E402

CATALOG = ROOT / "catalog"

# The smallest file that is really a PNG: `Pack.logo` is presence-on-disk, and
# `serve_logo` hands the file to starlette, so it has to exist and be readable.
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63000100000500010d0a2db4"
    "0000000049454e44ae426082"
)

NEWCOMER = "newcomer"
NEWCOMER_APP_ID = "org.example.Newcomer"


def _pack_data(kind: str, **overrides) -> dict:
    """A complete, plausible pack for a machine this project has never heard of.

    Complete rather than minimal on purpose: the question is "does a NEW pack
    reach every consumer", not "how little can a pack declare". Where a consumer
    needs a block this does not have, that is a finding to report — never a
    reason to keep adding fields until the test goes green.

    No `order` and no `controllers.order`: see the module docstring.
    """
    data = {
        "id": NEWCOMER,
        "kind": kind,
        "label": "Newcomer Entertainment System",
        "emulatorName": "Newcomer",
        "family": "Newcomer Corp",
        "description": "A console this project has never heard of.",
        "platform": "newcomer",
        "color": "#123456",
        "install": {"provider": "flatpak", "appIds": [NEWCOMER_APP_ID]},
        "launch": {"path": "flatpak", "args": "run @APPID@"},
        "config": {"dest": "@FLATPAK_CONFIG@/newcomer"},
        "packages": {"pacman": ["newcomer-runtime"]},
        "scraper": {
            "tgdbId": 999_001,
            "libretro": ["Newcomer - Entertainment System"],
            "mediaAlias": ["newcomer"],
        },
    }
    if kind == "emulator":
        # The schema requires `roms` for an emulator and forbids it for an app.
        data["roms"] = {"dir": "emu/newcomer", "extensions": ["*.ncr"]}
        data["controllers"] = {"maxPlayers": 4, "strategy": "sdl-index-clone",
                               "target": "inis/Pad.ini", "padType": "NewcomerPad"}
        # `perGame` is required of every emulator, which is the whole point of
        # the block: an emulator that cannot be configured per game has to say
        # so out loud rather than be indistinguishable from one nobody wired up.
        #
        # Declared SUPPORTED, and with `key: filename`, because that is the
        # interesting case here. A console this project has never heard of has
        # no serial anybody can read out of its dumps — the last-resort
        # identity is exactly what it would use — and a newcomer that supports
        # the feature is the one that would expose a hardcoded id list in the
        # per-game path, which is the class of bug this whole file exists for.
        data["perGame"] = {
            "supported": True,
            "path": "@FLATPAK_CONFIG@/newcomer/games/@GAMEID@.ini",
            "format": "ini",
            "key": "filename",
            "merge": "own-keys",
        }
    # An override of None REMOVES the block: "this pack declares no `install`"
    # is a case under test, and `"install": null` is a schema violation, not an
    # absent block.
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


@pytest.fixture
def catalogue(tmp_path):
    """A catalogue directory: every real pack, plus one newcomer.

    A junction per pack rather than a copy (the two top-level files are
    copied: a junction is a folder), and the SHIPPED side rather than
    `config/catalog.d/`, so the newcomer is an ordinary pack: nothing is
    stripped by the data-only rule and no consumer needs an environment flag to
    see it. `_schema/` comes along because `load_catalog` reads the schema from
    the tree it is pointed at.
    """
    def build(kind: str = "emulator", **overrides):
        root = tmp_path / f"catalog-{kind}-{len(list(tmp_path.iterdir()))}"
        root.mkdir()
        for entry in CATALOG.iterdir():
            if entry.is_dir():
                filesystem.link_dir(root / entry.name, entry)
            else:
                shutil.copy2(entry, root / entry.name)
        local = tmp_path / f"local-{root.name}"
        local.mkdir()

        pack_dir = root / NEWCOMER
        pack_dir.mkdir()
        data = _pack_data(kind, **overrides)
        (pack_dir / "pack.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        (pack_dir / "logo.png").write_bytes(_PNG_1x1)

        packs = load_catalog(root, local)
        # Guard: everything below is vacuous if the newcomer never loaded. A
        # schema change that rejects it must fail HERE, with that message, and
        # not as nine confusing failures further down.
        assert NEWCOMER in packs, (
            "the fictional pack no longer validates against "
            "catalog/_schema/pack.schema.json — fix the pack above, and check "
            "whether the schema change also breaks real third-party packs")
        return root, local, packs

    return build


def _query(catalog: Path, local: Path, *args: str) -> str:
    # --no-probe: without it the script asks the DEVELOPER'S flatpak what is
    # installed, and the answer to "where does this pack's config live" would
    # depend on which emulators happen to be on the machine running the suite.
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/catalog-query.py"), *args, "--no-probe",
         "--home", "/home/USER", "--gamecore-path", "/opt/GameCore",
         "--catalog", str(catalog), "--local", str(local)],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _gen_catalog():
    """`scripts/gen-catalog.py` is a script, not a module — its name has a dash."""
    spec = importlib.util.spec_from_file_location(
        "gamecore_gen_catalog", ROOT / "scripts" / "gen-catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KINDS = ["emulator", "app"]


# ── backend/routers/catalog.py ─────────────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_the_catalogue_endpoint_offers_the_newcomer(catalogue, monkeypatch, kind):
    """The list the "Systems" screen installs from. A pack it cannot name is a
    pack the owner cannot install from the box."""
    _, _, packs = catalogue(kind)
    from backend.routers import catalog as catalog_router
    monkeypatch.setattr(catalog_router, "load_catalog", lambda *a, **k: packs)

    rows = {row["id"]: row for row in catalog_router.list_catalog()}
    assert NEWCOMER in rows, f"/api/catalog lists {sorted(rows)} — no newcomer"
    assert rows[NEWCOMER]["kind"] == kind
    assert rows[NEWCOMER]["label"] == "Newcomer Entertainment System"


# ── backend/routers/systems.py ─────────────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_tile_gets_its_image_served(catalogue, monkeypatch, kind):
    """A tile with no image is what a fresh install looked like the day the
    logos moved into the packs. `serve_logo` must find a pack it has never
    seen, by the one rule tiles.py states: the logo is named after the pack."""
    _, _, packs = catalogue(kind)
    import backend.services.catalog as catalog_pkg
    from backend.routers import systems as systems_router
    monkeypatch.setattr(catalog_pkg, "load_catalog", lambda *a, **k: packs)

    # A real Request, because the route is now a conditional one: it may answer
    # 304 instead of the file. With no `If-None-Match` there is nothing to match
    # and the file comes back, which is the case this test is about.
    from starlette.requests import Request
    request = Request({"type": "http", "method": "GET", "headers": [],
                       "path": f"/assets/logos/{NEWCOMER}.png"})

    response = systems_router.serve_logo(request, f"{NEWCOMER}.png")
    assert Path(response.path) == packs[NEWCOMER].logo


# ── backend/services/configgen/__init__.py ─────────────────────────────────

def test_the_newcomer_is_profiled(catalogue):
    """The bug that cost a working gamepad: an emulator absent from `STEP_ORDER`
    was never profiled, and the only symptom was a pad doing nothing in that one
    emulator. A pack declaring a strategy is profiled, `order` or no `order`."""
    from backend.services.configgen import profilable_packs
    _, _, packs = catalogue("emulator")

    profiled = [p.id for p in profilable_packs(packs)]
    assert NEWCOMER in profiled, f"profiled {profiled} — the newcomer is not among them"


def test_a_pack_that_declares_no_controllers_is_skipped_for_its_own_reason(catalogue):
    """The acceptable half of the rule: dropped because the PACK says so."""
    from backend.services.configgen import profilable_packs
    _, _, packs = catalogue("app")            # apps declare no `controllers`
    assert NEWCOMER not in [p.id for p in profilable_packs(packs)]

    _, _, packs = catalogue("emulator",
                            controllers={"maxPlayers": 0, "strategy": "none"})
    assert NEWCOMER not in [p.id for p in profilable_packs(packs)]


# ── backend/services/gamemedia/gamemedia.py ────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_media_alias_reaches_gamemedia(catalogue, monkeypatch, kind):
    """Without its alias the newcomer's covers resolve against its own id, which
    ScreenScraper does not know — a whole system with no artwork."""
    _, _, packs = catalogue(kind)
    import backend.services.catalog as catalog_pkg
    from backend.services.gamemedia import gamemedia
    monkeypatch.setattr(catalog_pkg, "load_catalog", lambda *a, **k: packs)

    assert gamemedia._catalog_aliases().get(NEWCOMER) == "newcomer"


# ── backend/services/scraper.py ────────────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_platform_ids_reach_the_scraper(catalogue, monkeypatch, kind):
    """xenia was missing from `PLATFORM_MAP` entirely, so the libretro cover
    fallback never fired for the Xbox 360. Both maps are built by walking the
    catalogue, and this is what keeps them walking it."""
    _, _, packs = catalogue(kind)
    import backend.services.catalog as catalog_pkg
    from backend.services import scraper
    monkeypatch.setattr(catalog_pkg, "load_catalog", lambda *a, **k: packs)

    tgdb, libretro = scraper._catalog_scraper_maps()
    assert tgdb.get(NEWCOMER) == 999_001
    assert libretro.get(NEWCOMER) == ["Newcomer - Entertainment System"]


# ── backend/services/catalog/merge.py ──────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_becomes_a_tile(catalogue, tmp_path, kind):
    _, _, packs = catalogue(kind)
    from backend.services.catalog.merge import entry_from_pack

    entry = entry_from_pack(packs[NEWCOMER], tmp_path)
    assert entry["id"] == NEWCOMER
    assert entry["path"] == "flatpak"
    assert entry["iconPath"] == f"assets/logos/{NEWCOMER}.png"


@pytest.mark.parametrize("kind", KINDS)
def test_an_installed_box_gains_the_newcomer_on_update(catalogue, tmp_path, kind):
    """`config/` is out of the OTA rsync, so the merge is the ONLY way a pack
    that shipped after an install reaches that box's grid."""
    _, _, packs = catalogue(kind)
    from backend.services.catalog.merge import merge_file

    box = tmp_path / f"box-{kind}"
    (box / "config").mkdir(parents=True)
    grid = box / "config" / "systems.json"
    grid.write_text("[]\n", encoding="utf-8")

    notes = merge_file(grid, packs, box, kind=kind)
    ids = {row["id"] for row in json.loads(grid.read_text(encoding="utf-8"))}
    assert NEWCOMER in ids, f"the merge wrote {sorted(ids)}"
    assert any(NEWCOMER in note for note in notes), notes


# ── scripts/catalog-query.py ───────────────────────────────────────────────

def test_every_catalog_query_subcommand_sees_the_newcomer(catalogue):
    """The bridge every shell installer reads. A subcommand that cannot name the
    newcomer is an install step that silently skips it.

    Each subcommand is asserted on what the newcomer DECLARES: it has an
    `install.provider: flatpak`, a `config`, a `launch`, a `packages` block, and
    — as an emulator — a `roms` block.
    """
    catalog, local, _ = catalogue("emulator")

    assert NEWCOMER in _query(catalog, local, "ids").split()
    assert f"{NEWCOMER}\t{NEWCOMER_APP_ID}" in _query(catalog, local, "flatpaks")
    assert NEWCOMER_APP_ID in _query(catalog, local, "app-ids").split()
    assert f"{NEWCOMER}\t{NEWCOMER_APP_ID}" \
        in _query(catalog, local, "app-id-candidates")
    assert f"{NEWCOMER}\t/home/USER/.var/app/{NEWCOMER_APP_ID}/config/newcomer" \
        in _query(catalog, local, "config-dest")
    assert NEWCOMER in _query(catalog, local, "rom-dirs").split()
    # The launcher names the TOKEN, not the id — that is the whole point of the
    # field. A newcomer that leaked its app id into `args` would be a tile that
    # cannot follow the fallback its own pack declares.
    assert f"{NEWCOMER}\tflatpak\trun @APPID@" \
        in _query(catalog, local, "launchers")
    assert NEWCOMER_APP_ID in _query(catalog, local, "sandbox")
    assert "newcomer-runtime" in _query(catalog, local, "packages").split()


def test_catalog_query_drops_the_newcomer_only_where_it_declares_nothing(catalogue):
    """The other half: `--kind app` has no ROM directory because the schema
    forbids an app from having one, and a pack with no `install` block has no
    Flatpak because it declares none. Both are the pack's own doing."""
    catalog, local, _ = catalogue("app")
    assert NEWCOMER in _query(catalog, local, "ids").split()
    assert NEWCOMER not in _query(catalog, local, "rom-dirs").split()

    # No `install` block at all: the pack obtains nothing of its own — the
    # emulator is expected to be already on the box.
    catalog, local, _ = catalogue("emulator", install=None)
    assert NEWCOMER in _query(catalog, local, "ids").split()
    assert NEWCOMER not in _query(catalog, local, "flatpaks")


# ── scripts/gamecore-provider.py ───────────────────────────────────────────

@pytest.mark.linux_until_port(11)
@pytest.mark.parametrize("kind", KINDS)
def test_the_installer_bridge_installs_the_newcomer(catalogue, tmp_path, kind):
    """`--dry-run`, so nothing is fetched and no network is touched: what is
    being checked is that the pack is SELECTED, not what installing it does."""
    catalog, local, _ = catalogue(kind)
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gamecore-provider.py"),
         "install", NEWCOMER, "--dry-run",
         "--gamecore-path", str(tmp_path / "gc"),
         "--user-home", str(tmp_path / "home"),
         "--catalog", str(catalog), "--local", str(local)],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"PACK {NEWCOMER}" in r.stdout, r.stdout
    assert NEWCOMER_APP_ID in r.stdout, r.stdout
    assert "FAIL" not in r.stdout, r.stdout


# ── scripts/gen-catalog.py ─────────────────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_gets_a_tile_on_the_grid(catalogue, kind):
    """The first of the three bugs, pinned. `render` used to walk a list of ids;
    a pack missing from it generated nothing and appeared nowhere."""
    _, _, packs = catalogue(kind)
    systems_text, apps_text = _gen_catalog().render(packs)

    mine, other = ((systems_text, apps_text) if kind == "emulator"
                   else (apps_text, systems_text))
    assert NEWCOMER in {row["id"] for row in json.loads(mine)}, mine
    assert NEWCOMER not in {row["id"] for row in json.loads(other)}


@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_gets_a_tick_box_in_the_installer(catalogue, kind):
    """The second. The wizard is a PyInstaller binary built before the
    repository is on the machine, so a pack absent from this generated module
    cannot be selected at install time at all."""
    _, _, packs = catalogue(kind)
    assert repr(NEWCOMER) in _gen_catalog().render_installer_data(packs)


@pytest.mark.parametrize("kind", KINDS)
def test_the_newcomer_gets_its_colour_in_the_frontend(catalogue, kind):
    """`systemColors.ts` is the grid's fallback colour. It had already drifted
    on two ids and ignored five others while it was hand-written."""
    _, _, packs = catalogue(kind)
    assert f"{NEWCOMER}: '#123456'" in _gen_catalog().render_system_colours(packs)
