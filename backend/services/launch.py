"""Launching a game or app: every gate and preparation step before the spawn.

The router looks the system up; `LaunchRefused` becomes the HTTP error in main.py;
everything else — conflicts, BIOS/USB gates, controller and per-game config,
the spawn itself — is decided here.
"""
import asyncio
import logging
import time
from pathlib import Path

from .. import ws
from ..config import resolve_path
from . import (
    bios,
    configgen,
    controller_profiles,
    controller_registry,
    fullscreen_enforcer,
    gamepad_monitor,
    pergame,
    profile_saves,
    standby,
    usb_devices,
    window_focus,
)
from .catalog import launch as catalog_launch
from .errors import ServiceError
from .catalog import load_catalog
from .process_manager import SessionConflict, process_manager

log = logging.getLogger(__name__)

# Budget for config housekeeping in front of a launch. A stalled disk must cost
# a slightly stale config, never a game that will not start.
RECONCILE_BUDGET = 0.2

# How long a launch waits for a connected but not-yet-profiled pad. A cold
# apply_profile measured 6.36 s on the reference box; emulators read input
# config once at startup, so a shorter wait loses the race (pad dead in game).
PROFILE_BUDGET = 8.0

# prepare_launch hooks (RPCS3 settings + patches) take ~100 ms warm, far more
# cold. The hook gets a deadline and writes nothing it cannot finish.
PACK_PREPARE_BUDGET = 3.0


class LaunchRefused(ServiceError):
    """The launch did not happen."""


def _is_app(system: dict) -> bool:
    return system.get("kind") == "app" or system.get("type") == "application"


async def _broadcast(event: str, game_key: str, system_id: str, detail: str) -> None:
    try:
        await ws.broadcast(event, {
            "game_key": game_key, "system_id": system_id, "detail": detail,
        })
    except Exception:
        log.exception("launch: failed to broadcast %s", event)


async def _await_controller_profiles(system_id: str, game_key: str) -> None:
    """Wait (up to PROFILE_BUDGET) until every connected pad has its config.

    Waits, never profiles: SDL probes carry an 8 s timeout each. Costs nothing
    once the roster is settled. Never raises; a timeout launches anyway and
    tells the player via `game:notice`.
    """
    try:
        started = asyncio.get_running_loop().time()
        pending = await gamepad_monitor.await_profiled(PROFILE_BUDGET)
        waited = asyncio.get_running_loop().time() - started
        if not pending:
            if waited > 0.05:
                log.info("launch: %s — waited %.1f s for the controller to be "
                         "profiled", system_id, waited)
            return
        detail = (f"{', '.join(sorted(set(pending)))} was not configured in "
                  f"time — if it does not work in game, quit and start again")
        log.warning("launch: %s — %s (waited %.1f s)", system_id, detail, waited)
        await _broadcast("game:notice", game_key, system_id, detail)
    except Exception:
        log.exception("launch: %s — could not check controller profiling, "
                      "launching anyway", system_id)


async def _free_stale_slots(system_id: str) -> None:
    """Release slots no pad holds, for the pack about to start only.

    Release only (pure file I/O, no SDL); profiling a present pad stays the
    monitor's job. Never raises: a failure leaves a playable config.
    """
    try:
        occupied = {c["player"] for c in controller_registry.snapshot()}

        def sweep() -> list[str]:
            done: list[str] = []
            for slot in range(1, controller_profiles.MAX_PLAYERS + 1):
                if slot not in occupied:
                    done += controller_profiles.release_profile(
                        slot, occupied, pack_ids=(system_id,))
            return done

        freed = await asyncio.wait_for(asyncio.to_thread(sweep),
                                       timeout=RECONCILE_BUDGET)
        if freed:
            log.info("launch: %s — freed stale slots: %s",
                     system_id, "; ".join(freed))
    except TimeoutError:
        # Not retried: the monitor comes back within three seconds.
        log.warning("launch: %s — slot cleanup exceeded %.0f ms, launching "
                    "with the config as it is", system_id, RECONCILE_BUDGET * 1000)
    except Exception:
        log.exception("launch: %s — slot cleanup failed, launching anyway",
                      system_id)


