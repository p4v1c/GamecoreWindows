"""One strategy per way of obtaining a binary.

    flatpak          appIds (ordered, first one the remote has) the EMU_FLATPAK loop
    github-asset     repo, asset, dest, magic, version?, sha256?   the DuckStation block
    github-archive   repo, asset, dest, entrypoint, requires  the Xenia block
    pacman           packages                                 pacman_optional

Callable from the bash installer (scripts/gamecore-provider.py) and, later,
from the backend at runtime — the same code either way, so a hot install cannot
behave differently from a fresh one.

Every provider feeds /var/lib/gamecore/, so `uninstall.sh` keeps removing only
what GameCore actually installed.

Nothing here is fatal by itself. A provider that fails costs one tile and says
so; the install carries on. That is not politeness — the Xenia block once
aborted the whole installer at 52 %, before a single systemd unit, sudoers rule
or autologin drop-in existed, and left a machine that was neither a working
install nor a clean one.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import pwd
except ImportError:     # Windows: the module still loads, so its OS-neutral parts
    pwd = None          # are tested; the Linux providers go at port step 7.

from ..catalog import appid
from . import manifest
from .fetch import extract, fetch_release_asset

log = logging.getLogger(__name__)


@dataclass
class Context:
    gamecore_path: Path
    user: str = ""
    dry_run: bool = False
    # Where the player's files are. None means "the same directory as the
    # install", which is every box created before the code/data split — the
    # default keeps those boxes byte-identical. Set on a box whose data has
    # moved, so the sandbox below can be told where the ROMs actually are.
    gamecore_data: Path | None = None

    @property
    def data_root(self) -> Path:
        return self.gamecore_data or self.gamecore_path


@dataclass
class Result:
    ok: bool
    message: str
    already: bool = False       # was already there; nothing was changed


def _chown(path: Path, user: str, recursive: bool = False) -> None:
    """Hand the artefact to the gaming user. Silent when not running as root:
    the backend path runs as that user already and has nothing to do."""
    if not user or os.geteuid() != 0:
        return
    try:
        info = pwd.getpwnam(user)
    except KeyError:
        log.warning("providers: no such user %r — ownership left as root", user)
        return
    targets = [path] if not recursive else [path, *path.rglob("*")]
    for p in targets:
        try:
            os.chown(p, info.pw_uid, info.pw_gid)
        except OSError:
            pass


def _pacman_install(packages: list[str], optional: bool = False) -> bool:
    """`--needed` so a re-run is a no-op. Records what was NOT already there
    BEFORE installing, so "already present" never lands on the removal list."""
    if not packages:
        return True
    for pkg in packages:
        manifest.record_new_package(pkg)
    try:
        r = subprocess.run(["pacman", "-S", "--noconfirm", "--needed", *packages],
                           capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("providers: pacman failed — %s", e)
        return False
    if r.returncode != 0 and not optional:
        log.warning("providers: pacman -S %s exited %s", " ".join(packages), r.returncode)
    return r.returncode == 0


# ── flatpak ────────────────────────────────────────────────────────────────

def remote_has(app_id: str, remote: str = "flathub", timeout: int = 120) -> bool:
    """Does the remote still offer this application?

    `flatpak install` on a dead id fails with the same generic non-zero exit as
    a network outage, so asking first is what lets the fallback distinguish
    "this candidate is gone, try the next" from "the network is down, stop".
    Without the distinction a flaky connection would silently install the
    second-choice emulator and nobody would know why.
    """
    try:
        r = subprocess.run(["flatpak", "remote-info", remote, app_id],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("providers: remote-info %s failed — %s", app_id, e)
        return False
    return r.returncode == 0


def pick_app_id(pack, ctx: Context) -> tuple[str, str]:
    """(app id to install, why). Empty id = no candidate survives.

    Order of questions matters:

      1. anything already installed wins outright. Re-running the installer on
         a box that fell back months ago must not drag it back to a candidate
         that has since returned — that would leave the emulator's config
         directory, its saves and its BIOS behind under the old id.
      2. otherwise the first candidate the remote still offers.

    A pack with one candidate skips the probe entirely: that is every pack
    today, and paying a `remote-info` round trip per emulator to confirm the
    only choice available would add a minute to every install for nothing.
    """
    app_ids = pack.app_ids
    for app_id in app_ids:
        if manifest.flatpak_installed(app_id):
            return app_id, "already installed"
    if len(app_ids) == 1:
        return app_ids[0], "the only candidate"
    for app_id in app_ids:
        if remote_has(app_id):
            if app_id != app_ids[0]:
                # The line the owner needs in the journal on the day it fires.
                log.warning("providers: %s: %s is gone from the remote — falling "
                            "back to %s", pack.id, app_ids[0], app_id)
            return app_id, "offered by the remote"
        log.warning("providers: %s: %s is not on the remote", pack.id, app_id)
    return "", "no declared app id is on the remote"


def install_flatpak(pack, ctx: Context) -> Result:
    if ctx.dry_run:
        # No probe: --dry-run must not need the network, and the first
        # candidate is what a box with nothing installed would end up with.
        return Result(True, f"would install flatpak {pack.app_ids[0]}")

    app_id, why = pick_app_id(pack, ctx)
    if not app_id:
        return Result(False,
                      f"{pack.id}: none of {', '.join(pack.app_ids)} is on the "
                      f"remote any more — its tile will be missing. The pack "
                      f"needs a new entry in install.appIds.")

    already = why == "already installed"
    if already:
        # Left out of the uninstall manifest on purpose: an emulator the owner
        # had before GameCore must never end up on the removal list.
        log.info("providers: %s already installed", app_id)
    else:
        try:
            r = subprocess.run(["flatpak", "install", "-y", "flathub", app_id],
                               capture_output=True, text=True, timeout=3600)
        except (OSError, subprocess.SubprocessError) as e:
            return Result(False, f"{app_id}: flatpak install failed — {e}")
        if r.returncode != 0:
            return Result(False, f"{app_id}: flatpak install exited {r.returncode}")
        manifest.record_new_flatpak(app_id)
        # What is installed just changed, and the seed deployment that follows
        # expands @FLATPAK_CONFIG@ through it. Without this, an install that
        # fell back to the second candidate wrote its config under the first.
        appid.probe()

    # The sandbox override applies either way — hence a manifest of its own.
    #
    # Its exit code used to be dropped on the floor, and with it the only
    # difference between an emulator that can read the ROM directory and one
    # that cannot. `flatpak override` failing left the override recorded as
    # applied and the installation reported as a success, so the catalogue said
    # "installed" and the first launch found no games, no pad, or no display —
    # each of which reads as a broken emulator rather than as a missing
    # permission.
    flags = sandbox_flags(pack, ctx)
    try:
        r = subprocess.run(["flatpak", "override", *flags, app_id],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("providers: could not set the sandbox override for %s — %s", app_id, e)
        return Result(False, f"{app_id}: the sandbox override could not be run — {e}")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        log.warning("providers: flatpak override for %s exited %d: %s",
                    app_id, r.returncode, detail[-1] if detail else "(no output)")
        return Result(False, f"{app_id}: flatpak override exited {r.returncode}"
                             + (f" — {detail[-1]}" if detail else ""))
    manifest.record_flatpak_override(app_id)

    return Result(True, f"{app_id} " + ("already present" if already else "installed"),
                  already=already)


def sandbox_flags(pack, ctx: Context) -> list[str]:
    """The pack's sandbox policy, or the emulator default.

    Two genuinely different policies exist — emulators get the ROM directory,
    a gamepad and X11; Stremio gets the whole filesystem and no X11 socket —
    so it is declared per pack rather than hardcoded in two unrelated places.
    """
    sb = pack.data.get("sandbox")
    if sb is None:
        # The ROMs are under the DATA root, and the sandbox is a list of
        # literal paths: granting the install root alone was correct for
        # exactly as long as the data lived inside it. The day it moved, every
        # Flatpak emulator kept launching, was handed a path under /userdata,
        # and reported the game missing — the file was there, the sandbox
        # could not see it. Both roots are granted when they differ: `lib/`
        # (native binaries a pack may point at) and the seeds are still under
        # the install.
        roots = [ctx.gamecore_path]
        if ctx.data_root != ctx.gamecore_path:
            roots.append(ctx.data_root)
        return [*(f"--filesystem={r}" for r in roots), "--device=all", "--socket=x11"]
    return ([f"--filesystem={v}" for v in sb.get("filesystem", [])]
            + [f"--device={v}" for v in sb.get("device", [])]
            + [f"--socket={v}" for v in sb.get("socket", [])])


# ── github-asset (DuckStation) ─────────────────────────────────────────────

def install_github_asset(pack, ctx: Context) -> Result:
    spec = pack.data["install"]
    dest = ctx.gamecore_path / spec["dest"]
    magic = spec.get("magic")

    if ctx.dry_run:
        return Result(True, f"would fetch {spec['repo']}:{spec['asset']} → {dest}")

    # `-f dest` alone treats a truncated download or a saved HTML error page as
    # "installed" forever. Check the magic instead.
    from .fetch import _magic_ok
    if dest.is_file() and _magic_ok(dest, magic):
        return Result(True, f"{pack.id} already present", already=True)
    dest.unlink(missing_ok=True)

    ok = fetch_release_asset(spec["repo"], spec["asset"], dest, magic=magic,
                             version=spec.get("version", "latest"),
                             sha256=spec.get("sha256"))
    if not ok:
        return Result(False,
                      f"{pack.id} could not be downloaded — its tile will be missing. "
                      f"Re-run the installer once the network is back; "
                      f"nothing else is affected.")

    if mode := spec.get("mode"):
        dest.chmod(int(mode, 8))
    _chown(dest, ctx.user)
    return Result(True, f"{pack.id} installed → {spec['dest']}")


# ── github-archive (Xenia) ─────────────────────────────────────────────────

def install_github_archive(pack, ctx: Context) -> Result:
    spec = pack.data["install"]
    into = ctx.gamecore_path / spec["dest"]
    entry = into / spec["entrypoint"]

    if ctx.dry_run:
        return Result(True, f"would fetch {spec['repo']}:{spec['asset']} → {into}")

    if entry.is_file():
        return Result(True, f"{pack.id} already present", already=True)

    # Tools needed to extract and to run it. Installed before the download so a
    # successful fetch is never wasted on a missing unzip.
    if requires := spec.get("requires"):
        _pacman_install(list(requires))

    # mktemp, not a fixed /tmp name: this runs as root and a predictable path
    # is a symlink target waiting to happen.
    fd, tmp_name = tempfile.mkstemp(prefix="gamecore-pack-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        ok = fetch_release_asset(spec["repo"], spec["asset"], tmp,
                                 magic=spec.get("magic"),
                                 pattern=spec.get("assetPattern"),
                                 version=spec.get("version", "latest"),
                                 sha256=spec.get("sha256"))
        if not ok:
            return Result(False,
                          f"{pack.id} could not be downloaded — its tile will be "
                          f"missing. Re-run the installer once the network is back; "
                          f"nothing else is affected.")
        if not extract(tmp, into):
            return Result(False, f"{pack.id}: extraction failed — its tile will be missing")
    finally:
        tmp.unlink(missing_ok=True)

    _chown(into, ctx.user, recursive=True)
    if not entry.is_file():
        return Result(False,
                      f"{pack.id}: {spec['entrypoint']} not found after extraction "
                      f"— it will not launch")
    return Result(True, f"{pack.id} installed → {spec['dest']}/")


# ── pacman ─────────────────────────────────────────────────────────────────

def install_pacman(pack, ctx: Context) -> Result:
    packages = list(pack.data["install"]["packages"])
    if ctx.dry_run:
        return Result(True, f"would install {' '.join(packages)}")
    ok = _pacman_install(packages)
    return Result(ok, f"{pack.id}: " + ("installed " if ok else "could not install ")
                  + " ".join(packages))


PROVIDERS = {
    "flatpak": install_flatpak,
    "github-asset": install_github_asset,
    "github-archive": install_github_archive,
    "pacman": install_pacman,
}


def install(pack, ctx: Context) -> Result:
    """Run the pack's provider. A pack with no `install` block installs nothing
    — youtube is a Firefox profile, there is no artefact to obtain."""
    spec = pack.data.get("install")
    if not spec:
        return Result(True, f"{pack.id}: nothing to install", already=True)
    provider = PROVIDERS.get(spec["provider"])
    if provider is None:
        return Result(False, f"{pack.id}: unknown provider {spec['provider']!r}")
    try:
        return provider(pack, ctx)
    except Exception as e:                       # never take the install down
        log.exception("providers: %s failed", pack.id)
        return Result(False, f"{pack.id}: {e.__class__.__name__} — {e}")
