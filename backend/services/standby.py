"""Soft standby: low power without losing SSH or the backend.

Not sleep: the box stays up, we just cut what actually draws power. Two
stages, driven by controller inactivity (pad events fed by gamepad_monitor):

  active ──idle──▶ screensaver (UI slideshow, ws "standby:screensaver")
         ──idle──▶ sleep       (screen off + Power saver plan,
                                ws "standby:sleep")

Any controller button exits both stages (screen on, plan restored,
ws "standby:exit"). A running game blocks the whole machine.

Windows has idle timers of its own and cannot see a gamepad, so two more
mechanisms keep them out of the way:

  · `_signal_user_activity()` restarts Windows' display and sleep idle timers
    on pad input and during a game (`SetThreadExecutionState`, nothing held).
  · `desktop_power.claim()` takes the active plan's screen-off and sleep
    timeouts themselves while standby is on, so the number the owner set from
    the sofa is the only one that decides. Re-asserted from `_tick`: see
    `_ensure_timers_owned()`.

Every OS call goes through `_power()` (`platform/power.py`); the test suite
replaces that one function. Config lives in config/standby.json (kept across
OTA updates).
"""
import asyncio
import json
import logging
import os
import time

from . import desktop_power
from .paths import config_dir
from .platform import power as platform_power

log = logging.getLogger(__name__)

CONFIG_FILE = config_dir() / "standby.json"

DEFAULTS = {"enabled": True, "screensaver_mins": 10, "sleep_mins": 20,
            # After a wake from sleep: how long the screen stays black once the
            # link is up, while the TV finds the signal (services/display_ready.py).
            "wake_settle_secs": 2.5}
WAKE_SETTLE_MAX_SECS = 8.0

_POLL_SECS = 15

# How often, at most, Windows is told somebody is at the controller.
# `on_input()` fires per retained event and a stick swung hard is a burst;
# two seconds is far below any idle timeout, so throttling costs nothing.
_ACTIVITY_SIGNAL_GAP = 2.0
_last_activity_signal = 0.0

# How long a silence has to be before the NEXT press might be arriving at a
# screen somebody else has switched off — and therefore has to pay for a real
# screen-on, not just an activity signal.
#
# Restarting an idle timer does not light a screen already off, so a press
# after a long silence pays for a real screen-on. Not on every press: during
# play input never stops long enough for the screen to have gone out.
_WAKE_AFTER_SILENCE = 60.0
# Only on_input() writes this. _last_input is not usable for the job: _tick()
# pushes it forward every fifteen seconds while a game runs, so after a long
# cutscene it would report a silence of zero and no press would ever wake.
_last_press = 0.0

# The claim on Windows' idle timers is re-asserted from the watcher, not made
# once at startup: the owner can change the plan's timeouts from Windows'
# settings in desktop mode, and the claim must come back afterwards.
_CLAIM_RETRY_SECS = 60.0
_last_claim_attempt = 0.0

# Set by POST /api/standby/dark once the UI has faded to black. Screen-off waits
# for it so the screen goes off on a black frame, not mid-picture; capped for a box
# whose UI never answers (crashed kiosk, a frontend older than the endpoint).
# Made per sleep, so an earlier fade's answer is never already set.
_ui_dark: asyncio.Event | None = None
_UI_DARK_WAIT_SECS = 3.0

# state: "active" | "screensaver" | "sleep"
_state = "active"
_last_input = time.monotonic()


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        return {**DEFAULTS, **cfg}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save_config(cfg: dict) -> dict:
    merged = {**load_config(), **{k: v for k, v in cfg.items() if k in DEFAULTS}}
    merged["screensaver_mins"] = max(1, int(merged["screensaver_mins"]))
    # 0 means NEVER turn the screen off, and it has to survive this function.
    #
    # The settings screen offers it — SLEEP_MINS ends in 0, labelled "Never" —
    # and the clamp below used to swallow it whole: max(screensaver_mins, 0) is
    # screensaver_mins, so choosing "never" set screen-off to the SAME minute as
    # the screensaver. The most cautious option on the page produced the most
    # aggressive setting there is, and it took the slideshow down with it —
    # _tick tests sleep_mins first, so the screensaver stage became unreachable.
    # Picking "Never" on a box at 4 minutes blacked the television at 4 minutes.
    merged["sleep_mins"] = max(0, int(merged["sleep_mins"]))
    merged["wake_settle_secs"] = min(WAKE_SETTLE_MAX_SECS,
                                     max(0.0, float(merged["wake_settle_secs"])))
    if merged["sleep_mins"]:
        # Otherwise screen-off always comes after (or with) the screensaver
        merged["sleep_mins"] = max(merged["screensaver_mins"], merged["sleep_mins"])
    # tmp + os.replace, like auth._write_private: write_text truncates first,
    # so an interrupted write left a half-written standby.json and load_config
    # fell back to the defaults, quietly undoing the player's timings.
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    os.replace(tmp, CONFIG_FILE)
    return merged


