"""update/windows.py, stage 1: the release is checked before anything is touched.

The network is a local HTTP server serving a fake release; the code root, the
data root and the work directory are tmp_path. Stage 2 is never started: its
launcher is replaced, and the test reads what it would have been handed.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import sys
import threading
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "update"))

import windows  # noqa: E402

TAG = "v0.2.0"
RELEASE_FILES = {name: b"x" for name in windows.REQUIRED_FILES if name != "VERSION"}


def make_archive(path: Path, files: dict[str, bytes], version: str = TAG) -> bytes:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("VERSION", version + "\n")
    return path.read_bytes()


class _Server:
    """Serves /release.json, /ota.zip and /ota.zip.sha256 from a dict."""

    def __init__(self, routes: dict[str, bytes]):
        routes_ = routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's name
                body = routes_.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A code root at v0.1.0, a data root, a work dir; a release server; stage 2 recorded."""
    code = tmp_path / "GameCore"
    (code / "update").mkdir(parents=True)
    (code / "VERSION").write_text("v0.1.0\n")
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "state" / "update"
    archive = make_archive(tmp_path / "ota.zip", RELEASE_FILES)
    digest = hashlib.sha256(archive).hexdigest()
    routes = {"/ota.zip": archive, "/ota.zip.sha256": f"{digest}  gamecore-ota.zip\n".encode()}
    server = _Server(routes)

    def release(tag=TAG, with_sum=True, digest_field=None):
        assets = [{"name": windows.ASSET, "browser_download_url": server.base + "/ota.zip"}]
        if digest_field:
            assets[0]["digest"] = digest_field
        if with_sum:
            assets.append({"name": windows.CHECKSUM_ASSET, "browser_download_url": server.base + "/ota.zip.sha256"})
        routes["/release.json"] = json.dumps({"tag_name": tag, "assets": assets}).encode()

    launched = []

    class FakeProcess:
        pid = 4242

    def fake_launch(script, plan_file, popen=None):
        launched.append((script, json.loads(plan_file.read_text())))
        return FakeProcess()

    monkeypatch.setattr(windows, "launch_stage_two", fake_launch)
    monkeypatch.setenv("GAMECORE_PATH", str(code))
    monkeypatch.setenv("GAMECORE_DATA", str(data))
    monkeypatch.setenv("GAMECORE_BACKEND_PORT", "8799")
    release()

    def run(*extra):
        return windows.main(["install", "--api", server.base + "/release.json", "--work", str(work),
                             "--env-file", str(tmp_path / "absent.env"), *extra])

    yield {"code": code, "data": data, "work": work, "routes": routes, "release": release,
           "run": run, "launched": launched}
    server.close()


def test_a_newer_release_is_unpacked_beside_the_code_root_and_handed_over(box, capsys):
    assert box["run"]() == 0
    staging = box["code"].with_name("GameCore.new")
    assert (staging / "shell" / "GameCore.exe").is_file()
    assert (staging / "VERSION").read_text().strip() == TAG
    script, plan = box["launched"][0]
    assert script == box["work"] / "apply-update.ps1" and script.is_file()
    assert plan["code_root"] == str(box["code"]) and plan["staging"] == str(staging)
    assert plan["previous"] == str(box["code"].with_name("GameCore.prev"))
    assert (plan["from"], plan["to"], plan["port"]) == ("v0.1.0", TAG, "8799")
    assert plan["carry"] == ["python", ".venv", "caddy"]
    assert plan["log"] == str(box["data"] / "logs" / "update" / "update.log")
    # The code root itself is untouched by stage 1.
    assert sorted(p.name for p in box["code"].iterdir()) == ["VERSION", "update"]
    # Handed over: the lock now names stage 2.
    assert (box["work"] / "update.lock").read_text() == "4242"
    assert "restarts in a few seconds" in capsys.readouterr().out


def test_an_up_to_date_box_downloads_nothing(box, capsys):
    box["release"](tag="v0.1.0")
    assert box["run"]() == 0
    assert box["launched"] == []
    assert not box["code"].with_name("GameCore.new").exists()
    assert not (box["work"] / "update.lock").exists()
    assert "Already up to date" in capsys.readouterr().out


def test_a_download_that_does_not_match_its_checksum_is_refused(box, capsys):
    box["routes"]["/ota.zip.sha256"] = b"0" * 64 + b"  gamecore-ota.zip\n"
    assert box["run"]() == 1
    assert box["launched"] == []
    assert not box["code"].with_name("GameCore.new").exists()
    assert "does not match its published SHA-256" in capsys.readouterr().out


def test_a_release_without_any_checksum_is_refused(box, capsys):
    box["release"](with_sum=False)
    assert box["run"]() == 1
    assert "publishes no SHA-256" in capsys.readouterr().out