async def _place_per_game_config(system_id: str, rom_path: str) -> None:
    """Write this game's own settings before the emulator reads them.

    Bounded by RECONCILE_BUDGET: identifying the game may hit a cold disc or
    NFS. A missing per-game tweak is fine; a game that will not start is not.
    """
    try:
        placed = await asyncio.wait_for(
            asyncio.to_thread(pergame.materialise, system_id, rom_path,
                              configgen.HOME),
            timeout=RECONCILE_BUDGET)
        if placed:
            log.info("launch: %s", placed)
    except TimeoutError:
        log.warning("launch: %s — per-game config exceeded %.0f ms, launching "
                    "with the config as it is", system_id, RECONCILE_BUDGET * 1000)
    except Exception:
        log.exception("launch: %s — per-game config failed, launching anyway",
                      system_id)


async def _prepare_pack_launch(system_id: str, rom_path: str, exec_path: str,
                               exec_args: str, game_key: str) -> None:
    """Call the pack's `prepare_launch(...)` hook in generator.py, if any.

    Pack-specific logic stays in catalog/<id>/. Its `notice` goes out as
    `game:notice`.
    """
    try:
        pack = load_catalog().get(system_id.lower())
        module = configgen.load_generator(pack) if pack else None
        hook = getattr(module, "prepare_launch", None)
        if hook is None:
            return
        deadline = time.monotonic() + PACK_PREPARE_BUDGET
        result = await asyncio.wait_for(
            asyncio.to_thread(hook, rom_path=rom_path, home=configgen.HOME,
                              exec_path=exec_path, exec_args=exec_args,
                              deadline=deadline),
            timeout=PACK_PREPARE_BUDGET + 0.5)
        detail = (result or {}).get("notice")
        if detail:
            log.info("launch: %s", detail)
            await _broadcast("game:notice", game_key, system_id, detail)
    except TimeoutError:
        log.warning("launch: %s — pack preparation exceeded %.1f s, launching "
                    "as things are", system_id, PACK_PREPARE_BUDGET)
    except Exception:
        log.exception("launch: %s — pack preparation failed, launching anyway",
                      system_id)


async def _place_profile_saves(system_id: str, game_key: str) -> None:
    """Point the emulator at the active profile's saves. Unlike the steps
    above, a failure refuses the launch: the game would otherwise write into
    another profile's save."""
    try:
        dirs = await asyncio.wait_for(asyncio.to_thread(profile_saves.place, system_id),
                                      timeout=PACK_PREPARE_BUDGET)
        if dirs and dirs[0]:
            log.info("launch: %s — player 1 saves in %s", system_id, dirs[0])
    except Exception as e:
        log.exception("launch: %s — profile saves could not be placed", system_id)
        detail = "Could not set up this profile's saves, so the game did not start."
        await _broadcast("game:failed", game_key, system_id, detail)
        raise LaunchRefused(500, detail) from e


def _release_profile_saves(system_id: str) -> None:
    try:
        profile_saves.release(system_id, time.time())
    except Exception:
        log.exception("launch: %s — profile saves could not be given back", system_id)


def _connected_players() -> list[dict]:
    """Every profiled pad with its slot, for a pack's `launch_command`."""
    players = []
    for key, (vendor, product, name, _bus) in gamepad_monitor.roster().items():
        player = controller_registry.player_for(key)
        if player is not None:
            players.append({"player": player, "key": key, "vendor": vendor,
                            "product": product, "name": name})
    return sorted(players, key=lambda p: p["player"])