def get_state() -> str:
    return _state


async def _power(action: str, *args):
    """Run one `platform/power.py` call in a thread. None when it failed.

    THE choke point through which standby reaches Windows: the test suite
    replaces this one function (tests/conftest.py) to keep itself off the real
    screen. Failures are logged and never raised: standby is best effort.
    """
    try:
        result = await asyncio.to_thread(getattr(platform_power, action), *args)
    except OSError as e:
        log.info("standby: %s failed: %s", action, e)
        return None
    return True if result is None else result


async def _screen(on: bool) -> None:
    """Turn the television on or off."""
    if await _power("screen_on" if on else "screen_off") is None:
        log.warning("standby: could not turn the screen %s", "on" if on else "off")


async def _signal_user_activity() -> None:
    """Tell Windows somebody is at the controller: its idle timers restart.

    Windows does not count a gamepad as input, so without this its own screen
    timer would blank the TV under a player browsing with the pad alone.
    """
    await _power("signal_activity")


# The plan that was active before the sleep stage switched to Power saver.
_plan_before_sleep: str | None = None


async def _governor(gov: str) -> None:
    """Power saver while the screen is off, the previous plan back afterwards.

    Best effort, like cpupower was: a PC without a Power saver plan (Modern
    Standby machines often have Balanced only) keeps its plan; the screen is
    the real power sink.
    """
    global _plan_before_sleep
    if gov == "powersave":
        current = await _power("active_scheme")
        if not isinstance(current, str) or current == platform_power.POWER_SAVER:
            return
        if await _power("set_active_scheme", platform_power.POWER_SAVER) is not None:
            _plan_before_sleep = current
        return
    if _plan_before_sleep is not None:
        await _power("set_active_scheme", _plan_before_sleep)
        _plan_before_sleep = None


async def _enter(stage: str) -> None:
    global _state, _ui_dark
    from .. import ws
    if _state == stage:
        return
    log.info("standby: %s → %s", _state, stage)
    _state = stage
    if stage == "screensaver":
        await ws.broadcast("standby:screensaver", {})
    elif stage == "sleep":
        _ui_dark = dark = asyncio.Event()
        await ws.broadcast("standby:sleep", {})
        if ws.has_clients():
            try:
                await asyncio.wait_for(dark.wait(), _UI_DARK_WAIT_SECS)
            except asyncio.TimeoutError:
                log.info("standby: no UI faded to black, screen off anyway")
        if _state != "sleep":
            return      # a press woke the box during the fade
        await _screen(False)
        await _governor("powersave")


def ui_dark() -> None:
    """The UI has finished fading to black: the screen may go off now."""
    if _ui_dark is not None:
        _ui_dark.set()


async def exit_standby() -> None:
    """Wake the box. Deliberately unconditional — see resume_after_restart().

    It used to return immediately when _state was already "active", which is
    exactly the case where waking matters: the screen can be off while this
    process believes it is on, and POST /api/standby/exit then did nothing at
    all. Asking to wake up is never a no-op.
    """
    global _state, _last_input
    from .. import ws
    _last_input = time.monotonic()
    was = _state
    _state = "active"
    if was != "active":
        log.info("standby: wake from %s", was)
    await _screen(True)
    await _governor("performance")
    await ws.broadcast("standby:exit", {})


async def resume_after_restart() -> None:
    """Undo, at startup, any standby the previous process left on the screen.

    _state is in memory; its effect is not: a screen turned off by the previous
    process stays off across a crash, a restart or an OTA, while the new one
    starts at _state == "active" and a press would not wake it.

    Called from the lifespan before the watcher starts, so it also repairs a box
    that is already in that state — restarting the backend is what a stuck user
    will try, and now it works.
    """
    global _state, _last_input
    _state = "active"
    _last_input = time.monotonic()
    await _screen(True)
    await _governor("performance")


