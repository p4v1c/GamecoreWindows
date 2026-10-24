"""
Background service that reads every pad through SDL, in the backend.

This bypasses the Chromium Gamepad API, which never exposes the PS/guide button
and sees nothing while a game has the focus. When the PS/guide button is
pressed twice within DOUBLE_PRESS_WINDOW seconds (a single press is ignored,
to avoid accidental exits):
  - If a game is running  → suspend it and broadcast gp:guide, so the player
    lands back in the interface with the game frozen and intact
  - Otherwise             → broadcast gp:guide (frontend can choose to ignore)

Start+Select held together for CHORD_HOLD_S is the same escape, for the pads
whose guide button SDL does not map. It acts only while a game holds the
screen — see ChordWatcher.

The events come from `platform/gamepads.py` in the evdev vocabulary (its pad
layer), so the gestures below read `BTN_MODE`, `BTN_START`, `BTN_SELECT`.
"""
import asyncio
import logging
import time

from . import controller_profiles, controller_registry
from .gamepad_devices import (EV_KEY, GUIDE_CODES, KEY_DOWN, KEY_RELEASE, _event_sort_key,
                              _find_gamepad_devices, dup_indexes, pads_by_key)
from .input_activity import PAD_AXES, ActivityFilter
from .platform import gamepads as platform_gamepads

log = logging.getLogger(__name__)

# How many scan passes a pad gets to be profiled completely before the monitor
# stops trying. A pass that leaves an emulator unconfigured is retried on the
# next scan (3 s apart) rather than remembered as done.
#
# Bounded, because some give-ups are permanent — an emulator with no gamepad
# slot to clone from will never succeed — and each retry pays for SDL probes
# with an 8 s timeout apiece. Five passes is ~15 s, which covers the case this
# exists for: SDL not yet seeing a Bluetooth pad that has just connected. A
# DualShock 4's Ryujinx slot was lost exactly that way, and never recreated.
PROFILE_RETRIES = 5

# BTN_SELECT and BTN_START. Held together, they are the second way out of a
# game — see ChordWatcher for why a pad needs one.
BTN_SELECT = 0x13A
BTN_START = 0x13B
CHORD_CODES = frozenset({BTN_SELECT, BTN_START})

# How long Start+Select must be held before it counts.
#
# Long enough that an emulator's own use of the two buttons cannot reach it by
# accident, short enough that somebody trying to get out does not conclude it
# is not working and let go. Both are live buttons inside a game, which is the
# whole reason this is a hold and not a chord tapped once.
CHORD_HOLD_S = 1.0


class ChordWatcher:
    """Start+Select, held together, as a second way back to the interface.

    Some pads have no guide button SDL can map, and on those the double-press
    gesture does not exist, which leaves a player with no way out of a game
    whatsoever. This is that way out.

    Held rather than tapped, because Start and Select are both live buttons
    inside a game and a tapped chord is something an emulator's own bindings
    can produce by accident; a full second of both is not. And *because* it is
    held, it needs a timer: a pad speaks only when something changes, so
    nothing else would ever come back to ask whether the second had passed.

    Per device, so the chord means one pad's two buttons — not Start on one
    pad and Select on another in a two-player living room.
    """

    def __init__(self, fire):
        self._fire = fire                       # async callable, run on completion
        self._down: set[int] = set()
        self._task: asyncio.Task | None = None

    def feed(self, event) -> None:
        if event.type != EV_KEY or event.code not in CHORD_CODES:
            return
        if event.value == KEY_DOWN:
            self._down.add(event.code)
        elif event.value == KEY_RELEASE:
            self._down.discard(event.code)
        else:
            return                              # autorepeat: nothing changed hands

        if self._down == CHORD_CODES:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._hold())
        else:
            self.cancel()

    async def _hold(self) -> None:
        try:
            await asyncio.sleep(CHORD_HOLD_S)
        except asyncio.CancelledError:
            return
        # Re-read rather than trust the sleep: a release during it cancels the
        # task, but a cancellation that arrives late must not still fire.
        if self._down == CHORD_CODES:
            await self._fire()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None


# Guide button must be pressed twice within this window (seconds) to trigger.
DOUBLE_PRESS_WINDOW = 1.0
# Presses closer than this are the same physical press reported twice — ignore
# them.
DEBOUNCE = 0.05