async def _pack_launch_command(system_id: str, rom_path: str, exec_path: str,
                               exec_args: str) -> tuple[str, str]:
    """The pack's `launch_command(...)` hook may replace the command.

    melonDS uses it for local multiplayer (one instance per pad). The hook
    answers None to keep the command as it is; any failure keeps it too.
    """
    try:
        pack = load_catalog().get(system_id.lower())
        module = configgen.load_generator(pack) if pack else None
        hook = getattr(module, "launch_command", None)
        if hook is None:
            return exec_path, exec_args
        on, _off = configgen.autoconfigured_packs(load_catalog())
        # Autoconfig off: the hook may still change the command, never the config.
        opts = (configgen.generator_opts(pack, configgen.HOME, configgen.SNAP_DIR)
                if pack.id in {p.id for p in on} else None)
        result = await asyncio.wait_for(
            asyncio.to_thread(hook, rom_path=rom_path, exec_path=exec_path,
                              exec_args=exec_args, players=_connected_players(),
                              opts=opts),
            timeout=PACK_PREPARE_BUDGET)
        if result:
            log.info("launch: %s — command replaced by the pack: %s %s",
                     system_id, *result)
            return result
    except TimeoutError:
        log.warning("launch: %s — launch_command exceeded %.1f s, launching "
                    "the usual command", system_id, PACK_PREPARE_BUDGET)
    except Exception:
        log.exception("launch: %s — launch_command failed, launching the usual "
                      "command", system_id)
    return exec_path, exec_args


def _check_rom_path(system: dict, rom_path: str) -> None:
    """The ROM must sit inside the system's ROMs directory (no arbitrary exec)."""
    roms_root = resolve_path(system.get("romsPath", ""))
    if not roms_root:
        raise LaunchRefused(400, "System has no ROMs path configured")
    try:
        Path(rom_path).resolve().relative_to(roms_root.resolve())
    except ValueError:
        raise LaunchRefused(403, "ROM path is outside the system's ROMs directory")


async def _resume_or_refuse(system: dict, system_id: str, game_key: str) -> dict | None:
    """One game at a time: the same suspended game resumes, another is refused.

    Runs before any launch side effect. Apps may coexist with a suspended game.
    """
    if _is_app(system):
        return None
    held_games = [s for s in process_manager.background_sessions if not s.is_app]
    same = next((s for s in held_games
                 if s.game_key == game_key and s.system_id == system_id), None)
    if same is not None:
        try:
            state = await process_manager.foreground(same.session_id)
        except SessionConflict as e:
            raise LaunchRefused(409, str(e)) from e
        return {"ok": True, "game_key": game_key, "resumed": True, **state}
    if held_games:
        title = held_games[0].game_key or "another game"
        raise LaunchRefused(
            409,
            f"Another game is still running ({title}). Close it before "
            "starting a different game",
        )
    return None


async def _gates(system: dict, system_id: str, exec_args: str, game_key: str) -> str:
    """Resolve launch args and run the refuse/notify gates. Returns final args."""
    # The tile defers its Flatpak app id to the catalogue, resolved now.
    try:
        exec_args = await asyncio.to_thread(catalog_launch.resolve_args, system["id"], exec_args)
    except LookupError as e:
        log.warning("launch refused — %s", e)
        await _broadcast("game:failed", game_key, system_id, str(e))
        raise LaunchRefused(424, str(e))

    # A missing required BIOS = black screen that looks like a broken dump.
    # Only ABSENT blocks, never a hash mismatch.
    if blocker := bios.launch_blocker(system_id):
        log.warning("launch refused — %s", blocker)
        await _broadcast("game:failed", game_key, system_id, blocker)
        raise LaunchRefused(424, blocker)

    # A missing USB accessory is announced, never blocking.
    if notice := usb_devices.launch_notice(system_id):
        log.info("launch: %s", notice)
        await _broadcast("game:notice", game_key, system_id, notice)
    return exec_args


async def _prepare(system_id: str, rom_path: str, exec_path: str,
                   exec_args: str, game_key: str, is_app: bool = False) -> None:
    """Everything the emulator must find on disk when it starts."""
    # A launch may come from the LAN while the box sleeps; a dark screen with
    # standby still armed swallows gp:guide, the only way out of a game.
    await standby.exit_standby()
    # Emulators read input config once at startup: the profile write must have
    # landed before the stale-slot sweep and before the spawn. An app reads no
    # emulator config, and the wait cost Steam 6.7 s of a press that did nothing.
    if not is_app:
        await _await_controller_profiles(system_id, game_key)
        await _free_stale_slots(system_id)
    await _place_profile_saves(system_id, game_key)
    if rom_path:
        await _place_per_game_config(system_id, rom_path)
        await _prepare_pack_launch(system_id, rom_path, exec_path, exec_args, game_key)