def _spawn(coro) -> None:
    """Fire-and-forget from the pad read loop, without an unretrieved warning."""
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def on_input() -> None:
    """Any controller input: called from gamepad_monitor's read loop."""
    global _last_input, _last_activity_signal, _last_press
    now = time.monotonic()
    _last_input = now
    silence, _last_press = now - _last_press, now

    # Unconditional: Windows' own timer may have blanked the TV while _state
    # sat at "active". Throttled, which costs a press nothing.
    if now - _last_activity_signal >= _ACTIVITY_SIGNAL_GAP:
        _last_activity_signal = now
        _spawn(_signal_user_activity())

    if _state != "active":
        _spawn(exit_standby())          # already turns the screen back on
    elif silence >= _WAKE_AFTER_SILENCE:
        # GameCore thinks it is awake and the TV may still be dark (Windows'
        # timer); restarting a timer does not light a screen that is off.
        _spawn(_screen(True))


async def _ensure_timers_owned(enabled: bool) -> None:
    """Re-assert GameCore's claim on the box's idle timers, at most once a minute.

    A function so the watcher does not have to know how the claim works, and so
    a test can watch how often it is attempted. `claim()` is idempotent and
    reads before it writes, so an already-claimed box costs one read per minute
    and changes nothing.
    """
    global _last_claim_attempt
    now = time.monotonic()
    if now - _last_claim_attempt < _CLAIM_RETRY_SECS:
        return
    _last_claim_attempt = now
    try:
        # Standby off hands the timers to Windows (main and the router release
        # them); nothing to re-assert then.
        if enabled:
            await desktop_power.claim()
    except Exception:
        # Never at the expense of the tick that follows it: the screensaver and
        # sleep stages are what the player actually notices.
        log.exception("standby: could not claim the box's idle timers")


async def _tick(cfg: dict) -> None:
    """One pass of the watcher. A function so a test can ask for one.

    Otherwise the only way to exercise any of this is to wait _POLL_SECS of real
    time for the loop to come round, which is why none of it was covered.
    """
    global _last_input, _last_activity_signal
    from .process_manager import process_manager

    await _ensure_timers_owned(cfg["enabled"])
    if not cfg["enabled"]:
        # The clock stops with the switch, and this line is the fix.
        #
        # It used to `continue` and leave `_last_input` where it was, so the
        # counter went on accumulating against a threshold nobody was
        # measuring. Turn standby off, use the box all afternoon, turn it back
        # on — and it slept within one poll, straight past the screensaver,
        # because it believed nobody had touched it since lunchtime.
        _last_input = time.monotonic()
        return
    if process_manager.is_foreground:
        # A game counts as activity — idle starts when it exits.
        #
        # `is_foreground`, so a SUSPENDED game does not hold the box awake. It
        # is frozen: nobody is playing it, and holding the clock for it would
        # mean a console that never sleeps again because someone backgrounded
        # a game last Tuesday. What happens to that game when the box does
        # sleep is nothing at all: a suspended job is already the state a
        # sleeping process should be in, and it is still suspended after.
        _last_input = time.monotonic()
        # And Windows has to be told: the line above only holds OUR standby
        # off, and Windows' own timer cannot see the pad or the game. Said on
        # every tick rather than held (`ES_CONTINUOUS`): a hold would outlive a
        # crashed backend and the box would never sleep again; this stops with
        # the watcher. The timers taken by `_ensure_timers_owned()` are the
        # protection; this covers a plan changed behind GameCore's back.
        _last_activity_signal = _last_input
        await _signal_user_activity()
        return
    idle_mins = (time.monotonic() - _last_input) / 60
    # `cfg["sleep_mins"] and` — 0 is "never turn the screen off", not "turn it
    # off at zero minutes". Without the guard the box goes straight to sleep on
    # the first tick, which is what the option is there to prevent.
    if cfg["sleep_mins"] and idle_mins >= cfg["sleep_mins"]:
        await _enter("sleep")
    elif idle_mins >= cfg["screensaver_mins"]:
        await _enter("screensaver")


async def run() -> None:
    log.info("standby: watcher started")
    while True:
        await asyncio.sleep(_POLL_SECS)
        try:
            await _tick(load_config())
        except Exception:
            log.exception("standby: tick failed")