def test_github_s_own_digest_is_enough_and_must_agree_with_the_file(box):
    digest = hashlib.sha256(box["routes"]["/ota.zip"]).hexdigest()
    box["release"](with_sum=False, digest_field=f"sha256:{digest}")
    assert box["run"]() == 0
    with pytest.raises(windows.UpdateError, match="disagree"):
        windows.expected_sha256(f"{'1' * 64}  x", f"sha256:{digest}")


def test_an_archive_whose_version_is_not_the_release_tag_is_refused(box, tmp_path, capsys):
    archive = make_archive(tmp_path / "other.zip", RELEASE_FILES, version="v0.1.5")
    box["routes"]["/ota.zip"] = archive
    box["routes"]["/ota.zip.sha256"] = hashlib.sha256(archive).hexdigest().encode()
    assert box["run"]() == 1
    assert "the archive says v0.1.5, the release says v0.2.0" in capsys.readouterr().out
    assert box["launched"] == []


def test_an_incomplete_release_is_refused(box, tmp_path, capsys):
    files = {k: v for k, v in RELEASE_FILES.items() if k != "shell/GameCore.exe"}
    archive = make_archive(tmp_path / "partial.zip", files)
    box["routes"]["/ota.zip"] = archive
    box["routes"]["/ota.zip.sha256"] = hashlib.sha256(archive).hexdigest().encode()
    assert box["run"]() == 1
    assert "missing: shell/GameCore.exe" in capsys.readouterr().out


def test_a_corrupt_archive_is_one_error_line_not_a_traceback(box, capsys):
    blob = b"not a zip at all"
    box["routes"]["/ota.zip"] = blob
    box["routes"]["/ota.zip.sha256"] = hashlib.sha256(blob).hexdigest().encode()
    assert box["run"]() == 1
    assert "[update] ERROR:" in capsys.readouterr().out
    assert box["launched"] == []


def test_an_entry_outside_the_archive_folder_is_refused(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside.txt", "x")
    with pytest.raises(windows.UpdateError, match="outside its folder"):
        windows.safe_extract(archive, tmp_path / "dest")
    assert not (tmp_path / "outside.txt").exists()


def test_a_source_checkout_is_never_swapped(box, capsys):
    (box["code"] / ".git").mkdir()
    assert box["run"]() == 1
    assert "source checkout" in capsys.readouterr().out
    assert box["launched"] == []


def test_a_running_update_blocks_a_second_one_and_a_dead_one_does_not(tmp_path):
    lock = tmp_path / "update.lock"
    windows.acquire_lock(lock, 100, is_alive=lambda pid: True)
    with pytest.raises(windows.UpdateError, match="process 100"):
        windows.acquire_lock(lock, 200, is_alive=lambda pid: True)
    windows.acquire_lock(lock, 200, is_alive=lambda pid: False)
    assert lock.read_text() == "200"


def test_not_enough_space_is_said_before_unpacking(box, monkeypatch, capsys):
    monkeypatch.setattr(windows.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    assert box["run"]() == 1
    assert "not enough free space" in capsys.readouterr().out
    assert not box["code"].with_name("GameCore.new").exists()


def test_typed_at_a_shell_the_roots_come_from_the_session_file(tmp_path):
    env_file = tmp_path / "session.env"
    env_file.write_text("\ufeffGAMECORE_PATH=C:\\GC\n# note\nGAMECORE_DATA=D:\\Data\nGAMECORE_BACKEND_PORT=8800\n",
                        encoding="utf-8")
    assert windows.resolve_roots({}, env_file) == (Path("C:\\GC"), Path("D:\\Data"), "8800")
    # The process environment wins over the file.
    assert windows.resolve_roots({"GAMECORE_DATA": "E:\\Mine"}, env_file)[1] == Path("E:\\Mine")
    with pytest.raises(windows.UpdateError, match="GAMECORE_DATA"):
        windows.resolve_roots({}, tmp_path / "absent.env")


def test_stage_two_breaks_away_from_the_job_and_falls_back_when_refused(tmp_path):
    calls = []

    def popen(argv, creationflags, **kwargs):
        calls.append(creationflags)
        if creationflags & windows.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("breakaway not allowed")
        return "started"

    plan = tmp_path / "plan.json"
    assert windows.launch_stage_two(tmp_path / "apply-update.ps1", plan, popen=popen) == "started"
    assert calls[0] & windows.CREATE_BREAKAWAY_FROM_JOB and not calls[1] & windows.CREATE_BREAKAWAY_FROM_JOB
    assert all(c & windows.CREATE_NO_WINDOW for c in calls)
