"""The hot-install endpoints.

The CLI is never actually run here: every test stubs the subprocess. What is
asserted is the part that protects the box — what may be named, what may run at
once, and that a cross-origin page cannot drive any of it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.main import app                      # noqa: E402
from backend.routers import catalog               # noqa: E402


@pytest.fixture
def client():
    catalog._current = None
    return TestClient(app)


@pytest.fixture
def fake_cli(monkeypatch):
    """A CLI that succeeds instantly, so the busy logic is what is tested."""
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = None

        async def wait(self):
            return 0

    async def fake_exec(*argv, **kw):
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


# ── listing ────────────────────────────────────────────────────────────────

def test_the_catalogue_lists_every_pack(client):
    rows = client.get("/api/catalog").json()
    ids = {r["id"] for r in rows}
    assert {"rpcs3", "xenia", "twitch", "gopher64"} <= ids
    assert all({"id", "kind", "label", "installed", "origin"} <= set(r) for r in rows)


def test_the_n64_entry_shows_the_emulator_it_actually_launches(client):
    """The id stays `gopher64` — renaming it would orphan emu/gopher64/ and the
    cover cache on every installed box — but the NAME shown to a player must
    tell the truth."""
    row = next(r for r in client.get("/api/catalog").json() if r["id"] == "gopher64")
    assert row["emulatorName"] == "Rosalie's Mupen GUI"


# ── what may be named ──────────────────────────────────────────────────────

def test_an_unknown_pack_is_refused(client, fake_cli):
    assert client.post("/api/catalog/definitely-not-a-pack/install").status_code == 404
    assert fake_cli == [], "nothing may run for a pack the catalogue does not declare"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "rpcs3;rm -rf /", "RPCS3",
                                 "-rf", "", "a b"])
def test_a_malformed_id_never_reaches_the_cli(client, fake_cli, bad):
    r = client.post(f"/api/catalog/{bad}/install")
    assert r.status_code in (400, 404, 405), (bad, r.status_code)
    assert fake_cli == []


def test_a_known_pack_starts(client, fake_cli):
    r = client.post("/api/catalog/rpcs3/install")
    assert r.status_code == 200 and r.json()["ok"] is True


# ── one at a time ──────────────────────────────────────────────────────────

def test_a_second_operation_is_refused_while_one_runs(client, monkeypatch):
    """The task handle, not the lock, is the busy check: two requests in the
    same loop tick both see the lock unlocked — the task has not started yet —
    and the second would silently queue instead of getting its 409.

    Asserted on `_start` rather than over HTTP: the check is synchronous, and
    the TestClient gives each request its own event loop, which would make an
    "operation still running" state impossible to hold across two calls.
    """
    class _Pending:
        def done(self):
            return False

    monkeypatch.setattr(catalog, "_current", _Pending())
    with pytest.raises(Exception) as e:
        catalog._start("install", "pcsx2")
    assert getattr(e.value, "status_code", None) == 409
    assert client.get("/api/catalog/busy").json()["busy"] is True


def test_a_finished_operation_frees_the_slot(client, monkeypatch, fake_cli):
    class _Done:
        def done(self):
            return True

    monkeypatch.setattr(catalog, "_current", _Done())
    assert client.get("/api/catalog/busy").json()["busy"] is False
    assert client.post("/api/catalog/rpcs3/install").status_code == 200


# ── the privileged channel ─────────────────────────────────────────────────

def test_the_installed_cli_is_invoked_through_sudo(monkeypatch):
    """The backend runs as the GameCore user; installing Flatpaks does not.

    `sudo -n` never prompts: there is no terminal here, and a sudo waiting for
    a password would hang until the timeout with _busy_lock held.
    """
    monkeypatch.setattr(catalog.shutil, "which", lambda n: "/usr/local/bin/gamecore-emu")
    argv = catalog._cli_argv("install", "rpcs3")
    assert argv == ["sudo", "-n", "/usr/local/bin/gamecore-emu", "install", "rpcs3"]


def test_without_the_permissions_setup_it_runs_in_place(monkeypatch):
    """A development checkout should fail on its own terms, not pretend."""
    monkeypatch.setattr(catalog.shutil, "which", lambda n: None)
    argv = catalog._cli_argv("install", "rpcs3")
    assert Path(argv[0]).as_posix().endswith("install/bin/gamecore-emu")
    assert "sudo" not in argv


def test_the_sudoers_rule_names_a_root_owned_path():
    """A rule pointing inside GAMECORE_PATH — writable by the very user it
    grants — would be a root shell with extra steps."""
    setup = (ROOT / "install/steps/setup-update-permissions.sh").read_text()
    assert "NOPASSWD: /usr/local/bin/gamecore-emu" in setup
    assert "install -m 755 -o root -g root" in setup
    # And never a blanket flatpak rule: that would let the GameCore user
    # install any application from any remote, as root.
    assert "NOPASSWD: /usr/bin/flatpak" not in setup


# ── cross-origin ───────────────────────────────────────────────────────────

def test_a_cross_origin_page_cannot_install_anything(client, fake_cli):
    """These are bodyless POSTs — exactly the shape a cross-origin form can
    send. The global guard in main.py covers them; this pins that it does."""
    r = client.post("/api/catalog/rpcs3/install",
                    headers={"Origin": "https://evil.example", "Host": "box.local"})
    assert r.status_code == 403
    assert fake_cli == []


# ── the logo each pack ships ────────────────────────────────────────────────

def test_every_pack_carries_the_url_of_its_own_logo(client):
    """Settings → Emulators & apps drew a flat colour swatch per row, because a
    colour was all the list gave it. Every pack ships `logo.png` and the box has
    served it at /assets/logos/<id>.png all along — for the home grid, never for
    the one screen where somebody is choosing WHICH system to install.

    Sent rather than constructed by the caller: a screen that guessed the URL
    would request a logo for every pack and take a 404 for the ones with none,
    with no way to know in advance which those are — so it could not fall back
    to `color` without first failing.
    """
    rows = client.get("/api/catalog").json()
    assert rows, "the catalogue answered nothing"
    for r in rows:
        assert "logo" in r, f"{r['id']} has no logo field at all"
    with_logo = [r for r in rows if r["logo"]]
    assert with_logo, "not one pack advertises a logo"
    for r in with_logo:
        assert r["logo"] == f"assets/logos/{r['id']}.png", (
            f"{r['id']} does not follow the one naming rule tiles.logo_path states"
        )


def test_a_pack_shipping_no_logo_says_so_rather_than_pointing_at_a_404(client, monkeypatch):
    """`null`, never a URL that will not resolve. The row falls back to the
    colour swatch it always had, which is a true statement; a broken image is
    not."""
    from backend.routers import catalog as router

    real = router.load_catalog()
    assert real, "no catalogue to borrow a pack from"
    victim = sorted(real)[0]

    class _NoLogo:
        """The same pack, minus its logo file."""
        def __init__(self, p):
            self._p = p

        def __getattr__(self, name):
            return getattr(self._p, name)

        @property
        def logo(self):
            return None

    monkeypatch.setattr(router, "load_catalog",
                        lambda: {k: (_NoLogo(v) if k == victim else v)
                                 for k, v in real.items()})
    rows = {r["id"]: r for r in client.get("/api/catalog").json()}
    assert rows[victim]["logo"] is None
    assert rows[victim]["color"], "the fallback the row drops back to is gone too"