async def _spawn(system: dict, system_id: str, rom_path: str, exec_path: str,
                 exec_args: str, game_key: str) -> bool:
    try:
        return await process_manager.launch(
            exec_path=exec_path,
            exec_args=exec_args,
            rom_path=rom_path,
            game_key=game_key,
            system_id=system_id,
        )
    except SessionConflict as e:
        # The resident cap; the manager's message names what to close.
        log.info("launch refused — %s", e)
        raise LaunchRefused(409, str(e)) from e
    except OSError as e:
        # Missing binary, no rights, or one that asks for elevation (WinError 740).
        reason = ("not installed" if isinstance(e, FileNotFoundError)
                  else "not executable" if isinstance(e, PermissionError)
                  else e.strerror or str(e))
        detail = f"{system['id']}: cannot start {exec_path!r} — {reason}"
        log.warning("launch failed — %s", detail)
        await _broadcast("game:failed", game_key, system_id, detail)
        raise LaunchRefused(503, detail)


async def _hand_over(system: dict, system_id: str, started) -> None:
    """The new session's window to the front, then `game:presented`.

    The interface stays shown, black and locked, until that event, then the
    shell hides it: no empty desktop while the app starts. Sent at the
    timeout too (`window: false`): the interface never waits for good.
    """
    fs_cfg = system.get("fullscreen")
    if fs_cfg:
        shown = await fullscreen_enforcer.enforce(system_id, fs_cfg, started.job)
    else:
        shown = await window_focus.present(system_id, started.job)
    try:
        await ws.broadcast("game:presented", {
            "game_key": started.game_key, "system_id": started.system_id,
            "session": started.session_id, "window": bool(shown),
        })
    except Exception:
        log.exception("launch: failed to broadcast game:presented")


def start_window_task(system: dict, system_id: str) -> None:
    """Bring the new game's window to the front, fullscreen first if the tile
    asks: its window opens behind the interface otherwise (window_focus.present)."""
    started = process_manager.foreground_session
    if started is None:
        return
    task = asyncio.create_task(_hand_over(system, system_id, started))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


_launching = False
# Set while the box goes to sleep (services/rest_mode.py): the sentence a
# refused launch answers with, or "" when launches are allowed.
_gate_reason = ""


def is_launching() -> bool:
    """A launch is between its gate and its spawn."""
    return _launching


def close_gate(reason: str) -> None:
    """Refuse every new launch with `reason` until `open_gate()`."""
    global _gate_reason
    _gate_reason = reason


def open_gate() -> None:
    global _gate_reason
    _gate_reason = ""


async def launch(system: dict, system_id: str, rom_path: str, game_key: str) -> dict:
    """Launch `rom_path` (or the app) for `system`. Raises LaunchRefused."""
    global _launching
    if _gate_reason:
        raise LaunchRefused(409, _gate_reason)
    # Checked and claimed with no await between: a second launch slipping past
    # ran _prepare over the first, then released the running game's saves.
    if _launching or process_manager.is_foreground:
        raise LaunchRefused(409, "A game is already running")
    _launching = True
    try:
        return await _launch(system, system_id, rom_path, game_key)
    finally:
        _launching = False


async def _launch(system: dict, system_id: str, rom_path: str, game_key: str) -> dict:
    if rom_path:
        _check_rom_path(system, rom_path)

    exec_path = system.get("path", "")
    game_key = game_key or (Path(rom_path).name if rom_path else system["id"])

    resumed = await _resume_or_refuse(system, system_id, game_key)
    if resumed is not None:
        return resumed

    exec_args = await _gates(system, system_id, system.get("args", ""), game_key)
    try:
        await _prepare(system_id, rom_path, exec_path, exec_args, game_key, _is_app(system))
        exec_path, exec_args = await _pack_launch_command(system_id, rom_path,
                                                          exec_path, exec_args)
        resumed_flag = await _spawn(system, system_id, rom_path, exec_path, exec_args, game_key)
    except Exception:
        # No game, so no release at its end: placed saves would stay on the
        # profile's folder for the emulator started outside GameCore.
        await asyncio.to_thread(_release_profile_saves, system_id)
        raise
    if not resumed_flag:
        start_window_task(system, system_id)
    return {"ok": True, "game_key": game_key, "resumed": resumed_flag}