# When the last *accepted* guide press landed, and whether it is still waiting
# for a partner. `None` rather than 0.0: `time.monotonic()` counts from an
# arbitrary point, so 0.0 is not "long ago" everywhere — on a box where that
# point is recent it reads as "pressed a moment ago", and the next press alone
# would act.
_last_guide_press: float | None = None
_guide_armed = False
# Whether a game held the screen at the first press of the pending pair. The
# browser sees the same presses and can suspend the game before the second one
# lands here; the pair still means what the player began it over.
_guide_from_game = False


async def _watch_device(path: str) -> None:
    """Read one pad's events until it disconnects or is cancelled."""
    queue = platform_gamepads.subscribe(path)
    log.info("gamepad_monitor: watching %s", path)
    activity = ActivityFilter(PAD_AXES)
    chord = ChordWatcher(lambda: _suspend_to_interface("start+select", foreground_only=True))
    try:
        while True:
            event = await queue.get()
            if event.layer == platform_gamepads.GONE.layer:
                break
            if event.layer != "pad":
                continue
            # Anything that means somebody is there (a button, the d-pad, a
            # stick, a trigger) counts as activity and wakes the box.
            if activity.is_activity(event):
                from . import standby
                standby.on_input()
            # Before the KEY_DOWN filter below: a chord that is *held* has to
            # see the release that ends it, and a release is not a key down.
            chord.feed(event)
            if event.type != EV_KEY or event.value != KEY_DOWN:
                continue
            if event.code in GUIDE_CODES:
                log.info("gamepad_monitor: guide/PS button on %s", path)
                await _on_guide_pressed()
    finally:
        chord.cancel()
        platform_gamepads.unsubscribe(path, queue)


async def _on_guide_pressed() -> None:
    """One guide/PS press, from a pad that may well report it twice.

    The pairing is deliberately written around a single "when did the last
    press we believed land" clock, and every branch updates it. Clearing that
    clock when the gesture fired left the duplicate report of the *acting*
    press looking like a brand-new first press, which armed the next single
    press to suspend or resume again on its own.
    """
    global _last_guide_press, _guide_armed, _guide_from_game

    now = time.monotonic()
    elapsed = None if _last_guide_press is None else now - _last_guide_press

    # One physical press, reported twice by one pad. It is not a press of its
    # own: the pairing is left exactly as it was, armed or not.
    if elapsed is not None and elapsed < DEBOUNCE:
        return

    _last_guide_press = now

    if not (_guide_armed and elapsed is not None and elapsed <= DOUBLE_PRESS_WINDOW):
        from . import process_manager as pm_module
        _guide_armed = True
        _guide_from_game = pm_module.process_manager.is_foreground
        log.info("gamepad_monitor: guide pressed once — press again within %.1fs to exit",
                 DOUBLE_PRESS_WINDOW)
        return

    _guide_armed = False
    await _suspend_to_interface("guide", from_game=_guide_from_game)


async def _suspend_to_interface(gesture: str, foreground_only: bool = False,
                                from_game: bool = False) -> None:
    """Give the screen back: freeze whatever is running, and say so.

    Shared by the two gestures that mean the same thing, so there is one
    account of what "get me out" does and not a second one that drifts.
    `foreground_only` is for the gesture that is *only* an escape from a game:
    Start+Select are ordinary interface buttons when no game holds the screen,
    and holding them there should not quietly send anybody home. `from_game`
    says a game held the screen when the gesture began.
    """
    from . import process_manager as pm_module
    from .. import ws

    pm = pm_module.process_manager

    if foreground_only and not pm.is_foreground:
        return

    # Double-Home used to KILL the running game, and that was the only thing it
    # could do: there was no other way to get the screen back. It is a
    # destructive default reached by a gesture two accidental presses away, and
    # the number of times it has cost somebody an unsaved save is not knowable
    # because nothing recorded it.
    #
    # It suspends now. The player lands back in the interface with their game
    # frozen and intact, and closing it is a deliberate second action on the
    # session bar — where the theme also gets to say "Close application" for an
    # app. Nothing is lost by pressing this twice by mistake any more.
    #
    # The gesture stays the core's, which is why the choice is offered here
    # rather than left to a theme: `gp:guide` is in RESERVED_EVENTS precisely
    # so that no theme can take the one binding that gets a player out of a
    # game. What a theme draws is the session bar that follows.
    action = "home"
    if pm.is_foreground:
        try:
            await pm.background()
            action = "backgrounded"
            log.info("gamepad_monitor: suspended the running session (%s)", gesture)
        except Exception:
            # **Nothing is closed here, and that is the correction.**
            #
            # This used to fall back to `pm.kill()`, reasoning that a player must
            # never be trapped in a game because the polite way out failed. The
            # reasoning was right and the call was wrong: `kill()` with no
            # argument means "the one on the screen, else the suspended one",
            # and the way a suspend fails is that the foreground has *just
            # exited*. So by the time the fallback ran there was frequently no
            # foreground left, and it reached past it to destroy a suspended
            # game the player had never pointed at.
            #
            # A failed suspend is not consent to end anything. And the premise
            # was false anyway: if the signal did not land, the process is gone,
            # so there is no game left to be trapped in.
            action = "failed"
            log.exception("gamepad_monitor: could not suspend — session kept")
    elif from_game:
        # Already suspended by the browser's view of the same presses. `home`
        # would open the session menu on a request to leave the game.
        action = "backgrounded"

    try:
        # Still `gp:guide`, whichever gesture asked: it is the event themes and
        # the frontend already treat as "you are back in the interface", and it
        # is in RESERVED_EVENTS so no theme can take it. `gesture` is additive,
        # for anyone reading a log and asking which button did this.
        await ws.broadcast("gp:guide", {"action": action, "gesture": gesture})
    except Exception:
        log.exception("gamepad_monitor: error broadcasting gp:guide")


