"""A fresh install whose data lives outside the install still gets its bezels.

`install/arch.sh` copies the player's starting tree — the shipped bezels, the
declared bezel geometry (`config/overlays.json`), the bundled themes — with a
loop that put each directory into GAMECORE_PATH "when absent there". On the
layout every box had before the split, GAMECORE_PATH *is* the data root and it
worked. On a split install it seeded nothing: `provision_userdata` had already
created the (empty) directories under GAMECORE_DATA, so "absent" was never
true, and whatever was copied went into the install, where nothing reads it.
A fresh /userdata box booted, ran games, and drew no bezel around any of them
— Electron never even started the overlay monitor, because `overlays.json`
was not where the backend looks. The reference box only had its overlays
because its migration had copied them across.

The functions are extracted out of arch.sh and run in bash against temporary
roots, then the backend's own resolver is pointed at the seeded tree: the proof
that matters is `for_launch()` answering a bezel, not a file being present.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ARCH = REPO / "install" / "arch.sh"
GUI = REPO / "install" / "installer-gui" / "gamecore_installer.py"
sys.path.insert(0, str(REPO))

from backend.services import bezels, consoles, paths  # noqa: E402


def _bash_function(name: str) -> str:
    """The body of a top-level `name() { … }` in arch.sh, braces included."""
    text = ARCH.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}\n", text, re.M | re.S)
    assert m, f"{name}() not found in install/arch.sh"
    return m.group(0)


def _run(script: str, **env: str) -> subprocess.CompletedProcess:
    # `ok` is arch.sh's logger; the functions call it and the test does not
    # care what it prints.
    prelude = "set -euo pipefail\nok() { :; }\nwarn() { :; }\n"
    return subprocess.run(["bash", "-c", prelude + script], capture_output=True,
                          text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin", **env})


def _checkout(root: Path) -> Path:
    """What arch.sh's PROJECT_ROOT holds: the repository's data-bearing files."""
    src = root / "checkout"
    for rel in ("assets/overlays/gopher64.png", "assets/overlays/gba.png",
                "assets/overlays/gb.png", "assets/overlays/gbc.png",
                "config/overlays.json", "config/systems.json",
                "config/catalog.d/README.md", "assets/logos/README.md",
                "config/themes/shelf/theme.json", "frontend/src/assets/logo.png"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        real = REPO / rel
        if real.is_file():
            (src / rel).write_bytes(real.read_bytes())
        else:
            (src / rel).write_text("x")
    return src


def _seed(src: Path, data: Path) -> subprocess.CompletedProcess:
    user = os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True,
                                                    text=True).stdout.strip()
    r = _run(_bash_function("seed_data_tree")
             + f'\nseed_data_tree "{src}" "{data}" "{user}"\n')
    assert r.returncode == 0, r.stderr
    return r


# ── The function ────────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_a_fresh_split_install_gets_its_bezels_and_geometry(tmp_path):
    """The case that used to seed nothing: the data root exists, empty, because
    provision_userdata created it a few lines earlier."""
    src = _checkout(tmp_path)
    data = tmp_path / "userdata"
    for d in ("config", "emu", "assets/overlays", "assets/logos", "addons"):
        (data / d).mkdir(parents=True)                # exactly what provision_userdata leaves

    _seed(src, data)

    assert (data / "assets/overlays/gopher64.png").is_file()
    assert (data / "assets/overlays/gba.png").is_file()
    assert (data / "config/overlays.json").is_file()
    assert (data / "config/themes/shelf/theme.json").is_file()
    assert (data / "config/catalog.d/README.md").is_file()


@pytest.mark.linux_until_port(7)
def test_the_seeded_tree_resolves_a_bezel(tmp_path, monkeypatch):
    """The proof that matters. Not "the file is there" — the launch answers."""
    src = _checkout(tmp_path)
    data = tmp_path / "userdata"
    (data / "assets/overlays").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    _seed(src, data)

    monkeypatch.setattr(paths, "GAMECORE_ROOT", tmp_path / "install")
    monkeypatch.setattr(paths, "GAMECORE_DATA", data)
    bezels.forget()
    consoles.forget()
    try:
        assert bezels.for_launch("gopher64", "Mario Kart 64.z64")["source"] == "system"
        assert bezels.for_launch("gba", "Emerald.gba")["source"] == "system"
        # And the geometry Electron reads before starting the overlay monitor
        # at all — `if (!cfg) return` — is in the tree the backend serves.
        assert "gopher64" in json.loads((data / "config/overlays.json").read_text())
    finally:
        bezels.forget()
        consoles.forget()


@pytest.mark.linux_until_port(7)
def test_a_populated_directory_is_the_players_and_is_left_alone(tmp_path):
    """A re-run must not put back a bezel somebody deleted on purpose, nor
    replace one they uploaded. Absent or empty is seeded; anything else is
    theirs."""
    src = _checkout(tmp_path)
    data = tmp_path / "userdata"
    (data / "assets/overlays").mkdir(parents=True)
    (data / "assets/overlays/mgba.png").write_bytes(b"the player's own")
    (data / "config").mkdir()
    (data / "config/overlays.json").write_text('{"mine": true}')

    _seed(src, data)

    assert not (data / "assets/overlays/gopher64.png").exists()          # dir was populated
    assert (data / "assets/overlays/mgba.png").read_bytes() == b"the player's own"
    assert json.loads((data / "config/overlays.json").read_text()) == {"mine": True}
    # Directories that were empty are still seeded on that same run.
    assert (data / "assets/logos/README.md").is_file()


@pytest.mark.linux_until_port(7)
def test_the_old_layout_is_untouched_by_the_change(tmp_path):
    """Data inside the install, installed from a checkout elsewhere: seeded
    into the install exactly as the old loop did. And installed in place
    (checkout == install == data): nothing to do, as before."""
    src = _checkout(tmp_path)
    install = tmp_path / "GameCore"
    install.mkdir()
    _seed(src, install)                                   # data root == install root
    assert (install / "assets/overlays/gopher64.png").is_file()
    assert (install / "config/overlays.json").is_file()

    r = _seed(src, src)                                   # in place: from == to
    assert r.returncode == 0
    assert not (src / "assets/overlays/gopher64.png.bak").exists()   # nothing odd happened


def test_arch_sh_seeds_into_the_data_root_and_the_old_loop_is_gone():
    """The call site: GAMECORE_DATA, and only that. A second copy going into
    GAMECORE_PATH would recreate the dead tree this change removes."""
    text = ARCH.read_text(encoding="utf-8")
    assert re.search(r'^seed_data_tree "\$PROJECT_ROOT" "\$GAMECORE_DATA" "\$USER_NAME"$', text, re.M)
    assert 'cp -a "$PROJECT_ROOT/$_keep" "$GAMECORE_PATH/$_keep"' not in text


@pytest.mark.linux_until_port(7)
def test_what_the_old_loop_did_on_a_split_box_which_is_nothing(tmp_path):
    """Kept as the record of the failure, run rather than remembered: the loop
    arch.sh used until this change, against the exact state provision_userdata
    leaves. It seeds nothing into the data root — the directories exist — and
    puts the bezels into the install, where nothing reads them."""
    src = _checkout(tmp_path)
    install = tmp_path / "GameCore"
    data = tmp_path / "userdata"
    install.mkdir()
    for d in ("config", "emu", "assets/overlays", "assets/logos"):
        (data / d).mkdir(parents=True)
    old_loop = (
        'for _keep in emu config assets/overlays assets/logos; do\n'
        '  if [ -d "$PROJECT_ROOT/$_keep" ] && [ ! -e "$GAMECORE_PATH/$_keep" ]; then\n'
        '    mkdir -p "$(dirname "$GAMECORE_PATH/$_keep")"\n'
        '    cp -a "$PROJECT_ROOT/$_keep" "$GAMECORE_PATH/$_keep"\n'
        '  fi\n'
        'done\n')
    r = _run(old_loop, PROJECT_ROOT=str(src), GAMECORE_PATH=str(install), GAMECORE_DATA=str(data))
    assert r.returncode == 0, r.stderr
    assert not (data / "assets/overlays/gopher64.png").exists()          # the bug
    assert not (data / "config/overlays.json").exists()                   # the bug
    assert (install / "assets/overlays/gopher64.png").is_file()           # dead copy


# ── The shortcut's icon ─────────────────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_the_shortcut_uses_the_shipped_logo_and_falls_back_to_the_theme_icon(tmp_path):
    install = tmp_path / "GameCore"
    (install / "frontend/src/assets").mkdir(parents=True)
    (install / "frontend/src/assets/logo.png").write_bytes(b"\x89PNG")
    fn = _bash_function("launcher_icon")
    r = _run(fn + f'\nlauncher_icon "{install}"\n')
    assert r.stdout.strip() == str(install / "frontend/src/assets/logo.png"), r.stderr
    r = _run(fn + f'\nlauncher_icon "{tmp_path / "nothing"}"\n')
    assert r.stdout.strip() == "input-gaming"
    # And it is what the .desktop entry is written with.
    assert "Icon=$LAUNCHER_ICON" in ARCH.read_text(encoding="utf-8")


# ── The addons checkout ─────────────────────────────────────────────────────

def test_the_addons_checkout_is_not_pre_created_in_opt_on_a_split_box():
    """The CLI prefers /opt/gamecore-addons when it exists — that is what keeps
    every pre-split box working. Pre-creating it on a split box would make a
    fresh /userdata install carry mutable code in /opt again."""
    text = ARCH.read_text(encoding="utf-8")
    m = re.search(r'if \[\[ "\$GAMECORE_DATA" == "\$GAMECORE_PATH" \]\]; then\n'
                  r'\s*install -d -o "\$USER_NAME" -g "\$USER_NAME" /opt/gamecore-addons', text)
    assert m, "the /opt/gamecore-addons pre-creation is no longer conditional on the old layout"
    assert re.search(r'else\n\s*install -d -o "\$USER_NAME" -g "\$USER_NAME" "\$GAMECORE_DATA/addons"', text)


# ── The installer's data-path field ─────────────────────────────────────────

def _data_path_problem():
    """`data_path_problem` out of the wizard, without importing Qt."""
    tree = ast.parse(GUI.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "data_path_problem")
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(GUI), "exec"), ns)
    return ns["data_path_problem"]


@pytest.mark.parametrize("install,data,ok", [
    ("/opt/GameCore", "/userdata", True),
    ("/opt/GameCore", "/opt/GameCore", True),          # the old layout, still allowed
    ("/opt/GameCore", "/mnt/big/gamecore-data", True),
    ("/opt/GameCore", "/opt/GameCore/data", False),    # nested: an update replaces it
    ("/opt/GameCore", "/opt/GameCoreData", True),      # a sibling, not nested
    ("/opt/GameCore", "userdata", False),              # relative
    ("/opt/GameCore", "/user data", False),            # a space in a systemd unit
    ("/opt/GameCore", "/", False),
    ("/opt/GameCore", "/userdata/", False),            # arch.sh's _conf_path refuses it too
])
def test_the_wizard_refuses_a_data_path_arch_sh_could_not_use(install, data, ok):
    problem = _data_path_problem()(install, data)
    assert (problem == "") is ok, problem


def test_the_wizard_is_the_only_writer_of_the_data_root():
    """The wizard is the sole writer of GAMECORE_DATA.

    It used to be conditional: on the live ISO `gamecore-disk-install.sh` wrote
    the key itself, from the partition it had just mounted, and the wizard had
    to stay out of the way so the two could not disagree. With the ISO gone
    there is one installer and one writer, and the key is unconditional — a
    conf that reaches arch.sh without it silently falls back to the install
    directory, which is the old layout and not what the field says.
    """
    text = GUI.read_text(encoding="utf-8")
    assert re.search(r'f"GAMECORE_DATA=\{shlex\.quote\(c\[\'data\'\]\)\}",', text), \
        "the wizard no longer writes GAMECORE_DATA into the conf"
    assert "w.iso_src" not in text, \
        "an ISO branch is back in the wizard — there is only one installer now"
    assert 'self.data = QLineEdit("/userdata")' in text
    assert '"data": sysp.data.text().strip()' in text
