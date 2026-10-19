"""`install/steps/build-media-index.sh` — the step that gives a fresh box a
metadata source at all.

Run for real, against a throwaway GAMECORE_PATH and a stand-in gamescrape.py
that records how it was called. Nothing here downloads 234 MB, and nothing here
touches a real installation.

What is worth pinning, and why each one is a failure that happened or would
never be noticed:

  · **it never fails the install.** arch.sh warns and carries on for a dozen
    recoverable failures on purpose — a missing description is a degraded box,
    an aborted installer at 80 % is a machine that is neither installed nor
    clean. Every path through this script must exit 0.
  · **GAMECORE_PATH reaches gamescrape.** That single variable is what makes
    the index land in the installation instead of the invoking user's
    ~/.cache — the exact bug found on the reference box, where the tier was
    silently off with the index sitting on disk two directories away.
  · **it is idempotent.** Re-running the installer is documented as safe, and
    re-downloading 234 MB because someone ran it twice is not safe, it is rude.
  · **a stand-in that lies is caught.** A build that exits 0 and writes nothing
    is the shape of failure hardest to see, so the script checks the file
    rather than the exit code alone.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

STEP = REPO / "install/steps/build-media-index.sh"


@pytest.fixture
def box(tmp_path):
    """A throwaway GAMECORE_PATH with a gamescrape.py that does as it is told.

    Returns (root, run, calls_file). `run` executes the step and gives back the
    CompletedProcess; `calls_file` records the argv and environment the
    stand-in was handed.
    """
    root = tmp_path / "gamecore"
    gs_dir = root / "backend/services/gamemedia"
    gs_dir.mkdir(parents=True)
    (root / "emu").mkdir()
    calls = tmp_path / "calls.txt"

    def install_stub(body: str) -> None:
        stub = gs_dir / "gamescrape.py"
        stub.write_text(body, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def run(extra_env: dict | None = None) -> subprocess.CompletedProcess:
        # GAMECORE_DATA explicitly, and equal to the root: the suite exports one
        # of its own (conftest.py, so no test can reach a real box), and the
        # step reads it. Left to the environment, the index would be looked for
        # in the pytest sandbox and this box's own answers would never be seen.
        env = {**os.environ, "CALLS": str(calls), "GAMECORE_DATA": str(root)}
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", str(STEP), str(root), os.environ.get("USER", "nobody")],
            capture_output=True, text=True, env=env, timeout=120)

    return root, run, calls, install_stub


# A stand-in that writes the index it is asked for, and records how it was told.
_STUB_OK = """\
import os, sys, pathlib
with open(os.environ["CALLS"], "a") as f:
    f.write(repr(sys.argv[1:]) + "|" + os.environ.get("GAMECORE_PATH", "") + "\\n")
d = pathlib.Path(os.environ["GAMECORE_PATH"]) / "emu" / "gamescrape"
d.mkdir(parents=True, exist_ok=True)
(d / "launchbox.sqlite").write_bytes(b"SQLite format 3\\x00" + b"x" * 512)
"""

_STUB_FAILS = "import sys; sys.exit(1)\n"

# Exit 0 and write nothing: green, and the tier is still absent.
_STUB_LIES = "pass\n"


@pytest.mark.linux_until_port(7)
def test_it_builds_the_index_and_passes_gamecore_path(box):
    """The whole point. Without GAMECORE_PATH the index goes to ~/.cache."""
    root, run, calls, stub = box
    stub(_STUB_OK)

    r = run()
    assert r.returncode == 0, r.stderr
    assert (root / "emu/gamescrape/launchbox.sqlite").is_file()

    argv, gamecore_path = calls.read_text(encoding="utf-8").strip().split("|")
    assert "--refresh" in argv
    assert gamecore_path == str(root), (
        "gamescrape was run without GAMECORE_PATH pointing at the install — "
        "the index would land in the invoking user's ~/.cache")


@pytest.mark.linux_until_port(7)
def test_a_second_run_does_not_download_it_again(box):
    """Re-running the installer is documented as safe."""
    root, run, calls, stub = box
    stub(_STUB_OK)

    assert run().returncode == 0
    assert run().returncode == 0
    assert len(calls.read_text(encoding="utf-8").strip().splitlines()) == 1, (
        "the second run rebuilt the index")


@pytest.mark.linux_until_port(7)
def test_a_failed_build_never_fails_the_install(box):
    """A missing description is a degraded box. An aborted installer is not."""
    root, run, calls, stub = box
    stub(_STUB_FAILS)

    r = run()
    assert r.returncode == 0, "the step took the install down with it"
    assert "could not be built" in r.stdout
    # And it says how to get it back rather than leaving the reader guessing.
    assert "--refresh" in r.stdout


@pytest.mark.linux_until_port(7)
def test_a_build_that_reports_success_and_writes_nothing_is_caught(box):
    """Exit 0 with no file is the failure nobody sees.

    The script checks the artefact, not the exit code — the same rule the
    installer applies to a downloaded emulator's magic bytes.
    """
    root, run, calls, stub = box
    stub(_STUB_LIES)

    r = run()
    assert r.returncode == 0
    assert "wrote no index" in r.stdout


@pytest.mark.linux_until_port(7)
def test_the_skip_variable_is_honoured_and_says_how_to_undo_it(box):
    root, run, calls, stub = box
    stub(_STUB_OK)

    r = run({"GAMECORE_SKIP_MEDIA_INDEX": "1"})
    assert r.returncode == 0
    assert not (root / "emu/gamescrape/launchbox.sqlite").exists()
    assert not calls.exists(), "gamescrape ran despite the skip"
    assert "--refresh" in r.stdout, "skipping must print the way back"


@pytest.mark.linux_until_port(7)
def test_a_missing_gamescrape_is_reported_not_fatal(box):
    """A partial tree — an interrupted rsync, a hand-assembled install."""
    root, run, calls, stub = box
    (root / "backend/services/gamemedia/gamescrape.py").unlink(missing_ok=True)

    r = run()
    assert r.returncode == 0
    assert "not found" in r.stdout


# ── the wiring in arch.sh ──────────────────────────────────────────────────

def test_arch_calls_the_step_and_only_for_a_full_install():
    """234 MB is not what `--minimal` asked for.

    Read from arch.sh rather than assumed: the call and its guard are one fact,
    and a guard that drifts from the call is how a minimal install quietly
    grows a quarter-gigabyte download.
    """
    text = (REPO / "install/arch.sh").read_text(encoding="utf-8")
    assert "install/steps/build-media-index.sh" in text, (
        "arch.sh no longer builds the media index — a fresh box then has no "
        "metadata source unless ScreenScraper credentials are configured")

    call_at = text.index("install/steps/build-media-index.sh")
    guard = text[max(0, call_at - 400):call_at]
    assert '"$MODE" == "full"' in guard, (
        "the media index call lost its --minimal guard")