def _game_running() -> bool:
    """True while a session is RESIDENT — suspended ones included. Imported
    late: process_manager pulls in the database and the websocket layer, and
    this module is imported at startup.

    `is_running` and not `is_foreground`, and the distinction was got wrong once
    here. The reasoning for the screen was that a frozen emulator re-reads no
    input config, so renumbering the players under it could not surprise it.
    True, and one step short: nothing goes wrong *while* it is frozen, it goes
    wrong on RESUME. The emulator comes back holding the binding it read at
    startup, the registry has since closed the gap left by a pad that was
    unplugged, and the surviving player now writes to a slot the game is not
    listening on — which is precisely "the remaining player silently becomes
    somebody else", arriving a few minutes late.

    A suspended game is a session in progress. The roster waits for it.
    """
    try:
        from . import process_manager
        return process_manager.process_manager.is_running
    except Exception:      # never let a bookkeeping question break reconciliation
        return False


# ── "has this pad been profiled yet?" ────────────────────────────────────────
# The scan loop's own bookkeeping, published so the LAUNCH path can ask it.
#
# It was local to run(), and that is the whole of the race measured on the
# reference box: an emulator reads its input config once, at startup, and
# nothing outside this module could tell a pad whose configs had been written
# from one the loop had simply not reached yet. Both look like a connected pad.
#
#     RPCS3 started 08:52:53 · its config landed 08:52:59 — six seconds late,
#     "Failed to bind device '' to handler SDL" ×4 in its log, dead pad in game.
#     Dolphin, same protocol, two seconds. Both worked at the NEXT launch.
#
# Six seconds is not a slow disk: a cold apply_profile for one pad measures
# 6.36 s on this box — nine generators, and the SDL probes carry an eight-second
# timeout apiece. That is why the launch path cannot simply profile: those
# probes cannot sit in front of a launch. It waits for THIS instead.
_roster: dict[str, tuple[str, str, str, int]] = {}
_applied: dict[str, tuple[tuple | None, int]] = {}
# A first scan has completed. Distinct from an empty roster: "no pad" and "we
# have not looked yet" are the same dict and only one of them is an answer.
_scanned = False
# The scan loop is alive. Without it nothing will ever profile anything, so
# there is nothing to wait for — a launch on a box whose monitor never started
# (SDL missing, a test that does not run it) must not pay the budget for a pass
# that is never coming.
_running = False
# Something outside the scan loop changed what a pass would produce, so the
# next one must run even though no pad moved. Turning autoconfig back on is the
# only setter today, and it is exactly the case the loop's own two triggers
# cannot see: the roster is identical (`was == live`) and every footprint is
# settled (`retries == 0`), so without this the pads on the box would keep the
# configs the owner edited by hand until one of them was physically unplugged.
_dirty = False


def roster() -> dict[str, tuple[str, str, str, int]]:
    """The pads the last scan saw: key → (vendor, product, name, bustype)."""
    return dict(_roster)


def request_reprofile() -> None:
    """Re-write every connected pad on the next scan, at most 3 s away.

    Not done inline by the caller: `_reconcile` is what knows the roster, the
    dup indexes and the slot compaction, and a second place computing them
    would be a second answer to "who is player 2". The switch says "start
    again"; the loop decides what that means.
    """
    global _dirty
    _applied.clear()
    _dirty = True


