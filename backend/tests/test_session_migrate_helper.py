"""`gamecore-session-migrate` — where the migration learns what to migrate.

This is the helper the OTA starts through its one narrow sudoers rule. The rule
grants a unit and no parameters, so where the values come from IS the security
argument: root-owned sources only, never the caller.

It used to read one file, `/var/lib/gamecore/manifest.env`, and `source` it
under `set -e`. That file is not on every box — one installed before the
manifest existed has none — and the reference machine proved it the expensive
way: the preflight passed, the code was replaced, and *then* the migration died
on the missing file. The update aborted after the point of no return.

So there are two sources now, and the preflight asks this same script whether
either resolves, rather than testing for a file of its own.

Nothing here touches the machine: the manifest is a temporary file and
`systemctl` is a stub on PATH.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "install" / "bin" / "gamecore-session-migrate"
PREFLIGHT = REPO / "update" / "check-session-prerequisites.sh"


@pytest.fixture
def box(tmp_path):
    """An installation tree, a manifest that may or may not exist, and a
    systemd that says what the test wants."""
    install = tmp_path / "opt" / "GameCore"
    (install / "install" / "steps").mkdir(parents=True)
    step = install / "install" / "steps" / "setup-gamecore-session.sh"
    calls = tmp_path / "step-calls"
    step.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {calls}\n")
    step.chmod(0o755)

    binds = tmp_path / "bin"
    binds.mkdir()

    def systemd(environment: str = "", user: str = "") -> None:
        (binds / "systemctl").write_text(textwrap.dedent(f"""
            #!/usr/bin/env bash
            case "$*" in
              *"-p Environment"*) printf '%s\\n' '{environment}' ;;
              *"-p User"*)        printf '%s\\n' '{user}' ;;
              *)                  exit 0 ;;
            esac
        """).lstrip())
        (binds / "systemctl").chmod(0o755)

    systemd()   # a systemd that knows nothing, unless a test says otherwise

    def run(*args, manifest: str | None = None) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{binds}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "GAMECORE_MANIFEST": str(tmp_path / "manifest.env"),
            "GAMECORE_BACKEND_UNIT": "gamecore-backend.service",
        }
        if manifest is not None:
            (tmp_path / "manifest.env").write_text(manifest)
        return subprocess.run(["bash", str(HELPER), *args], env=env, text=True,
                              capture_output=True, timeout=60)

    return {"run": run, "systemd": systemd, "install": install, "calls": calls,
            "tmp": tmp_path}


def _called(box) -> list[str]:
    return box["calls"].read_text().splitlines() if box["calls"].exists() else []


# ── where the values come from ──────────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_the_installer_s_own_record_is_used_when_it_is_there(box):
    r = box["run"](manifest=(
        f"USER_NAME=player\nGAMECORE_PATH={box['install']}\n"
        "GAMECORE_DATA=/userdata\nWEB_PORT=9100\n"))
    assert r.returncode == 0, r.stderr
    assert _called(box) == [f"player {box['install']} /userdata 9100"]


@pytest.mark.linux_until_port(7)
def test_a_box_with_no_manifest_asks_the_installation_itself(box):
    """The case that cost a failed update: installed before the manifest
    existed. The backend's unit carries all four values, is root-owned, and is
    on every box that runs."""
    box["systemd"](
        environment=f"GAMECORE_PATH={box['install']} GAMECORE_DATA=/userdata "
                    "GAMECORE_BACKEND_PORT=8765",
        user="pavic")
    r = box["run"]()
    assert r.returncode == 0, r.stderr
    assert _called(box) == [f"pavic {box['install']} /userdata 8765"]
    assert "gamecore-backend.service" in r.stdout


@pytest.mark.linux_until_port(7)
def test_neither_source_is_refused_with_the_command_to_run(box):
    r = box["run"]()
    assert r.returncode != 0
    assert "cannot tell where this installation is" in r.stderr
    assert "setup-gamecore-session.sh" in r.stderr
    assert _called(box) == [], "it ran the step without knowing what to migrate"


@pytest.mark.linux_until_port(7)
def test_a_path_that_holds_no_installation_is_refused(box):
    box["systemd"](environment="GAMECORE_PATH=/nowhere", user="pavic")
    r = box["run"]()
    assert r.returncode != 0
    assert "is not there" in r.stderr
    assert _called(box) == []


@pytest.mark.linux_until_port(7)
def test_the_data_root_defaults_to_the_install_like_everywhere_else(box):
    box["systemd"](environment=f"GAMECORE_PATH={box['install']}", user="pavic")
    r = box["run"]()
    assert r.returncode == 0, r.stderr
    assert _called(box) == [f"pavic {box['install']} {box['install']} 8765"]


# ── the dry run the preflight leans on ──────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_check_resolves_and_does_nothing(box):
    box["systemd"](environment=f"GAMECORE_PATH={box['install']}", user="pavic")
    r = box["run"]("--check")
    assert r.returncode == 0, r.stderr
    assert "would migrate" in r.stdout and "user=pavic" in r.stdout
    assert _called(box) == [], "--check ran the migration"


def test_check_fails_where_the_migration_would_fail(box):
    r = box["run"]("--check")
    assert r.returncode != 0


def test_the_preflight_asks_this_script_rather_than_testing_for_a_file():
    """One implementation, so the two cannot disagree — which is exactly what
    happened: every file the preflight tested for was in place, it passed, and
    the migration then died on a manifest nobody had checked."""
    text = PREFLIGHT.read_text()
    assert "gamecore-session-migrate\" --check" in text or "--check" in text
    assert "manifest" in text.lower(), "the reason is not written down"


# ── the same disease, the other helper ──────────────────────────────────────

RESTART = REPO / "install" / "bin" / "gamecore-restart"


@pytest.fixture
def restart_box(tmp_path):
    """A machine where systemctl records what it was asked to restart."""
    binds = tmp_path / "bin"
    binds.mkdir()
    calls = tmp_path / "systemctl-calls"

    def systemd(user: str = "") -> None:
        (binds / "systemctl").write_text(textwrap.dedent(f"""
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {calls}
            case "$*" in
              *"-p User"*)     printf '%s\\n' '{user}' ;;
              *"is-active"*)   exit 1 ;;
              *)               exit 0 ;;
            esac
        """).lstrip())
        (binds / "systemctl").chmod(0o755)

    systemd()

    def run(manifest: str | None = None) -> subprocess.CompletedProcess:
        env = {"PATH": f"{binds}:/usr/bin:/bin", "HOME": str(tmp_path),
               "GAMECORE_MANIFEST": str(tmp_path / "manifest.env"),
               "GAMECORE_BACKEND_UNIT": "gamecore-backend.service"}
        if manifest is not None:
            (tmp_path / "manifest.env").write_text(manifest)
        return subprocess.run(["bash", str(RESTART)], env=env, text=True,
                              capture_output=True, timeout=60)

    return {"run": run, "systemd": systemd, "calls": calls}


@pytest.mark.linux_until_port(7)
def test_the_restart_works_on_a_box_with_no_manifest(restart_box):
    """The failure that ends an update after it has succeeded: the new version
    is written, the restart is asked for, it dies on a missing file, and the
    box keeps running the old code with nothing on screen to say why."""
    restart_box["systemd"](user="pavic")
    r = restart_box["run"]()
    assert r.returncode == 0, r.stderr
    asked = restart_box["calls"].read_text()
    assert "restart gamecore-backend.service" in asked


@pytest.mark.linux_until_port(7)
def test_the_manifest_still_wins_when_it_is_there(restart_box):
    restart_box["systemd"](user="from-the-unit")
    r = restart_box["run"](manifest="USER_NAME=from-the-manifest\n")
    assert r.returncode == 0, r.stderr
    assert "from-the-manifest@" in restart_box["calls"].read_text()


@pytest.mark.linux_until_port(7)
def test_no_user_anywhere_is_said_rather_than_guessed(restart_box):
    restart_box["systemd"](user="")
    r = restart_box["run"]()
    assert r.returncode != 0
    assert "cannot tell which user" in r.stderr
