"""Applying what a pack declares beyond obtaining its binary.

`providers.py` answers "where does the artefact come from". This answers
everything after: the git checkouts an app needs beside it, the files it drops,
the user unit it runs under, and the two-step ceremonies (generate a cert, then
trust it) that no amount of declaration can turn into data.

    packages   → pacman, recorded in the uninstall manifest
    install    → providers.py
    sources    → git clone, or ff-only sync of an existing checkout
    files      → src verbatim, or template with the tokens below
    usb        → one udev rules file per pack, written and NOT activated
    services   → user unit + default.target.wants symlink
    postInstall→ pack-local script, as the user, bounded, never fatal

Order matters and is fixed: a file written into a checkout needs the checkout,
a unit needs its ExecStart to exist, and a post-install step needs all three.

Tokens, in `dest` and inside templates:

    @HOME@            the gaming user's home
    @USER@            the gaming user's name
    @GAMECORE_PATH@   the install directory
    @<SECRET_KEY>@    any key the pack declares under `secrets`

Two rules this module does not get to decide:

  · **Nothing here runs for a local pack.** `catalog/loader.py` strips
    `sources`, `services`, `postInstall` and `packages` from anything found in
    `config/catalog.d/` before it ever becomes a Pack, so a directory dropped on
    a box cannot reach this code. The operator lifts that with
    GAMECORE_TRUST_LOCAL_PACKS=1, and the loader logs it at every load.
  · **A pack may only read from its own directory.** `src`, `template`, `unit`
    and `run` are resolved against pack.path and rejected if they land outside
    it — that is what keeps "one directory per app" from becoming "one
    directory, plus whatever ../.. reaches".

Nothing raises. A failure costs the app its tile and says which; it never ends
the install. The Xenia block once aborted the whole installer at 52 %, and left
a machine that was neither a working install nor a clean one.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pwd
except ImportError:     # Windows: the module still loads, so its OS-neutral parts
    pwd = None          # are tested; the Linux providers go at port step 7.

from .. import usb_devices
from .providers import Context, Result, _chown, _pacman_install, install
from .host_access import apply_host_access

log = logging.getLogger(__name__)

GIT_CLONE_TIMEOUT = 300
GIT_SYNC_TIMEOUT = 120


@dataclass
class AppContext(Context):
    """A Context that also knows the things an app install needs.

    `secrets` carries what the wizard collected (TWITCH_CLIENT_ID…). It is
    passed in rather than read from the environment at each use so that a dry
    run, a test and a real install all see exactly the same values.
    """
    user_home: Path = Path("/root")
    secrets: dict[str, str] = field(default_factory=dict)

    # Where a pack's `usb[].udevRule` lands. A field and not a constant so a
    # test can point it at tmp_path: a suite that had to write into the real
    # /etc/udev/rules.d to cover this would either need root or be skipped, and
    # a skipped test is how rule generation ships unexercised.
    udev_rules_dir: Path = Path("/etc/udev/rules.d")

    @property
    def unit_dir(self) -> Path:
        return self.user_home / ".config" / "systemd" / "user"


def _tokens(pack, ctx: AppContext) -> dict[str, str]:
    t = {
        "@HOME@": str(ctx.user_home),
        "@USER@": ctx.user,
        "@GAMECORE_PATH@": str(ctx.gamecore_path),
        "@GAMECORE_DATA@": str(ctx.data_root),
    }
    for spec in pack.data.get("secrets", []):
        key = spec["key"]
        t[f"@{key}@"] = ctx.secrets.get(key, "")
    return t


def _expand(text: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


def _wanted(entry: dict, ctx: AppContext) -> bool:
    """Evaluate `when: secrets.KEY` / `!secrets.KEY`.

    An entry with no `when` always applies. This is what lets one pack ship both
    a real config and a demo one and pick between them without a branch in the
    installer — the twitch pack's whole reason for having two `files` entries.
    """
    cond = entry.get("when")
    if not cond:
        return True
    negated = cond.startswith("!")
    key = cond.lstrip("!").split(".", 1)[1]
    return bool(ctx.secrets.get(key)) is not negated


def _pack_file(pack, rel: str) -> Path:
    """Resolve a pack-relative path, refusing anything outside the pack.

    `..` in a pack.json is not a use case; it is the one thing that turns a
    directory drop into a read of the rest of the disk.
    """
    root = pack.path.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{pack.id}: {rel!r} points outside the pack directory")
    return target


def _as_user(ctx: AppContext, argv: list[str]) -> list[str]:
    """Run as the gaming user when we are root, unchanged otherwise.

    -H so $HOME is theirs: every destination an app writes to is derived from it.
    """
    if os.geteuid() != 0 or not ctx.user:
        return argv
    return ["sudo", "-u", ctx.user, "-H", *argv]


def _run_as_user(ctx: AppContext, argv: list[str], env: dict[str, str],
                 timeout: int, cwd: Path) -> subprocess.CompletedProcess:
    """Same, with an environment — and the environment never touches argv.

    `sudo -u <user> env KEY=value …` would have been shorter and puts every
    value in /proc/<pid>/cmdline, which is world-readable. One of those values
    is TWITCH_CLIENT_SECRET. --preserve-env names the variables and lets sudo
    carry them across from our own environment instead.

    HOME is deliberately not preserved: -H already sets it to the target user's
    home, and preserving it too makes which one wins a question about sudo's
    option order rather than about this code.
    """
    merged = {**os.environ, **env}
    if os.geteuid() == 0 and ctx.user:
        carried = [k for k in env if k != "HOME"]
        argv = ["sudo", f"--preserve-env={','.join(carried)}", "-u", ctx.user, "-H", *argv]
    return subprocess.run(argv, env=merged, capture_output=True, text=True,
                          timeout=timeout, cwd=str(cwd))


def _own(path: Path, owner: str, ctx: AppContext, recursive: bool = False) -> None:
    if owner == "user":
        _chown(path, ctx.user, recursive=recursive)


# ── packages ───────────────────────────────────────────────────────────────

def apply_packages(pack, ctx: AppContext) -> list[Result]:
    packages = list((pack.data.get("packages") or {}).get("pacman", []))
    if not packages:
        return []
    if ctx.dry_run:
        return [Result(True, f"{pack.id}: would install {' '.join(packages)}")]
    ok = _pacman_install(packages, optional=True)
    return [Result(ok, f"{pack.id}: " + ("dependencies " if ok else "could not install ")
                   + " ".join(packages))]


# ── sources ────────────────────────────────────────────────────────────────

def _git(argv: list[str], timeout: int) -> bool:
    try:
        r = subprocess.run(["git", *argv], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("apply: git %s — %s", " ".join(argv), e)
        return False
    return r.returncode == 0


def apply_sources(pack, ctx: AppContext) -> list[Result]:
    """Clone when absent, fast-forward otherwise, and never over local changes.

    Cloning only when the directory is absent left every checkout pinned to the
    commit it was first installed at, for ever — a fix shipped upstream reached
    nothing, because OTA never touches /opt outside GAMECORE_PATH. Syncing over
    a dirty tree is the other failure: an installer that resets behind the
    owner's back is how a hand-applied fix disappears. It says what it did not
    do instead.
    """
    out: list[Result] = []
    for src in pack.data.get("sources", []):
        dest = Path(_expand(src["dest"], _tokens(pack, ctx)))
        owner = src.get("owner", "root")
        if ctx.dry_run:
            out.append(Result(True, f"{pack.id}: would sync {src['git']} → {dest}"))
            continue
        if not (dest / ".git").is_dir():
            if dest.exists() and any(dest.iterdir()):
                out.append(Result(False, f"{pack.id}: {dest} exists and is not a checkout — left untouched"))
                continue
            if _git(["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30",
                     "clone", "-q", src["git"], str(dest)], GIT_CLONE_TIMEOUT):
                _own(dest, owner, ctx, recursive=True)
                out.append(Result(True, f"{pack.id}: cloned → {dest}"))
            else:
                shutil.rmtree(dest, ignore_errors=True)   # never a half-written checkout
                out.append(Result(False, f"{pack.id}: clone of {src['git']} failed — its tile will not start"))
            continue
        dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain"],
                               capture_output=True, text=True)
        if dirty.returncode == 0 and dirty.stdout.strip():
            out.append(Result(True, f"{pack.id}: local changes in {dest} — NOT updated", already=True))
            continue
        if _git(["-C", str(dest), "pull", "-q", "--ff-only"], GIT_SYNC_TIMEOUT):
            _own(dest, owner, ctx, recursive=True)
            out.append(Result(True, f"{pack.id}: {dest} up to date"))
        else:
            out.append(Result(True, f"{pack.id}: could not fast-forward {dest} — left at its commit",
                              already=True))
    return out


# ── files ──────────────────────────────────────────────────────────────────

def apply_files(pack, ctx: AppContext) -> list[Result]:
    out: list[Result] = []
    tokens = _tokens(pack, ctx)
    for spec in pack.data.get("files", []):
        if not _wanted(spec, ctx):
            continue
        dest = Path(_expand(spec["dest"], tokens))
        # `ifAbsent` is for the file the owner is expected to hand-edit — the
        # demo EmberTV config. Re-running the installer is documented as safe,
        # and it must not mean "your edits are gone".
        if spec.get("ifAbsent") and dest.exists():
            out.append(Result(True, f"{pack.id}: {dest} kept as it is", already=True))
            continue
        rel = spec.get("src") or spec["template"]
        try:
            source = _pack_file(pack, rel)
        except ValueError as e:
            out.append(Result(False, str(e)))
            continue
        if not source.is_file():
            out.append(Result(False, f"{pack.id}: {rel} declared but not in the pack — {dest.name} not written"))
            continue
        if ctx.dry_run:
            out.append(Result(True, f"{pack.id}: would write {dest}"))
            continue
        try:
            # The parent belongs to the owner too: a root-owned
            # ~/.mozilla/firefox/<profile> breaks certutil (SEC_ERROR_BAD_DATABASE)
            # and Firefox's own caches, and the profile is created by this very
            # line the first time round.
            if spec.get("owner") == "user":
                subprocess.run(_as_user(ctx, ["mkdir", "-p", str(dest.parent)]),
                               check=True, capture_output=True, timeout=30)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
            _own(dest.parent, spec.get("owner", "root"), ctx)
            if spec.get("template"):
                dest.write_text(_expand(source.read_text(encoding="utf-8"), tokens),
                                encoding="utf-8")
            else:
                shutil.copyfile(source, dest)
            os.chmod(dest, int(spec.get("mode", "644"), 8))
            _own(dest, spec.get("owner", "root"), ctx)
        except (OSError, subprocess.SubprocessError) as e:
            out.append(Result(False, f"{pack.id}: could not write {dest} — {e}"))
            continue
        out.append(Result(True, f"{pack.id}: {dest}"))
    return out


# ── services ───────────────────────────────────────────────────────────────

def apply_services(pack, ctx: AppContext) -> list[Result]:
    """User units, and only user units — the schema allows nothing else.

    The whole tree is created AS THE USER. This was the one place in the shell
    installer that made something under $USER_HOME as root, and on a
    distribution whose /etc/skel has no .config it left ~/.config itself
    root-owned: the Electron shell could not write its profile, gamecore-ui
    looped on Restart=on-failure, and the kiosk never came up — while the
    installer printed "Installation complete".
    """
    out: list[Result] = []
    units = pack.data.get("services", [])
    if not units:
        return out
    if not ctx.dry_run:
        subprocess.run(_as_user(ctx, ["mkdir", "-p", str(ctx.unit_dir / "default.target.wants")]),
                       capture_output=True)
    for spec in units:
        if not _wanted(spec, ctx):
            continue
        try:
            source = _pack_file(pack, spec["unit"])
        except ValueError as e:
            out.append(Result(False, str(e)))
            continue
        name = source.name
        if not source.is_file():
            out.append(Result(False, f"{pack.id}: {spec['unit']} declared but not in the pack — {name} not installed"))
            continue
        target = ctx.unit_dir / name
        if ctx.dry_run:
            out.append(Result(True, f"{pack.id}: would install {name}"))
            continue
        try:
            target.write_text(_expand(source.read_text(encoding="utf-8"), _tokens(pack, ctx)),
                              encoding="utf-8")
            os.chmod(target, 0o644)
            _own(target, "user", ctx)
            if spec.get("enable", False):
                link = ctx.unit_dir / "default.target.wants" / name
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(Path("..") / name)
                _own(link, "user", ctx)
        except OSError as e:
            out.append(Result(False, f"{pack.id}: could not install {name} — {e}"))
            continue
        out.append(Result(True, f"{pack.id}: {name} (user unit, starts at login)"))
    return out


def enabled_units(pack, ctx: AppContext) -> list[str]:
    """Units the caller should daemon-reload and restart now.

    A manually symlinked unit is invisible to a running user manager until a
    reload, so without this the services sit dead until the next boot on a box
    the installer has just declared ready.
    """
    return [Path(s["unit"]).name for s in pack.data.get("services", [])
            if s.get("enable", False) and _wanted(s, ctx)]


def start_services(pack, ctx: AppContext) -> list[Result]:
    """Start installed units after seeds and controller generators have run."""
    units = enabled_units(pack, ctx)
    if not units:
        return []
    if ctx.dry_run:
        return [Result(True, f"{pack.id}: would restart {', '.join(units)}")]
    missing = [name for name in units if not (ctx.unit_dir / name).is_file()]
    if missing:
        return [Result(False, f"{pack.id}: units not installed: {', '.join(missing)}")]
    uid = pwd.getpwnam(ctx.user).pw_uid if ctx.user else os.getuid()
    runtime = Path(f"/run/user/{uid}")
    if not (runtime / "bus").exists():
        return [Result(True, f"{pack.id}: services enabled; no user session yet, starts at login")]
    env = {"XDG_RUNTIME_DIR": str(runtime),
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus"}
    try:
        for args in (["daemon-reload"], ["restart", *units]):
            r = _run_as_user(ctx, ["systemctl", "--user", *args], env, 30, ctx.user_home)
            if r.returncode:
                return [Result(False, f"{pack.id}: service start failed: {r.stderr.strip()}")]
    except (OSError, subprocess.SubprocessError) as e:
        return [Result(False, f"{pack.id}: service start failed: {e}")]
    return [Result(True, f"{pack.id}: started {', '.join(units)}")]


# ── udev ───────────────────────────────────────────────────────────────────

def apply_udev(pack, ctx: AppContext) -> list[Result]:
    """Lay down the rules a pack's `usb` block declares. One file per pack.

    One file per pack, named after it, rather than appending to a shared one:
    appending has no idempotent form — re-running the installer would grow the
    file every time — and a pack that is removed has to be able to take its
    rules with it, which it cannot do if they are interleaved with everyone
    else's.

    **Written, never activated.** No `udevadm control --reload-rules`, no
    `udevadm trigger`. That is `install/arch.sh`'s job, once, after every pack
    has had its say: reloading per pack would re-trigger the whole device tree
    a dozen times during an install, and the rules matter at the next plug
    event anyway. A device already plugged in when the rule lands is exactly
    what `launch.gamepadTrigger` covers.

    Root-only by nature. When the installer is not root the rule cannot be
    written, and that is reported rather than raised — an owner installing a
    pack as themselves gets a working emulator whose adapter needs a manual
    step, which is strictly better than a failed install.
    """
    out: list[Result] = []
    lines = usb_devices.udev_rules(pack)
    if not lines:
        return out
    target = ctx.udev_rules_dir / f"99-gamecore-{pack.id}.rules"
    body = ("# Generated by GameCore from catalog/{}/pack.json — do not edit.\n"
            "# Re-written on every install of this pack; hand edits are lost.\n"
            .format(pack.id) + "\n".join(lines) + "\n")
    if ctx.dry_run:
        return [Result(True, f"{pack.id}: would write {target}")]
    try:
        ctx.udev_rules_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        os.chmod(target, 0o644)
    except OSError as e:
        # Not fatal, and the message says what the owner loses: the emulator
        # installs and runs, the accessory needs a rule nobody laid down.
        out.append(Result(False, f"{pack.id}: could not write {target} — {e}. "
                          "Its USB accessories may need root to be readable."))
        return out
    out.append(Result(True, f"{pack.id}: {target.name} "
                      f"({len(pack.data.get('usb') or [])} device(s) declared)"))
    return out


# ── postInstall ────────────────────────────────────────────────────────────

def run_post_install(pack, ctx: AppContext) -> list[Result]:
    """Ordered, as the user, bounded, and never fatal — per the schema.

    The escape hatch for what does not reduce to data: EmberTV needs a TLS
    certificate generated and then trusted in a Firefox NSS database, which is
    two irreducible steps with an order between them.
    """
    out: list[Result] = []
    tokens = _tokens(pack, ctx)
    for step in pack.data.get("postInstall", []):
        if not _wanted(step, ctx):
            continue
        label = step.get("label", step["run"])
        try:
            script = _pack_file(pack, step["run"])
        except ValueError as e:
            out.append(Result(False, str(e)))
            continue
        if not script.is_file():
            out.append(Result(False, f"{pack.id}: {step['run']} declared but not in the pack — {label} skipped"))
            continue
        if ctx.dry_run:
            out.append(Result(True, f"{pack.id}: would run {label}"))
            continue
        env = {k.strip("@"): v for k, v in tokens.items()}
        try:
            r = _run_as_user(ctx, ["bash", str(script)], env,
                             step.get("timeoutSec", 60), pack.path)
        except subprocess.TimeoutExpired:
            out.append(Result(False, f"{pack.id}: {label} timed out"))
            continue
        except (OSError, subprocess.SubprocessError) as e:
            out.append(Result(False, f"{pack.id}: {label} — {e}"))
            continue
        if r.returncode == 0:
            out.append(Result(True, f"{pack.id}: {label}"))
        else:
            detail = (r.stderr or r.stdout or "").strip().splitlines()
            out.append(Result(False, f"{pack.id}: {label} failed"
                              + (f" — {detail[-1][:120]}" if detail else "")))
    return out


# ── the whole pack ─────────────────────────────────────────────────────────

def apply(pack, ctx: AppContext) -> list[Result]:
    """Everything the pack declares, in the only order that works."""
    results = apply_packages(pack, ctx)
    if pack.data.get("install"):
        results.append(install(pack, ctx))
    for step in (apply_host_access, apply_sources, apply_files, apply_udev, apply_services,
                 run_post_install):
        try:
            results.extend(step(pack, ctx))
        except Exception as e:                    # never take the install down
            log.exception("apply: %s failed in %s", pack.id, step.__name__)
            results.append(Result(False, f"{pack.id}: {step.__name__} — {e.__class__.__name__}: {e}"))
    return results