def _take_dirty() -> bool:
    global _dirty
    was, _dirty = _dirty, False
    return was


def unprofiled() -> list[str]:
    """Connected pads whose emulator configs have not been written yet.

    Names, so a caller can say which pad; empty when every pad the scan loop
    can see is done. A pad that has run OUT of retries counts as done — the
    monitor has given up on it, and a launch that waited for it would wait for
    something that is never going to happen.
    """
    if not _running:
        return []
    if not _scanned:
        return ["the first controller scan"]
    pending: list[str] = []
    for key, (_vendor, _product, name, _bus) in _roster.items():
        footprint, retries = _applied.get(key, (None, 1))
        if footprint is None or retries > 0:
            pending.append(name)
    return pending


async def await_profiled(budget: float) -> list[str]:
    """Wait for `unprofiled()` to empty. Returns whatever is still pending.

    Polled rather than event-driven, deliberately. An `asyncio.Event` created
    at import time belongs to whichever loop first touches it, and this is
    awaited from the request loop while it is set from the monitor's — the
    TestClient alone builds a fresh loop per client. The predicate is three
    dict lookups and no I/O, so a 100 ms poll costs nothing next to the six
    seconds it is there to cover.

    Deliberately NOT a fresh scan: the roster the loop publishes is at most
    one scan period old, and that is the answer the launch path needs.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    while True:
        pending = unprofiled()
        if not pending or loop.time() >= deadline:
            return pending
        await asyncio.sleep(0.1)

async def _reconcile(was: dict[str, tuple[str, str, str, int]],
                     live: dict[str, tuple[str, str, str, int]],
                     applied: dict[str, tuple[tuple[str, str, str, int] | None, int]],
                     first_scan: bool, ws) -> None:
    """Bring every player slot in line with the pads that are actually here.

    Only the arriving pad used to be profiled, and only the leaving pad's slot
    was released. But the strings these emulators store are relative to the
    whole roster — Dolphin's `SDL/<dup>/<name>`, RPCS3's `<name> <dup+1>`,
    Ryujinx's `<dup>-<GUID>` — so a departure silently invalidates the
    survivors' configs. Player 1's pad running out of battery mid-session left
    player 2 holding a `dup` that no longer described anything, and the next
    launch of Dolphin found port 1 pinned to a controller that had left.

    Reconciling the whole roster whenever it changes fixes that class of bug
    rather than its instances, and it is the only way a slot ever gets a second
    chance: the old code profiled a pad exactly once, when `known` was False.
    """
    # Departures first: their slots must be free before dup indexes are recomputed.
    #
    # No release_profile() here any more. It used to hang off this loop, which
    # meant a slot was only ever freed for a departure this process WITNESSED —
    # and worse, it named the slot the pad held before compact() moved anyone.
    # The sweep below decides from the roster instead, which is the thing that
    # actually says whether a slot is occupied.
    for key in [k for k in was if k not in live]:
        applied.pop(key, None)
        label = controller_registry.label_for(key)
        player = controller_registry.disconnect(key)
        if player is None:
            continue
        log.info("gamepad_monitor: controller %d disconnected (%s)", player, label)
        try:
            await ws.broadcast("gp:disconnected", {"player": player, "label": label})
        except Exception:
            log.exception("gamepad_monitor: error broadcasting gp:disconnected")

    # Close up the gap a departure leaves, but only between games. Slots are
    # never taken back from a connected pad, so unplugging player 1 during
    # co-op left the survivor on slot 2 for good — and a Switch game asking for
    # Player 1 then found nobody. Doing it mid-session would be worse: the
    # remaining player would silently become someone else.
    #
    # The pads that move are re-profiled on their own, because the player
    # number is part of the footprint.
    if not _game_running():
        for key, (old_slot, new_slot) in controller_registry.compact().items():
            log.info("gamepad_monitor: player %d → %d (%s)", old_slot, new_slot,
                     controller_registry.label_for(key))

    arrivals = [k for k in live if not controller_registry.has(k)]
    roster: dict[str, tuple[int, str]] = {}
    for key, (vendor, product, evdev_name, _bus) in live.items():
        player = controller_registry.connect(key, evdev_name)
        roster[key] = (player, controller_profiles.resolve_name(vendor, product, evdev_name))

    # Free every slot no pad holds, on EVERY inventory change — not only on a
    # departure. This is the reboot hole, and it is the one the reference box
    # was sitting in: someone plays at four, powers the box off, unplugs three
    # pads, powers it back on. `was` is empty at startup, so those three
    # departures produce no event at all and their slots were never released.
    # With one DualShock 4 connected the box showed RPCS3 players 2-4 naming
    # Xbox pads, Ryujinx holding indexes 2 and 3, and Dolphin's GCPad4 on
    # SDL/3 — four players, three of them ghosts.
    #
    # Before the profiling loop, so a slot is freed before anything claims it,
    # and passing the whole remaining roster because some of what was written
    # is not per-slot: the PS1/PS2 multitap is on while ANY player at or above
    # slot 3 is here, which no single index can answer.
    occupied = {player for player, _name in roster.values()}
    for slot in range(1, controller_profiles.MAX_PLAYERS + 1):
        if slot in occupied:
            continue
        try:
            released = await asyncio.to_thread(
                controller_profiles.release_profile, slot, occupied)
            if released:
                log.info("gamepad_monitor: slot %d freed — %s",
                         slot, "; ".join(released))
        except Exception:
            log.exception("gamepad_monitor: release_profile failed for slot %d", slot)

    # Which systems this pass gave up on, per pad, for the arrival toast below.
    # The profiling loop is the only place that knows, and it runs before the
    # loop that speaks to the player — so the answer has to be carried, not
    # recomputed. Re-profiling to find out would double every SDL probe.
    unconfigured: dict[str, list[str]] = {}
    # And which ones were left alone on purpose. Same journey, opposite
    # meaning: `unconfigured` is a failure the box would like to fix, this is
    # the owner's own instruction being carried out. The toast has to be able
    # to tell them apart — "the Switch will not respond to this pad" and "you
    # turned autoconfig off" call for different sentences and only one of them
    # is a fault.
    autoconfig_off: dict[str, list[str]] = {}

    for key, dup in dup_indexes(roster).items():
        vendor, product, evdev_name, bustype = live[key]
        player, name = roster[key]
        # Everything that decides what gets written. The player number belongs
        # here — a pad moving slot rewrites different sections — and so does
        # the transport, which an SDL GUID encodes.
        footprint = (player, vendor, product, name, dup, bustype)
        prev, retries = applied.get(key, (None, 0))
        # Settled: this exact footprint was configured cleanly, or it failed
        # often enough that retrying is just burning SDL probes every 3 s.
        if prev == footprint and retries <= 0:
            continue
        if prev != footprint:
            retries = PROFILE_RETRIES   # new pad, or it moved slot — full budget
        # Claimed before the attempt so a raise cannot loop forever, but with
        # one retry spent rather than the whole thing marked done.
        applied[key] = (footprint, retries - 1)
        try:
            results = await asyncio.to_thread(
                controller_profiles.apply_profile, player, vendor, product, evdev_name, dup)
            # Log both outcomes. `if results:` alone meant a pass that
            # configured nothing looked exactly like one that never ran —
            # apply_profile details the give-ups itself, but the "0 emulators"
            # headline belongs here.
            log.info("gamepad_monitor: player %d profiled (%s, %s:%s, dup %d) — %s",
                     player, name, vendor, product, dup,
                     "; ".join(results) if results else "no emulator configured")
            unconfigured[key] = list(getattr(results, "skipped_labels", ()))
            # Only when the pad got NOTHING. A per-emulator exception is a
            # choice the owner made on one system and the pad still works on
            # the other eight — toasting it on every connect would be nagging
            # about something already visible on the settings screen. The trap
            # this feature has to avoid is the OTHER shape: a pad plugged in
            # weeks later that does nothing anywhere, with no thread back to
            # the switch. That is this line, and only this line.
            off = list(getattr(results, "off_labels", ()))
            autoconfig_off[key] = off if off and not results else []
            if getattr(results, "complete", True):
                applied[key] = (footprint, 0)          # done, stop asking
            elif retries - 1 <= 0:
                # Out of budget. Say so once, here, rather than leaving the
                # owner to notice months later that one emulator never got a
                # slot — apply_profile has already logged which ones and why.
                log.warning("gamepad_monitor: player %d (%s) still incomplete after "
                            "%d attempts — giving up until it reconnects",
                            player, name, PROFILE_RETRIES)
        except Exception:
            log.exception("gamepad_monitor: controller_profiles failed for player %d", player)

    if not first_scan:
        for key in arrivals:
            player, _name = roster[key]
            vendor, product, label, _bus = live[key]
            log.info("gamepad_monitor: controller %d connected (%s)", player, label)
            # P1 made the give-up visible in the journal. That is not where the
            # player is standing: they have just plugged a pad in and it does
            # not work. The toast is, and until now it said "Controller 2
            # connected" in green for a controller that is dead in every
            # emulator matching a device by name.
            #
            # Asked through P1's own `identification`, not by matching the skip
            # strings: one source of truth for "can this pad be named", and it
            # is the exact condition the wizard answers.
            unmapped = False
            try:
                unmapped = not (await asyncio.to_thread(
                    controller_profiles.identification, vendor, product, label)
                )["identified"]
            except Exception:
                # A pad arriving is news whatever we can work out about it.
                # Letting this question take the toast down with it would
                # silence precisely the pad we understand least.
                log.exception("gamepad_monitor: could not tell whether %s:%s "
                              "is identified", vendor, product)
            # A pad can be perfectly identified and STILL be left out of one
            # emulator — the reference box's Xbox pad was, on the Switch alone,
            # while every other system bound it. `unmapped` cannot say so: it
            # answers "can this pad be named", which was true. So the toast was
            # the green "Controller 1 connected" and the only trace of the
            # give-up was a journal line nobody reads from a sofa.
            #
            # A system missing from one console out of thirteen is exactly the
            # fault that is undiagnosable from the couch, and it is silent by
            # construction: the game simply does not answer the pad.
            try:
                await ws.broadcast("gp:connected",
                                   {"player": player, "label": label,
                                    "vendor": vendor, "product": product,
                                    "unmapped": unmapped,
                                    "unconfigured": unconfigured.get(key, []),
                                    "autoconfigOff": autoconfig_off.get(key, [])})
            except Exception:
                log.exception("gamepad_monitor: error broadcasting gp:connected")


async def run() -> None:
    """Main loop: scan for gamepad devices every few seconds and watch them."""
    global _running, _scanned
    if not await asyncio.to_thread(platform_gamepads.start):
        # Left False on purpose, so `unprofiled()` answers "nothing to wait
        # for" rather than "not scanned yet": no pad will ever be profiled.
        log.error("gamepad_monitor: no pad can be read (%s): no guide shortcut, "
                  "no player slots", platform_gamepads.unavailable())
        return

    log.info("gamepad_monitor: started (SDL)")

    from .. import ws

    watched: dict[str, asyncio.Task] = {}
    # registry key → the (vendor, product, resolved name, dup) an emulator
    # config was last written for, and how many attempts that footprint has
    # left. A slot is re-profiled when its footprint changes, which is what
    # makes the roster self-correcting — and now also when the last pass left
    # an emulator unconfigured, until the budget runs out.
    #
    # Module-level rather than local so the launch path can read it — see
    # `unprofiled()`. Still passed explicitly to _reconcile, which keeps that
    # function callable with a dict of its own from the tests.
    applied = _applied
    live: dict[str, tuple[str, str, str]] = {}
    # Pads already plugged in when the backend starts get their slots
    # silently — a console doesn't toast for pads that were always there.
    first_scan = True
    _running = True

    try:
        while True:
            devices = _find_gamepad_devices()
            current = set(devices)

            # Watch every joystick: the guide shortcut and standby's activity.
            # Player slots are decided below, from the device list rather than
            # from which watchers happen to be alive.
            for path in sorted(current - set(watched), key=_event_sort_key):
                watched[path] = asyncio.create_task(_watch_device(path), name=f"gpad:{path}")
            for path in [p for p, t in watched.items() if t.done()]:
                del watched[path]      # gone, or errored: re-watched next pass if still there

            was, live = live, pads_by_key(devices)
            # Published BEFORE the reconcile, not after: for the whole 6 s a
            # cold pass takes, a pad that is plugged in and not yet written
            # must read as pending. Publishing afterwards would leave the exact
            # window this exists to close.
            _roster.clear()
            _roster.update(live)
            # `was != live` alone would never give a failed pass its retry: nothing
            # about the pad changes when SDL merely has not caught up with it yet.
            pending = any(retries > 0 for _, retries in applied.values())
            # Read before the test, not inside it: `or` short-circuits, so a
            # request that happened to arrive on a pass where the roster also
            # changed would never be consumed and would re-profile every three
            # seconds for the life of the process.
            dirty = _take_dirty()
            if was != live or pending or dirty:
                await _reconcile(was, live, applied, first_scan, ws)
            first_scan = False
            _scanned = True
            await asyncio.sleep(3)
    finally:
        # A cancelled monitor is a box where nothing will be profiled again.
        # Saying so is what keeps a launch from waiting on it.
        _running = False


