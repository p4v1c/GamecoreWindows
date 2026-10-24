"""The OTA updater, stage 1: fetch and check the release, then hand over to stage 2.

Run by the backend (`POST /api/update/apply`), which streams every line to the
interface. It changes nothing in the code root: it downloads the newest
release's `gamecore-ota.zip`, checks its SHA-256, unpacks it next to the code
root (`<code>.new`) and starts `update/apply-update.ps1` from the release just
unpacked, detached, from the work directory. Stage 2 stops GameCore, swaps the
folders, restarts and rolls back if the new code does not come up
(docs/architecture/13-release-and-ota.md).

Stdlib only, and nothing imported from the backend: the updater must work
whatever state the backend's code is in.

    windows.py install [--force]           stage 1 (what the backend runs)
    windows.py finish --root R --data D    after the swap: themes, catalogue merge
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "p4v1c/GamecoreWindows"
ASSET = "gamecore-ota.zip"
CHECKSUM_ASSET = ASSET + ".sha256"
API_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 60
CHUNK = 1024 * 1024
# The swap renames folders, so only the unpacked release needs room; twice its
# size leaves pip and the logs some space on a nearly full drive.
SPACE_FACTOR = 2
DEFAULT_STATE_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "GameCore"
TASK_PATH = "\\GameCore\\"
TASK_NAME = "Session"
CADDY_SERVICE = "GameCoreCaddy"
# Installed into the code root by install/steps, not shipped in a release:
# stage 2 moves them from the old root into the new one.
CARRIED_DIRS = ("python", ".venv", "caddy")
# Files a release must contain to be worth swapping in.
REQUIRED_FILES = ("backend/main.py", "shell/GameCore.exe", "update/apply-update.ps1",
                  "update/windows.py", "install/bin/gamecore-session", "VERSION")
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


class UpdateError(Exception):
    """A reason to stop before anything was changed."""


def say(message: str) -> None:
    print(f"[update] {message}", flush=True)


def version_key(tag: str) -> tuple[int, int, int]:
    """Tolerant x.y.z ordering: a malformed remote tag must never raise.

    Duplicated from routers/update.py on purpose: the updater imports nothing
    from the code it replaces.
    """
    nums = [int(n) for n in re.findall(r"\d+", tag or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))       # type: ignore[return-value]


def pick_assets(release: dict) -> tuple[str, str, str, str]:
    """(tag, zip url, checksum url, GitHub's digest) from a release. Raises UpdateError."""
    tag = str(release.get("tag_name") or "")
    if not tag:
        raise UpdateError("the latest release has no tag")
    assets = {a.get("name"): a for a in release.get("assets") or []}
    archive = assets.get(ASSET)
    if archive is None:
        raise UpdateError(f"release {tag} has no {ASSET}")
    checksum = assets.get(CHECKSUM_ASSET) or {}
    digest = str(archive.get("digest") or "")
    if not checksum and not digest.startswith("sha256:"):
        raise UpdateError(f"release {tag} publishes no SHA-256 for {ASSET}; refusing to install it")
    return tag, archive["browser_download_url"], checksum.get("browser_download_url", ""), digest


def expected_sha256(checksum_text: str, digest: str) -> str:
    """The archive's SHA-256, from the checksum asset and GitHub's digest. They must agree."""
    values = set()
    if checksum_text.strip():
        values.add(checksum_text.split()[0].lower())
    if digest.startswith("sha256:"):
        values.add(digest.split(":", 1)[1].lower())
    if len(values) != 1 or not re.fullmatch(r"[0-9a-f]{64}", next(iter(values))):
        raise UpdateError(f"the published SHA-256 values disagree or are malformed: {sorted(values)}")
    return values.pop()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _request(url: str, timeout: int):
    req = urllib.request.Request(url, headers={"User-Agent": "GameCore-updater",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_json(url: str) -> dict:
    try:
        with _request(url, API_TIMEOUT_S) as r:
            return json.load(r)
    except (OSError, ValueError) as e:
        raise UpdateError(f"cannot read {url}: {e}") from e


def fetch_text(url: str) -> str:
    try:
        with _request(url, API_TIMEOUT_S) as r:
            return r.read(4096).decode("ascii", errors="replace")
    except OSError as e:
        raise UpdateError(f"cannot read {url}: {e}") from e


def download(url: str, dest: Path) -> None:
    """Stream to `dest`, saying every tenth of the way."""
    try:
        with _request(url, DOWNLOAD_TIMEOUT_S) as r, open(dest, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done, step = 0, 1
            for block in iter(lambda: r.read(CHUNK), b""):
                out.write(block)
                done += len(block)
                if total and done * 10 >= total * step:
                    say(f"Downloaded {done * 100 // total} % ({done // CHUNK} MB)")
                    step = done * 10 // total + 1
    except OSError as e:
        raise UpdateError(f"download failed ({url}): {e}") from e


def safe_extract(archive: Path, dest: Path) -> int:
    """Unpack `archive` into `dest`; refuse any entry that would land outside it. Returns bytes."""
    root = dest.resolve()
    with zipfile.ZipFile(archive) as z:
        size = 0
        for info in z.infolist():
            target = (root / info.filename).resolve()
            if target != root and root not in target.parents:
                raise UpdateError(f"the archive holds a path outside its folder: {info.filename}")
            size += info.file_size
        z.extractall(root)
    return size


def unpacked_size(archive: Path) -> int:
    with zipfile.ZipFile(archive) as z:
        return sum(i.file_size for i in z.infolist())


def check_layout(tree: Path, tag: str) -> None:
    missing = [f for f in REQUIRED_FILES if not (tree / f).is_file()]
    if missing:
        raise UpdateError(f"the release is incomplete, missing: {', '.join(missing)}")
    shipped = (tree / "VERSION").read_text(encoding="utf-8").strip()
    if shipped != tag:
        raise UpdateError(f"the archive says {shipped}, the release says {tag}")


def check_free_space(code_root: Path, need: int) -> None:
    free = shutil.disk_usage(code_root.parent).free
    if free < need * SPACE_FACTOR:
        raise UpdateError(f"not enough free space next to {code_root}: need "
                          f"{need * SPACE_FACTOR // CHUNK} MB, have {free // CHUNK} MB")


def read_env_file(path: Path) -> dict[str, str]:
    """`KEY=value` lines of install/steps/write-session-env.ps1's file; empty when unreadable."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    pairs = (line.split("=", 1) for line in text.splitlines()
             if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def resolve_roots(env: dict[str, str], env_file: Path) -> tuple[Path, Path, str]:
    """Code root, data root and backend port: the backend's environment, else the session's file.

    Typed at a shell there is no GAMECORE_DATA, and guessing one would merge
    the catalogue into a folder nobody reads.
    """
    merged = {**read_env_file(env_file), **{k: v for k, v in env.items() if v}}
    root = merged.get("GAMECORE_PATH") or str(Path(__file__).resolve().parents[1])
    data = merged.get("GAMECORE_DATA")
    if not data:
        raise UpdateError(f"GAMECORE_DATA is not set and {env_file} does not say it")
    return Path(root), Path(data), merged.get("GAMECORE_BACKEND_PORT") or "8765"


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                         capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return f'"{pid}"' in out.stdout


def acquire_lock(lock: Path, pid: int, is_alive=_pid_alive) -> None:
    """One update at a time, across both stages. A lock whose owner died is taken over.

    The backend refuses a second /api/update/apply, but this holds when the
    updater is started another way too; stage 2 rewrites the pid and removes it.
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = int(lock.read_text(encoding="ascii").strip() or 0)
        except (OSError, ValueError):
            owner = 0
        if owner and owner != pid and is_alive(owner):
            raise UpdateError(f"another update is running (process {owner})") from None
        lock.write_text(str(pid), encoding="ascii")
        return
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(str(pid))


def build_plan(code_root: Path, data_root: Path, port: str, work: Path,
               current: str, tag: str) -> dict:
    """What stage 2 reads. Every path it touches is named here, nothing is derived there."""
    return {
        "code_root": str(code_root),
        "staging": str(code_root.with_name(code_root.name + ".new")),
        "previous": str(code_root.with_name(code_root.name + ".prev")),
        "failed": str(code_root.with_name(code_root.name + ".failed")),
        "data_root": str(data_root),
        "port": port,
        "from": current,
        "to": tag,
        "carry": list(CARRIED_DIRS),
        "task_path": TASK_PATH,
        "task_name": TASK_NAME,
        "caddy_service": CADDY_SERVICE,
        "lock": str(work / "update.lock"),
        "result": str(work / "last-update.json"),
        "log": str(data_root / "logs" / "update" / "update.log"),
    }


def launch_stage_two(script: Path, plan_file: Path, popen=subprocess.Popen):
    """Start apply-update.ps1 detached, out of the backend's job if Windows allows it.

    It kills the backend that started it, so it must not die with it: a logon
    task's job may refuse the breakaway, and then the plain start is the best left.
    """
    argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Plan", str(plan_file)]
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    kwargs = dict(cwd=str(plan_file.parent), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        return popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        say("Windows refused to start stage 2 outside this job; starting it inside.")
        return popen(argv, creationflags=flags, **kwargs)


def prepare(code_root: Path, work: Path, release: dict, current: str, force: bool) -> tuple[str, Path] | None:
    """Download, check and unpack the release next to the code root. None when up to date."""
    tag, zip_url, sum_url, digest = pick_assets(release)
    say(f"Installed: {current}. Latest: {tag}.")
    if version_key(tag) <= version_key(current) and not force:
        say("Already up to date.")
        return None
    archive = work / ASSET
    archive.unlink(missing_ok=True)
    say(f"Downloading {ASSET}...")
    download(zip_url, archive)
    want = expected_sha256(fetch_text(sum_url) if sum_url else "", digest)
    if sha256_file(archive) != want:
        raise UpdateError("the download does not match its published SHA-256")
    say("Checksum verified.")
    check_free_space(code_root, unpacked_size(archive))
    staging = code_root.with_name(code_root.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    say(f"Unpacking into {staging}...")
    safe_extract(archive, staging)
    check_layout(staging, tag)
    archive.unlink(missing_ok=True)
    return tag, staging


def install(args: argparse.Namespace) -> int:
    code_root, data_root, port = resolve_roots(dict(os.environ), args.env_file)
    if (code_root / ".git").exists():
        raise UpdateError(f"{code_root} is a source checkout: update it with git, not the OTA")
    work = args.work
    acquire_lock(work / "update.lock", os.getpid())
    version_file = code_root / "VERSION"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "v0.0.0"
    ready = prepare(code_root, work, fetch_json(args.api), current, args.force)
    if ready is None:
        (work / "update.lock").unlink(missing_ok=True)
        return 0
    tag, staging = ready
    plan_file = work / "plan.json"
    plan_file.write_text(json.dumps(build_plan(code_root, data_root, port, work, current, tag), indent=2),
                         encoding="utf-8", newline="\n")
    script = work / "apply-update.ps1"
    shutil.copyfile(staging / "update" / "apply-update.ps1", script)
    stage_two = launch_stage_two(script, plan_file)
    # Handed over: the lock is stage 2's now, so a second run waits for it.
    (work / "update.lock").write_text(str(stage_two.pid), encoding="ascii")
    say(f"{tag} is ready. GameCore restarts in a few seconds to install it; "
        f"if it does not come back, the previous version is put back by itself.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="windows.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install", help="fetch the newest release and hand it to stage 2")
    p.add_argument("--force", action="store_true", help="reinstall even when up to date")
    p.add_argument("--api", default=f"https://api.github.com/repos/{REPO}/releases/latest")
    p.add_argument("--work", type=Path, default=DEFAULT_STATE_DIR / "update")
    p.add_argument("--env-file", type=Path, default=DEFAULT_STATE_DIR / "session.env")
    f = sub.add_parser("finish", help="after the swap: deliver themes, merge the catalogue")
    f.add_argument("--root", type=Path, required=True)
    f.add_argument("--data", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "finish":
            from finish import finish                    # update/finish.py, the new release's
            return finish(args.root, args.data, say)
        return install(args)
    except UpdateError as e:
        say(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
