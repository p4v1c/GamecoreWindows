# 4 — Backend, services

Where the logic lives. No FastAPI import exists in this directory: a service
is callable from a test, a script, or another service.

[`controller_profiles.py`](08-controller-pipeline.md) is big enough to have its
own document.

---

## process_manager.py

Module-level singleton: `process_manager = ProcessManager()`. A session's
process tree lives in a Windows **Job Object** (`platform/jobs.py`); the job is
what is suspended, resumed and killed, never one process. What describes a
session is `game_session.py`'s `Session`.

State: `_sessions` (every resident `Session`, launch order), `_launching`,
`_switching` (an `asyncio.Lock`), `_seq` (the run number), `_pending_key` /
`_pending_system`.

| Member | Role |
|---|---|
| `kill_process_group(proc)` | module-level: kill a child started **without** a job and every process it started (`processes.kill_tree`). Used by `routers/update.py` |
| `_resident_cap()` / `MAX_SESSIONS` | 3 resident sessions with 16 GiB of RAM installed (`processes.physical_memory()`), else 2 |
| `is_running` / `is_foreground` | a session exists / one is on the screen (`_launching` counts as both) |
| `foreground_session`, `background_sessions`, `background_session`, `current_game`, `session_state()` | what the box runs, in the shapes the routers and the WS send |
| `_reap()` | drops sessions whose job is empty; announces `game:finished` for an adopted one (no watcher awaits it) |
| `launch(...)` | `_refuse_launch()` (foreground, one resident game, cap, `session.launch_blocker()`), claims `_launching`, builds argv (`command_line.launch_argv`), spawns with `jobs.spawn()` into `logs.launch_output()`, records the session, broadcasts `game:started`, starts `_watch()` |
| `background()` | `_freeze()`: the interface to the front (`window_focus.hide`), **then** `job.suspend()`; a failed suspend gives the screen back to the game |
| `foreground(session_id)` | freezes whatever holds the screen the same way, `job.resume()`, `window_focus.activate()`; `_undo_swap()` when the resume fails |
| `_save_state()` / `_clear_session()` | write/remove `config/session.json` atomically ([7](07-config-and-data.md#configsessionjson)) |
| `adopt_orphan()` / `_adopt_row()` | at startup, re-open a job a previous backend left: only when `jobs.open_job(name)` succeeds, the job is not empty and its first process matches the boot id and start time on record (`process_identity.py`) |
| `kill(session_id)` | `job.terminate()`: the whole tree, frozen processes included; adopted: `_kill_orphan()` |
| `_watch(session)` | polls `job.alive()` every `WATCH_POLL_SECS`, then releases saves, clears the slot it owns, closes the job, records playtime if > 5 s, broadcasts `game:finished` |

Decisions that look odd until you know why:

1. **`_launching` is claimed synchronously**, before the first `await`. Two
   concurrent `launch()` calls would otherwise both pass the checks.
2. **The game starts suspended** (`CREATE_SUSPENDED`), joins its job, then
   runs: nothing it starts can be born outside the job.
3. **Terminate, no polite close.** Several emulators answer a close request
   with a confirmation dialog that cannot be clicked from a gamepad.
4. **No `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.** A backend restart (update,
   crash) closes its handles; the game must survive to be adopted. The job's
   first process holds a duplicate of the job handle, so the job's name lives
   as long as it does: that is what `open_job()` finds after a restart.
5. **A session ends when its job is empty**, not when its first process
   exits: a launcher that starts the game and quits leaves the game running.
6. **The interface goes in front before the freeze**, never after: a frozen
   window that has the foreground never answers the activation messages, and
   Windows then refuses to put it behind (measured on the real PC).
   Suspends and resumes are serialised by `_switching` for the same reason:
   each awaits a window move before it freezes.
7. **The job is persisted, and the game is never killed at shutdown.** See
   `config/session.json` in [7](07-config-and-data.md). Killing it in the
   lifespan would take unsaved progress with it on every update, and would do
   nothing for a crash, where the lifespan never runs.

`services/launch.py:_spawn` catches `OSError` (missing binary, no rights, an
executable that asks for elevation) and answers **503** naming the system and
the binary, plus a `game:failed` broadcast the UI turns into a toast.

### `game_session.py` — one session

`Session`: the job, `adopted`, identity (`game_key`, `system_id`, `rom_path`,
`exec_path`, `launch_args`), `session_id`, `state`, and the time it spent
frozen (`bg_since`, `bg_total`). `alive()` is `job.alive()`; `played_secs()`
is wall time minus frozen time; `describe()` is the WS/API shape; `to_disk()`
the `session.json` row. `from_disk(row, job, session_id)` rebuilds an adopted
one.

### `command_line.py` — a launcher's args to argv

`split_args(text)`: shell-style quotes group words and are removed, a
backslash is an ordinary character (POSIX `shlex.split` ate the separators of
`C:\Games\x.iso`). `launch_argv(exec_path, exec_args, rom_path)` appends the
ROM as one word.

### `session.py` — the desktop games appear on

`launch_blocker()` says why a game started now would not reach the TV (nobody
logged on to the console, the backend in another session), `desktop()` names
the session, user and primary display, `display_env()` is the environment a
game starts with (the backend's own plus `SDL_GAMECONTROLLERCONFIG_FILE`).
See [1](01-runtime-topology.md#on-windows-the-backend-runs-in-the-console-users-session).
`wayland_env()` / `kscreen_available()` are Linux helpers `display.py`,
`standby.py` and `desktop_power.py` still call (steps 5 and 7).

### `window_focus.py` — who is in front

| Function | Does |
|---|---|
| `present(system_id, job)` | waits (`PRESENT_TIMEOUT_SECS`) for the new game's first window and brings it to the front: Windows keeps the foreground for the interface, which received the last input |
| `hide(system_id)` | brings the interface's largest window (`SHELL_EXECUTABLES`: `GameCore.exe`, dev `electron.exe`) to the front, before a freeze |
| `activate(system_id, job)` | brings every window of a resumed game to the front, topmost last; the layout of several windows is kept |
| `game_windows(job)`, `wait_for_window(job, timeout)` | the job's app windows; shared with `fullscreen_enforcer.py` |

All best effort: a window left behind is one press away, and an exception
would report a suspend or a resume that happened as failed.

---

## `themes.py` — discovery and the completeness rule

`SURFACES = {"splash", "shell"}`, `SDK_VERSION = 10`.

`_read_manifest(dir)` returns a validated manifest or `None` with a logged
reason. It rejects a folder whose `theme.json` is unreadable, whose `id` does
not equal the directory name (the id is a directory name, never a path —
`_safe_id` enforces `^[a-z0-9][a-z0-9_-]{0,63}$`), or whose `entry` file is
missing.

A theme is `compatible` only if its `api` is not newer than `SDK_VERSION` **and**
it declares every surface. Anything less is listed but not selectable, with the
reason in `warnings` — the UI needs to say *why*, not just refuse. That rule is
the one users feel: it is what makes a theme all-or-nothing, so there is never a
half-dressed UI.

`get_active()` is the box's choice (`config/theme.json`); with no file it falls
back to `shipped()`: `SHIPPED_DEFAULT` when it loads, else the built-in look.
A new profile starts on `shipped()` too.

Covered by `backend/tests/test_themes.py`.

## `gamepad_monitor.py` — evdev, the source of truth for input

Runs as a lifespan task. It exists because the browser Gamepad API cannot be
trusted for the Guide button and cannot see anything while a fullscreen
emulator owns the display.

| Function | Role |
|---|---|
| `run()` | main loop — rescans for devices every few seconds, watches each |
| `_find_gamepad_devices()` | in `gamepad_devices.py`: `path → (name, uniq, is_pad, vendor, product)` for every readable `/dev/input/event*` |
| `pads_by_key()`, `dup_indexes()`, `_can_read(path)` | in `gamepad_devices.py`: one entry per physical pad, per-name duplicate counters, permission probe |
| `_watch_device(path)` | reads one device until it disconnects or is cancelled |
| `ActivityFilter` | in `input_activity.py`: which events mean somebody is at the pad (a button down, the d-pad hat, a quarter of an axis's travel), for `standby.on_input()` |
| `_on_guide_pressed()` | the double-press logic; the pair means what held the screen at its **first** press (`_guide_from_game`), then `_suspend_to_interface()` |
| `_suspend_to_interface(gesture, foreground_only, from_game)` | suspends the foreground session and broadcasts `gp:guide` (`backgrounded`, `failed`, or `home` for a pair begun in the interface) |

It also drives `controller_registry` on connect/disconnect,
`controller_profiles.apply_profile()` / `release_profile()` for the pad's
per-emulator configuration, and `standby.on_input()` — which is how a
controller button wakes a sleeping box.

---

## `controller_registry.py` — console-style player slots

Assigns P1…P4 and keeps them stable across reconnects.

| Function | Role |
|---|---|
| `normalize_mac(value)` | extracts a lowercased `aa:bb:…` from any MAC-ish string |
| `key_for(uniq, path)` | stable key: the MAC when known, else the device node |
| `identity(key, vendor, product)` | what a profile remembers a pad by: the MAC, else `vendor:product` (a devnode changes on replug; two identical pads without a MAC read as one) |
| `has(key)` / `label_for(key)` / `player_for(key)` | lookups |
| `connect(key, label)` | assigns the **lowest free slot**; idempotent for a known key |
| `disconnect(key)` | frees the slot, returns the player number it held |
| `player_for_mac(value)` | slot for any MAC-bearing string — used to attach a sysfs battery to a player |
| `snapshot()` | `[{player, label}]` ordered by slot — what `/api/sysinfo` returns |

---

## `controller_roster.py` — the pads the controller screen draws

`connected_pads()` joins `gamepad_monitor.roster()` (the last scan), the
registry slot, the sysfs battery and the pad's identity into one row per pad:
`{id, player, name, kernelName, vendor, product, connection, battery, charging,
known, controls, analogTriggers}`.

| Field | Source |
|---|---|
| `id` | `controller_registry.identity()`: the MAC, else `vendor:product` |
| `known` | `mapped` (a wizard capture exists for this vendor:product), `sdl` / `table` (`resolve_name` source in `SDL3_TRUSTED`), else `unknown` |
| `controls` | the standard controls its SDL mapping binds (`parse_controls`): the capture first, else `sdl2_probe`'s built-in mapping; `null` when SDL has none |
| `analogTriggers` | a trigger bound to an axis; `b6`-style triggers are buttons |

---

## `battery.py`

| Function | Role |
|---|---|
| `read_batteries()` | sysfs → `[{name, level, charging}]`; only supplies whose device has a gamepad button (`_is_gamepad`): a mouse or keyboard battery is not a controller pill |
| `_check(batteries)` | **pure** — returns the alerts to send for this poll, so it is unit-testable |
| `run()` | polls and broadcasts `gp:battery` |

The UI renders it as a toast; in-game Electron paints a native always-on-top
HUD instead, because the React toast is hidden under the emulator.

---

## Rest mode: `rest_mode.py`, `resume.py`, `bt_wake.py`, `wake_source.py`

| Function | Role |
|---|---|
| `rest_mode.enter()` | close the launch gate, wait for a launch in flight, `close_everything()`, `sudo -n systemctl suspend`; `ServiceError` 409/503 |
| `rest_mode.close_everything()` | `process_manager.kill()` per session, poll groups (`EXIT_WAIT_SECS`), SIGKILL survivors (`FORCE_WAIT_SECS`) |
| `rest_mode.woke()` | reopen launches; also after `REOPEN_AFTER_SECS` if no wake is reported |
| `launch.close_gate(reason)` / `open_gate()` / `is_launching()` | the gate `launch()` checks first |
| `resume.run()` | `gdbus monitor` on logind; `parse_sleep_signal(line)` is pure; holds `sleep_guard`'s lock while it listens |
| `sleep_guard.take()` / `release()` | the logind delay lock for sleep (`systemd-inhibit … cat`; released by closing stdin) |
| `sleep_guard.before_sleep()` | `PrepareForSleep(true)`: gate, `close_everything(6, 2)` and `bt_wake.turn_off_pads()` together (one failing does not cut the other short), bounded by `SLEEP_BUDGET_SECS` (12 s), then release, always |
| `sleep_guard.after_wake()` | sweep again after a sleep, take the lock back (first step of `on_resume()`) |
| `resume.on_resume()` | `wake_source.report()`, `sleep_guard.after_wake()`, gate, `standby.exit_standby()`, `display_ready.wait_for_display()`, `display.restore_preferred()`, the `wake_settle_secs` delay, then the wake count and `power:resumed`, `gamepad_monitor.request_reprofile()`, `bt_wake.set_wake_permissions()`; each step isolated |
| `wake_source.remember()` / `report()` | `event_count` of every `/sys/class/wakeup/wakeup*` on `PrepareForSleep(true)`, compared first thing after the wake; logs `wake_source: woken by <name> (<USB product>) +N` (+ `/sys/power/pm_wakeup_irq` when set). `moved()` is pure |
| `resume.announce_sleep()` / `is_sleeping()` | `power:sleeping` on `PrepareForSleep(true)`; true until `power:resumed` |
| `display_ready.wait_for_display(settle, root, max_wait)` | poll `/sys/class/drm/card*-*` (`status`, `enabled`, `dpms`) every 200 ms until one connector is lit, `WAIT_MAX_SECS` = 10 s; no DRM at all means no wait. `connector_ready()` is pure |
| `bt_wake.pads_to_arm(objects, roster_macs)` | **pure**: paired devices with `Icon` input-gaming or a roster MAC, never keyboard/mouse, whose `WakeAllowed` is false |
| `bt_wake.others_to_disarm(objects)` | **pure**: paired devices whose `Icon` is in `NOT_PADS` (keyboard, mouse, tablet) and `WakeAllowed` is true; `set_wake_permissions()` sets them false in the same pass that arms the pads |
| `bt_wake.pads_to_turn_off(objects, roster_macs)` / `turn_off_pads()` | **pure** / edge: connected paired pads (same pad rule as `pads_to_arm`); `Device1.Disconnect` on each, concurrently, `BUSCTL_SECS` each; returns how many |
| `bt_wake.run()` | a pass at start, then whenever a new pad MAC joins `gamepad_monitor.roster()` |
| `display.restore_preferred()` | after a wake, put the confirmed mode back if the output came up in another |

`gamepad_devices.is_pad_keys(keys)` is the pad rule (BTN_SOUTH, not a keyboard);
`install/bin/gamecore-wake-setup` mirrors it in bash and
`backend/tests/test_wake_setup_helper.py` runs both over the same devices.

## `standby.py`

| Function | Role |
|---|---|
| `load_config()` / `save_config(cfg)` | `config/standby.json` |
| `get_state()` | `active` / `screensaver` / `asleep` |
| `_run_cmd(*argv)` | helper, returns success |
| `_screen(on)` | DPMS on/off |
| `_governor(gov)` | `cpupower frequency-set -g …` — optional, needs a sudoers rule |
| `_enter(stage)` | stage transition + WS broadcast; `sleep` waits for `ui_dark()` (3 s cap) before DPMS |
| `ui_dark()` | the UI's fade to black is over (`POST /api/standby/dark`) |
| `exit_standby()` | wake — **unconditional** |
| `resume_after_restart()` | called from the lifespan; forces the screen on at startup |
| `on_input()` | called from the evdev loop on any controller button |
| `_ensure_timers_owned(enabled)` | re-asserts `desktop_power.claim()` (standby on) or `claim_x()` (standby off), at most once a minute |
| `run()` | the idle poll loop |

A running game blocks standby entirely — GameCore's standby, which is not the
only one on the box.

**Blocking GameCore's own standby is not enough, and a film going dark mid-play
is how that was found.** Nothing in the journal accused the watcher, and it was
innocent: Stremio for 27 minutes, melonDS for 43, Ryujinx for 74, and not one
`active → screensaver` in any of them. What blanked the television was the
**X server**. GameCore's session is an X11 session
(`/usr/share/xsessions/gamecore.desktop` → `gamecore-session` → `kwin_x11`) and
PowerDevil is not running in it, but a real X server is — with its own screen
saver and its own DPMS delays, which nothing ever reset: a DualShock 4's buttons
are tagged `ID_INPUT_JOYSTICK`, which the X server does not count as input, so a
film in a kiosk browser is perfect silence to it.

`_signal_user_activity()` cannot cover that. It speaks to
`org.freedesktop.ScreenSaver`, which in GameCore's own session **nobody owns** —
the box said `ServiceUnknown: The name is not activatable` fifteen times a
minute for the whole of every game. It is a best effort for the desktop session,
not the protection.

What protects it is `desktop_power.claim()`, which takes the idle timers
themselves (both arms: PowerDevil's screen-off *and* `xset s` / `xset dpms`).
That claim cannot be made once at startup and left: the backend is a **system**
service and starts at boot, before any session exists — boot at 07:39:06, the
session's compositor at 07:39:20 — and each switch between GameCore's session
and the desktop is a new X server with its delays back at the default. So
`_tick()` re-asserts it, before the early return it takes for a foreground game.

The X arm is never handed back, even with standby switched off: X's delays
cannot see a pad or a game, and handing them back blanked Ryujinx ten minutes
in. Standby off keeps X's timers at zero (`claim_x()`) and only gives the
desktop's PowerDevil timeout back (`release()`).

**The state is in memory; its effect is not.** `xset dpms force off` is a
property of the X server, and X belongs to SDDM — it does not restart with the
backend. So a box asleep when the backend restarted (crash, `systemctl restart`,
the end of an OTA) came back holding `_state == "active"` with the TV still
dark. `on_input()` tests `_state != "active"` before waking, so a button press
did nothing; pad events arrive over evdev rather than X, so DPMS never re-armed
by itself; and `POST /api/standby/exit` returned at its first line. SSH or a USB
keyboard were the only ways out.

Hence both halves: `resume_after_restart()` in the lifespan repairs a box already
in that state, and `exit_standby()` no longer short-circuits on
`_state == "active"` — asking to wake up is never a no-op.

`_run_cmd` awaits `session.display_env()` for the environment of the tools it
runs (on Linux a display probe that could block for seconds stood behind it).

---

## `cover_pipeline.py` — orchestration

| Function | Role |
|---|---|
| `resolve(system, filename, refresh=False)` | the five-tier resolution, [drawn here](02-request-flows.md#3-resolving-a-cover) |
| `_from_gamemedia(sid, target, base, refresh)` | the ScreenScraper / LaunchBox tier — returns `(cover, unreachable)` |
| `_id_urls(kind, value)` | candidate `(url, ext)` pairs for a disc ID, best first |
| `_regions(letter)` | region-code expansion for GameTDB paths |
| `_fetch_by_id(kind, value, base)` | downloads the first candidate that exists; raises `Unreachable` if none answered |

The order is: cache → embedded icon → **gamemedia** → disc-ID lookup → name
scrapers. gamemedia sits below the embedded icon because that one is exact and
costs no network, and above the name scrapers because it is the only tier that
is exact on a **cartridge** — there is no icon to extract and no serial to
read, so everything below it guesses from the filename while a CRC32 the
ScreenScraper database recognises identifies the game outright.

It asks for a flat jacket (`gamemedia.COVER_ORDER`), so what lands in
`emu/covers/` is the same *kind* of picture the pipeline has always produced.
Nothing downstream sees a different shape.

Negative results are written as `.miss` files, honoured for 7 days, so an
offline box does not retry the network on every scroll.

**A `.miss` is only written for a real "not found".** The pipeline could not tell
"nobody has a cover for this game" from "nothing answered": network errors were
swallowed lower down, `resolve()` saw `None` either way, and it wrote the marker
unconditionally. The worst trigger was also the most likely — a new box has no
network until the owner configures Wi-Fi *from the GameCore interface*, and
`prefetch` starts 15 s after boot. The first run therefore walked the whole
library with no connectivity and marked every game; once Wi-Fi was up,
`resolve()` returned 404 from the marker before trying anything, so the library
stayed blank **for a week**, silently, and rebooting did not help. An exhausted
TheGamesDB quota did the same.

`utils.rom_in_root` is the containment guard: `filename` comes from the
`{filename:path}` route parameter, which accepts slashes and `..`, and the value
went straight into `roms_root / filename`. Same rule `launch_game` has always
applied — resolve, then check against the root. It moved to `utils.py` when
`routers/media.py` became its third caller: two near-identical copies were a
convention, three would have been a rule to remember rather than import.
`cover_pipeline._rom_in_root` remains as an alias, because that is the name the
cover tests assert on.

### `backend/utils.py` — the shared facts

Three things live here precisely because they used to exist in several copies:

- **`SYSTEM_ID_RE`** — the alphabet a system id must match before it is joined
  onto any served root (the `pergame.py` router imports it; two routers once
  carried their own identical compile).
- **`atomic_write(path, text)` / `atomic_write_bytes(path, data)` / `atomic_write_json(path, data, **dumps_kwargs)`**
  — write through a temp file in the same directory, then `os.replace()`, with
  `encoding="utf-8"` spelled out. `write_text()` truncates first and writes
  second, and much of what the backend writes happens at startup or at a launch
  — the exact moments someone can cut the power at the wall. This implementation
  existed six times (configgen `helpers/base.py`, `pergame`, two bezel
  modules since removed, `merge` ×2, `ota` ×2) before converging; the dumps
  kwargs pass through unchanged so every caller keeps its exact on-disk shape.
- **`fmt_size(n)`** — 1024-based, and since v1.2.15 the labels say so
  (KiB/MiB/GiB); the arithmetic always divided by 1024 while the labels claimed
  decimal units. The rom-manager addon carries its own deliberate copy
  (self-contained by contract) and moved in the same release.

## `local_media.py` — read the game itself

Offline and exact. Nothing here guesses from a filename.

| Function | Role |
|---|---|
| `_ps3_icon(rom)` / `_ps3_sfo(rom)` | `PS3_GAME/ICON0.PNG`, `PARAM.SFO` |
| `_ps4_icon(rom)` / `_ps4_sfo(rom)` | `sce_sys/icon0.png`, `param.sfo` |
| `_psp_read(rom, inner)` / `_psp_sfo(rom)` | pulls a file **out of the ISO** via `iso9660` |
| `_gc_wii_id(rom)` | 6-char game ID from a GameCube/Wii image header |
| `_playstation_serial(rom)` | PS1/PS2 serial (`SLUS-20946`) from `SYSTEM.CNF` inside the image |
| `extract_icon(system_id, rom, dest)` | writes the embedded icon, or `None` |
| `get_title(system_id, rom)` | real title from embedded metadata — why PS3 folders show a name, not `BLES01234` |
| `disc_id(system_id, rom)` | `(kind, id)` for an exact online lookup, e.g. `("wii", "GALE01")` |

## `iso9660.py` — minimal ISO reader

`class Iso9660` with `open(path)` (classmethod, autodetects the sector layout,
returns `None` for a non-ISO such as a compressed `.cso`), `_sector`,
`_read_extent`, `_entries` and `read_file("PSP_GAME/ICON0.PNG")`
(case-insensitive). Supports `with` via `__enter__`/`__exit__` — use it, the
factory only closes the handle on its own failure paths.

## `scraper.py` — the network tier

| Function | Role |
|---|---|
| `_normalize(name)` | lowercase alphanumerics, for fuzzy matching |
| `_name_variants(base)` | the spellings to try against the CDN index |
| `_get_index(client, system_name)` | fetches and caches the libretro directory listing |
| `fetch_cover(rom_path, system_id, dest)` | libretro first, then TheGamesDB |
| `_fetch_tgdb_cover(name, system_id, dest)` | needs `THEGAMESDB_API_KEY`, silently skipped otherwise |
| `_region_rank(n)` | prefers the region you probably want when several match |
| `Unreachable` | raised when **no lookup completed** — distinct from "not found" |
| `_is_transient(status)` | 403, 408, 425, 429 and anything ≥ 500 mean "ask again later" |

`fetch_cover` and `_fetch_by_id` return `None` only when the lookups completed
and nobody has the cover; they raise `Unreachable` otherwise. That distinction is
the whole point — `cover_pipeline` caches the negative result for seven days, and
a dead network is not evidence about a game. `_fetch_tgdb_cover` raises on any
non-200 too: a 429 body has no `games` key either, so an empty result used to be
indistinguishable from a genuine miss.

## `gamemedia/` — ScreenScraper + LaunchBox

Two vendored files (`gamescrape.py`, `gamemedia.py` — standard library only,
see `VENDORED.md` in that folder) and one adapter, `__init__.py`, which is the
**only thing the rest of the backend imports**.

What it adds: identification by **file hash** instead of by filename, and 54
media types per game instead of one cover.

| Function | Role |
|---|---|
| `available()` | is any source configured? Cheap, no network. Everything else is a no-op when it is False |
| `status()` | `{available, screenscraper, launchbox_index, cache}` |
| `resolve(system_id, target, only=…, refresh=…)` | the manifest. `None` = *the tier could not be asked*, distinct from a manifest with `found: false` |
| `media_file(system_id, filename, slug)` | local path to one media, downloading it if it was deferred |
| `media_index(manifest)` | `media` as the API exposes it — descriptors only, **never the source URL** |
| `to_game_meta(manifest)` | a manifest in the `GameMeta` shape `/api/metadata` has always returned |
| `cached(system_id, filename)` | the manifest already on disk. No network, no thread |

**A PC game's minimum spec** comes from neither source. `steam_store.py`
(GameCore's, not vendored) asks the public Steam store after `resolve()`, only
for packs with `scraper.steamStore` (`STEAM_STORE_SYSTEMS`): `storesearch` by
the scraped title, then `appdetails` → `pc_requirements.minimum`, cut to one
short line per key (`meta.requirements`: `os`, `cpu`, `ram`, `gpu`, `disk`).
A store entry counts only at `NAME_ACCEPT` with the same episode numbers, so
FIFA 22 never takes FIFA 23's spec. The answer, a miss included, is stored as
`steam: {appid, checked_at}` in the manifest and not asked again until
`?refresh=1`; a network failure stores nothing.

The LaunchBox tier also records up to three gameplay shots
(`screenshot-gameplay`, `-2`, `-3`, deferred like the rest): the Shelf PC back
uses the first as key art and the others for its shot row.

**How a game is identified**, best first: the file's CRC32+MD5+SHA1 when it is a
real file under 384 MiB (certain — a renamed or mistagged ROM still lands on the
right game); the `TITLE` its `PARAM.SFO` declares when it is a PS3/PS4/PSP
directory (the directory name is whatever the user typed, and ScreenScraper 404s
on it); the parsed filename otherwise.

**Three states, not a boolean.** "This game is not in the database" is a fact
worth caching. "The quota is spent", "the network is down" and "nothing is
configured" are not, and a manifest carries `unreachable` to say so. Collapsing
them is what would carve a permanent "no such game" over a whole library swept
before the credentials were entered.

**A media can be a picture of nothing, and that is a fifth state.** Ask
ScreenScraper for a `box-2D-back` and one of the answers is a **chroma-key
plate**: a valid PNG of flat `#00FF00`, cut to the exact box dimensions of the
system. This is not its convention for "I have none" — that answer is a 7-byte
`NOMEDIA` body, which `_looks_like_media()` already rejects. Probed across nine
regions for FIFA 19 on PS3: `de` serves the plate, the other eight serve
`NOMEDIA`. The plate is a placeholder somebody contributed, indistinguishable
from art by anything that does not look at the pixels. It downloads, it decodes and it draws, so nothing downstream could tell
— the Shelf theme ended up recognising them in the browser by quantising the
pixels, and every other consumer showed a green slab. Measured on the reference
box: nine titles across five systems, four distinct files, one per box shape.

Recognised at download time by **compression, with no image decoder** (the
backend has no Pillow, in neither `requirements.txt` nor either venv). A flat
fill is the one thing PNG compresses to nothing, and the two populations do not
touch:

| | bytes per pixel |
|---|---|
| the nine plates | at most **0.0093** |
| the 52 real scans | at least **1.3055** |

`gamescrape.looks_like_flat_plate()` reads the dimensions out of the IHDR — a
fixed offset, nothing decompressed — and compares against `FLAT_PLATE_BPP`,
which sits 5× above the highest plate and 26× below the lowest real scan.

Such a media is recorded `blank` rather than kept. `blank` is **settled, not
incomplete**: `failed` would mean "retry me" and this one comes back identical
every time, so `_manifest_complete()` accepts it and `media_index()` leaves it
out entirely — absent means "this game has no back cover", which is true and
directly actionable, where present-but-green makes every consumer work it out
from the pixels. A plate filed before this existed is still caught: the
completeness check opens any media the manifest records as under 8 KiB, so it
costs one rescrape, once, and cannot loop.

**The tier fallback is per MEDIA as well as per game.** `lb_everything()` used
to be reachable only when ScreenScraper did not find the title at all, was
unreachable, or had no credentials — so a game ScreenScraper knew kept every gap
ScreenScraper had. `_top_up_blanks()` now asks the other tier for the blank
slugs alone, never for a deferred or failed one, and never for `meta` (the text
stays one tier's, in one language, which is what `lang` promises). It costs one
local index lookup and one download per slug replaced; a game with nothing blank
costs nothing. Measured on the reference box, against the index already sitting
there: **all nine plates have a real `Box - Back` in LaunchBox, every one
matched at a similarity of 1.00.**

That number was first reported as seven, and how it was got wrong is worth more
than the number. The two "misses" — FIFA 19 and Breath of the Wild — came from
querying the index with a hand-rolled normalisation instead of `find_game()`:
one dropped the platform filter and matched FIFA's *Windows* entry, the other
could not get `The Legend of Zelda: Breath of the Wild` past its own colon.
`find_game()` has neither problem. That first re-measurement was also taken on
FILENAMES, which is wrong for a PS3, PS4 or PSP dump — those are identified by
the `TITLE` in their PARAM.SFO. Measured properly, through the same path the
code takes, the matcher is **63 for 64**, and the one failure is instructive:
the disc says `FIFA 19`, LaunchBox files it as `FIFA 19: Legacy Edition`, and
`difflib` scores that pair at **0.48** while scoring `FIFA 09` at **0.909**.
Text similarity ranks it exactly backwards, because its ratio is penalised by
the length the two titles do not share.

`search._subtitled()` is the last resort added for it — an identity question
rather than a similarity one, which is what a local index allows and a remote
search endpoint does not. It accepts an entry only when it is our title plus a
subtitle (`:` or ` - `), the stem normalises to exactly our title, the episode
numbers agree, and it is the ONLY such entry on the platform. Skyscraper solves
the same problem with a containment rule (`scraperworker.cpp` promotes a
candidate to 100 % when the search name appears in it and the edit-distance
score is already ≥ 50) — a guard a short title with a long subtitle cannot
clear, so it would not fire here; and its own subtitle stripping sits commented
out in `nametools.cpp`, tried and turned off. Neither is copied. With the rule
in, the library is **64 for 64**, and games that already matched never reach it.

**And the trigger needed a caller.** `_manifest_complete()` refusing a plate is
worth nothing on its own: `cover_pipeline.resolve()` returns as soon as
`emu/covers/<game>.png` exists, so a library whose covers are all cached never
reaches this tier again. Measured after the release that shipped the check — the
nine plates had not moved. `warm()` is what pulls it now, via
`has_stale_plate()`: it already walks every game once per boot with the manifest
open, and it asks a question narrower than completeness on purpose, because that
one answers False for a language change too and would turn a boot into a
rescrape storm.

`warm()` asks twice: before its downloads, for plates already on disk, and
after, for a deferred media it just fetched. `fetch_media()` does not check for
plates, so a new game's back used to stay a green slab until the next boot.

Two design points that are GameCore's, not upstream's:

- **Nothing is downloaded before it is asked for.** A scrape fetches
  `EAGER_MEDIA` (the cover, one image — what the default theme displays and
  what the previous pipeline downloaded) and records the rest as `deferred`
  with its URL. `media_file()` fetches one on demand: one HTTP request, no
  second `jeuInfos`, no quota. Fetching all 28 media of a PS3 game up front
  would be ~34 s per title at the 1.2 s rate ScreenScraper requires.
- **A stored URL carries no credentials.** ScreenScraper puts `devid`,
  `devpassword`, `ssid` and `sspassword` in the query of every media URL it
  returns, and a deferred media keeps its URL in `game.json`. They are stripped
  on write and restored from the live configuration on read, so the developer
  account is never written to disk — it is shared by every user of the same
  softname and gets revoked if it leaks.

Synchronous by design (`urllib` + a thread pool), so every call runs in
`asyncio.to_thread` behind a single lock. The lock is about the quota, not
thread safety: prefetch warms three games at a time, and three concurrent
scrapes would put three `jeuInfos` in flight ignoring each other's 1.2 s.

Configured through four environment variables — `SCREENSCRAPER_DEV_ID`,
`SCREENSCRAPER_DEV_PASSWORD`, `SCREENSCRAPER_USER`, `SCREENSCRAPER_PASSWORD`
([where they come from](07-config-and-data.md#environment)). The LaunchBox tier
needs no account at all, only `python3 gamescrape.py --refresh` run once — and
**with `GAMECORE_PATH` set**, or the index lands in `~/.cache/gamescrape` where
the backend never looks. That is not hypothetical: it is where the index sat on
the reference box, with `status()` reporting `launchbox_index: false` and the
tier silently off since the day it was populated. `resolve_index_dir()` now
answers that question once for both the CLI and the backend, and
`test_the_cli_and_the_backend_agree_on_where_the_index_lives` keeps the two
answers equal.

A full install builds it once, in `install/steps/build-media-index.sh`, and
never fails the install over it. `--minimal` and `GAMECORE_SKIP_MEDIA_INDEX=1`
skip it, and both print the single command that adds it later.

The backend **never** builds it — 106 MB of download from inside an HTTP
handler would block the request for minutes. A box with no index simply has no
LaunchBox tier, and says so in the manifest notes.

## `metadata.py`

`resolve(system, filename)` → description, year, genres, players, rating.
Disk-cached and negative-cached. Two sources: **gamemedia first** (hash
matching, French synopses when it has them, developer/publisher/age ratings,
and no network at all on the LaunchBox side), then TheGamesDB unchanged —
still the only source on a box with neither configured.
`_genre_names(client)` resolves the genre id table once;
`_search_name(system, filename)` builds the query;
`_fetch_tgdb(platform_id, name)` does the call.

**The seven keys the API has always returned keep their name, type and
meaning** — `found`, `title`, `description`, `year`, `genres`, `players` (a
number), `rating` (the age rating, a string). GameMetaPanel and every theme
already read them. Two traps that shaped the mapping: ScreenScraper writes
players as a range (`"1-3"`), which as a string silently stops the player-count
chip from rendering, so it is reduced to its maximum; and its `rating` is a
score out of 20 where TheGamesDB's is a label, so the normalised 0–1 score
arrives on a new key, `score`, and `rating` stays the label.

A `found: false` from gamemedia is deliberately **not** copied into this
service's cache: gamemedia already caches its own negative, and only when the
tiers really answered. A second copy with a 7-day TTL would outlive the retry
gamemedia does for free the day the credentials appear or the quota resets.

## `sfo.py`

`parse_bytes(d)` and `parse(path)` — PARAM.SFO key/value table, `{}` on any
error. Same binary format on PS3, PS4 and PSP. The addons repo has its own
copy in `shared/py/`; this one additionally exposes `parse_bytes()` for data
already in memory.

## `rom_scanner.py`

`clean_name(filename)` (strips extension and bracketed tags like `[!]`,
`(USA)`), `matches_ext(filename, extensions)`, and
`iter_rom_files(roms_path, extensions, scan_dirs)` — alphabetical, applying the
common exclusions. The rom-manager addon keeps a mirrored copy.

### One game, one entry

`shadowed_by_a_descriptor(entries)` → `{hidden file: the entry that owns it}`.

A PS1 dump is a descriptor plus its tracks, and duckstation scans `*.bin` *and*
`*.cue` — so a single-disc game was listed twice, same name, same artwork. Both
extensions have to stay scannable (a `.cue` is the only launchable file of a
multi-track dump, and plenty of dumps ship as a bare `.bin`), so the dedup is at
the listing level and the descriptor wins.

Two rules, because either alone leaves duplicates on a real library:

- **what the descriptor names** — a multi-track dump's `Game (Track 01).bin`
  shares no stem with `Game.cue`. Transitive: an `.m3u` hides its `.cue` files
  and their tracks go with them in the same pass;
- **what shares its stem** — the common case of a dump renamed while the
  descriptor kept pointing at the old name. Measured on the reference box:
  `Dragon Ball Z .cue` names `Dragon Ball Z (Europe).bin`, which does not
  exist, while `Dragon Ball Z .bin` sits next to it.

`.m3u` lines are read whole rather than tokenised — disc names contain spaces,
and a token pattern matches only the tail after the last one. A directory with
no descriptor is never read, so a library without disc images is untouched.

**A file may only be hidden if the entry replacing it will actually be listed**,
so the system's `extensions` are passed in and a descriptor it does not scan
hides nothing. Forgetting that emptied a library in production: `config/` is
excluded from the OTA, so a box installed before `*.cue` was added to
duckstation keeps a catalogue scanning `*.bin` and not `*.cue`. The `.cue` was
on disk and shadowed the `.bin`; it was then filtered out by `matches_ext`, and
PS1 went from one game to none. Any rule that hides an entry has to check that
something visible takes its place.

The **value** of that mapping is what `playtime_repair` needs: hiding a file
that a player has hours on would orphan them.

## `playtime_repair.py`

`rekey_shadowed_entries()` → number of rows moved. Runs once in the lifespan,
before anything can serve a library.

`game_key` is the filename the library listed. So the day the library stops
listing a file, every hour recorded against it stops being reachable: the row
is still there, nothing points at it, and a game played for hours reports as
never played. Hiding a `.bin` behind its `.cue` does exactly that — measured
before this existed, one row on the reference box: 15 minutes, 21 sessions.

So the rename is followed, in `playtime` and in `sessions`. If both keys
already have a row (the player launched the `.cue` too) they are **merged**,
not picked between: seconds and session counts add up, `last_played` is the
later of the two. Idempotent by construction, and it never deletes a row it has
not merged into its replacement.

The map is built from **what `iter_rom_files` actually returns**, not from
"the descriptor wins" — so it corrects in either direction. That distinction is
not theoretical: `config/` is excluded from the OTA, so a box installed before
`*.cue` was added to duckstation lists the `.bin` and not the `.cue`, and a
release that moved the playtime onto that `.cue` left it as unreachable as the
bug it was fixing. A disc group no member of which is listed is left alone —
moving playtime onto an invisible entry only hides it further.

The covers and metadata caches need no equivalent: both are keyed on the
*stem*, which `.bin` and `.cue` share.

## `system_split.py`

What `scripts/split-systems.py` does — move an unmigrated box's games from a
split pack's tile (`dolphin`, `mgba`) to one tile per system. `plan(packs)`
reads only; `edit_configs()` and `edit_grid()` are pure and return the new file
contents, so the dry run prints exactly what `apply()` writes. `apply()` only
renames, never overwrites, and keeps the old tile while a game is left in its
folder. Bezel files and their config are not moved: the overlay left the
Windows edition. Nothing calls it but the script: the procedure and its guarantees are in
[07](07-config-and-data.md#splitting-a-system-scriptssplit-systemspy).

## `prefetch.py`

`run()` walks the library at startup and calls `warm(system, filename)` so the
first scroll is not a spinner. Two passes, in this order:

1. **Cover and metadata**, three games at a time, through the same pipelines the
   API uses — an already-cached game costs one `stat()`.
2. **The rest of the artwork a theme draws**, once every game has a cover:
   `gamemedia.warm()` for each entry, sequentially.

The order is the point. Someone opening the library thirty seconds after boot
wants the grid full, not one title's detail panel complete.

The second pass costs no `jeuInfos`: a scrape downloads `box-front` and records
every other media with its URL, so warming is a plain download of URLs already
on disk. It fetches only what the manifest says the game *has* and does not yet
*hold* — which is why it is nearly free on a warm box and why a game the cover
pipeline has not reached yet is simply skipped until the next boot.

What it pulls is `gamemedia.WARM_MEDIA`: the flat jacket, the three faces the 3D
box is built from (`box-3d`, `box-spine`, `box-back`) and the two captures the
detail panel shows. Override with `GAMECORE_WARM_MEDIA` — comma-separated, empty
to warm nothing, for a box on a slow line or with a very large library.

`wait_until_nobody_is_playing()` is the deferral both this worker and
`standby_videos` use: it returns once no game is in the foreground.

## `standby_videos.py` + `standby_picks.py`

The clips the CRT standby plays on its TV. Never part of `WARM_MEDIA`: a clip is
5 to 20 MB. `run()` sweeps 3 minutes after boot, then every 3 hours:

1. `standby_picks.rank()` orders the library by value: hours played (capped at
   50 h) plus how recently (fades over 30 days), summed over every profile
   (`playtime` + `profile_playtime`). Unplayed games follow, mixed across
   consoles.
2. `plan_eviction()` trims the clips on disk to the cap, least valuable first;
   a clip whose ROM is gone ranks last.
3. Downloads `video-normalized`, else `video`, in value order through
   `gamemedia.media_file()` (same lock, same ScreenScraper pacing), after
   `prefetch.wait_until_nobody_is_playing()` each time. Only games that already
   have a manifest; it never scrapes.
4. Stops at the cap: `GAMECORE_STANDBY_VIDEO_CAP_GB` (default 5, `0` drops every
   clip). A fetched clip that does not fit is dropped and the sweep stops.

Eviction goes through `gamemedia.drop_file()`, which files the media back as
`deferred` with its URL and `bytes`: deleting the file alone would make the
manifest incomplete and cost a rescrape. The kept `bytes` lets the next sweep
skip a clip it knows will not fit instead of fetching it again.

`playlist()` is what `GET /standby/videos` returns; see
[03](03-backend-routers.md#standbypy).

Measured on the reference box before this existed: `box-front` 47/47, but
`box-3d` and `screenshot-gameplay` only 41/47 — the six missing were simply the
games nobody had opened yet, each one costing a round trip behind the scraper's
1.2 s spacing the moment someone did.

---

## `fullscreen_enforcer.py`

For apps with no fullscreen command-line flag (`"fullscreen"` key on a system
entry). `enforce(system_id, cfg, job)` (fire-and-forget, from `launch.py`)
waits for the job's first window (`window_focus.wait_for_window`), makes it
borderless fullscreen (`window_control.make_borderless_fullscreen`) until it
covers its monitor (`ATTEMPTS` x `RETRY_SECS`: an app that resizes itself once
loaded undoes the first try), then brings it to the front. Borderless, not
exclusive: the HUD alerts are a topmost window exclusive fullscreen hides.
`cfg["wm_class"]` (Linux) is accepted and unused; `timeout_s` is read.

---

## `auth.py` — shared password

| Function | Role |
|---|---|
| `_write_private(path, data)` | atomic write, **0600 from the very first byte** |
| `_auth()` / `_secret()` | read `config/auth.json` and `config/auth_secret` |
| `is_configured()` | whether a password was ever set |
| `set_password(new, reset_secret=False)` | argon2id hash; **always bumps `generation`** |
| `verify_password(password)` | argon2 verify |
| `_mac(secret, payload)` | HMAC-SHA256 |
| `make_cookie()` | `expiry.generation.HMAC(secret, "expiry.generation")` |
| `check_cookie(value)` | expiry + generation + MAC |
| `blocked_for(ip)` | seconds still to wait — 0 means not blocked |
| `register_failure(ip)` / `register_success(ip)` | in-memory backoff, exponential after 5 failures |

Bumping `generation` is how "change password" invalidates every live session
without storing any session state.

---

## `ws.py` and `db.py` (backend root)

`ws.py` — `connect(ws)` (accepts and replays `game:running` if a game is
already up), `disconnect(ws)`, `has_clients()`, `broadcast(event, data)`, `set_current_game(game)`.
`broadcast` sends to all clients concurrently, each send bounded by
`SEND_TIMEOUT` (2 s); a client that errors or stalls is dropped and its socket
closed so the UI reconnects. Awaiting sends one by one with no timeout let one
stuck client stall launches, addon CLI output and the OTA log pump.

`db.py` — `get_db()` returns a live `aiosqlite` handle, re-opening it if the
cached one has gone stale; `init_db()` creates the `playtime` and `sessions`
tables. Schema in [7](07-config-and-data.md#playtimedb).

---

## Services added by the recent phases

Ten modules and two sub-packages arrived after the inventory above was written.
These entries are **summaries, not full function inventories**: each names what
the module is for and the one decision that is not recoverable by reading its
signatures. The docstrings carry the rest.

### `launch.py` — everything before the spawn

`launch(system, system_id, rom_path, game_key)` runs, in order: foreground
check → ROM path inside `romsPath` → resume-or-refuse (one resident game) →
catalogue args → BIOS gate → USB notice → `standby.exit_standby()` → wait for
pad profiles → release stale slots → profile saves → per-game config → pack
`prepare_launch` → pack `launch_command` → `process_manager.launch()` →
the window task (`start_window_task()`: `window_focus.present` or
`fullscreen_enforcer.enforce`; the per-game settings window gets it too). Refusals raise `LaunchRefused(status, detail)`; the router maps them.
The foreground check also claims a module-level `_launching` flag, held until
the spawn returns: a second launch arriving meanwhile is refused (409) before
any preparation. Without it, the refused launch re-placed and then released the
running game's profile saves.
Every preparation step is budgeted and never raises: a late config costs a
session, a failed launch costs the box. The one exception is
`_place_profile_saves()` (`profile_saves.place`): when a pack that separates
saves cannot be pointed at the profile's folder, the launch is refused
(`game:failed`), since the game would write into another profile's save.
A launch that fails after that step gives the saves back at once
(`_release_profile_saves()`): no game ends, so nothing else would.

`_pack_launch_command()` calls a pack's optional `launch_command(rom_path,
exec_path, exec_args, players, opts)` hook (`generator.py`), which may return
another `(exec_path, exec_args)`. `players` comes from `_connected_players()`
(slot, registry key, vendor, product, name); `opts` is `None` when autoconfig
is off for the pack, so the hook may change the command but not the config.
None, an exception or `PACK_PREPARE_BUDGET` keep the usual command. melonDS
uses it for local multiplayer ([10](10-catalog-and-install.md#melonds-local-multiplayer)).

### `pack_library.py` — what a pack adds to its library listing

Called by `GET /systems/{id}/games` (`routers/games.py`) around the folder scan:

| Function | What |
|---|---|
| `sync(system_id, roms_path)` | runs the pack's optional `sync_library(roms_dir, covers_dir, home)` hook (`generator.py`) before the scan. For a system whose games live in another program's library: the Lutris pack writes one stub per installed game, so covers, playtime and favourites work on ordinary files ([10](10-catalog-and-install.md#pc-games-lutris)). Never raises: the stubs already there are listed |
| `shows_extension(system_id)` | `roms.showExtension`, default true. False: every entry's `ext` is sent as `""`, and the views print no extension |

The pack table is read once per process, like `local_media`'s.

### `systems.py` — the grid rows

`get_systems()`, `get_apps()`, `list_all()`, `find(system_id)`. Hot-reloads
`config/systems.json` and `config/apps.json` on mtime change and expands
`@HOME@` / `@GAMECORE_DATA@` / `@GAMECORE_PATH@` at read time.

### `pack_art.py` — pictures a pack ships for themes

`pictures_in(folder)` lists `art/<name>.<ext>` (webp, png, jpg, svg; plain
names only). `art_for(id)` merges `<tier>/<id>/art/` name by name over the
catalogue's tiers (shipped, OTA, `config/catalog.d`), so a pack.json override
keeps the shipped photo, then lays the operator's `assets/art/<id>/` on top.
Folders only, no `load_catalog()`: a grid request costs a few stat calls. `with_art(rows)` adds `art: {name: url}` to the grid rows served by
`GET /systems`, and `picture(id, name)` is what `GET /systems/{id}/art/{name}`
serves. Looked up by name, never joined into a path.

### `display.py` — mode switching with a revert timer

`state()`, `set_mode()`, `confirm()`, `revert_now()`, `ui_scale()`,
`set_scale()`, `preferred_mode()`. The timer lives in the backend because a
refused mode blanks the settings screen that would otherwise run it.

### `paths.py` — the two roots

Owns `GAMECORE_ROOT`, `GAMECORE_DATA` and `_LAYOUT`. Nothing outside it may join
a writable directory onto a root; `test_paths.py` enforces that on the AST.
Fully described in [7](07-config-and-data.md#path-resolution--backendservicespathspy).

### `platform/` — every call into Windows

`backend/services/platform/` holds the OS calls, one module per concern, under
the names the services call. Nothing else imports `ctypes.windll`, `winreg`,
`_winapi` or `win32*`, or checks `os.name` (`gamecore-review` greps for it).
A Linux mechanism leaves the tree in the port step that lands its replacement
([dev log](../dev-log/windows-port.md)); the modules so far:

| Module | Function | Does | Replaces (Linux) |
|---|---|---|---|
| `filesystem.py` | `link_dir(link, target)` | a **junction** to an existing local directory; `FileExistsError` / `FileNotFoundError` otherwise | `os.symlink` (needs admin or Developer Mode on Windows: `WinError 1314`) |
| | `is_link(path)` | junction or symlink, also when its target is gone | `Path.is_symlink()`, False for a junction |
| | `link_target(path)` | where it points, without the `\\?\` prefix | `os.readlink` |
| `processes.py` | `boot_id()` | `BootId` under `HKLM\...\Memory Management\PrefetchParameters`, incremented every boot; `""` when unreadable | `/proc/sys/kernel/random/boot_id` |
| | `start_time(pid)` | creation time from `psutil`, ms precision, as a string; `""` for no such process | field 22 of `/proc/<pid>/stat` |
| | `kill_tree(pid)` | kills a process and its descendants (`psutil`); for a child with no job (the update script) | `os.killpg` |
| | `physical_memory()` | installed RAM in bytes, 0 when unknown | `os.sysconf` (absent on Windows: the resident cap silently stayed at 2) |
| `jobs.py` | `spawn(cmd, env, stdout)` | starts `cmd` suspended, assigns it to a new named Job Object (`Local\GameCore-<uuid>`), gives the first process a duplicate of the job handle, resumes it; returns a `GameJob` | `start_new_session=True` + `create_subprocess_exec` |
| | `GameJob.alive()`, `.pids()` | any process left (`JobObjectBasicAccountingInformation`), every pid (`JobObjectBasicProcessIdList`) | `os.killpg(pgid, 0)`, `/proc` scan |
| | `.suspend()`, `.resume()` | `NtSuspendProcess` / `NtResumeProcess` on every process; suspend is all or nothing | `SIGSTOP` / `SIGCONT` to the group |
| | `.terminate()`, `.close()` | `TerminateJobObject` (exit code 137); let go of the handle, processes untouched | `SIGKILL` to the group |
| | `open_job(name, leader_pid)` | re-open a job a previous backend started; only `Local\GameCore-` names | the saved pgid |
| `console_session.py` | `own_session_id()`, `console_session_id()`, `session_user(id)`, `primary_display()` | `ProcessIdToSessionId`, `WTSGetActiveConsoleSessionId`, `WTSQuerySessionInformationW`, `EnumDisplayDevicesW` | `DISPLAY` / `XAUTHORITY` probing |
| `window_control.py` | `top_level_windows(pids)`, `windows_of_executables(names)` | `EnumWindows` by owner pid / by process image, visible unowned app windows | `_NET_CLIENT_LIST` + `_NET_WM_PID` / `WM_CLASS` |
| | `bring_to_front(hwnd)` | `SetForegroundWindow`; when refused, again with `AttachThreadInput` to the foreground thread, never to a frozen one (`SendMessageTimeoutW` probe) | `_NET_ACTIVE_WINDOW` |
| | `make_borderless_fullscreen(hwnd)`, `covers_its_monitor(hwnd)` | frame styles off (`SetWindowLongPtrW`), `SetWindowPos` over the monitor (`SWP_ASYNCWINDOWPOS`), in per-monitor DPI | `_NET_WM_STATE_FULLSCREEN` |
| `audio.py` | `list_outputs()`, `get_volume()`, `set_volume(percent)`, `set_default_output(id)` | active render endpoints and the default one (pycaw over Core Audio); the default is set for every role through `IPolicyConfig`. COM is initialised once per worker thread and never released: comtypes frees its pointers at garbage collection, after which `CoUninitialize` crashed the process | `wpctl` (PipeWire) |
| `com.py` | `ensure_com()`, `wmi_query(namespace, wql, fields)` | COM initialised once per worker thread and never released; WQL rows as dicts, `OSError` when WMI refuses | — |
| `network.py` | `adapters()`, `join_adapters(adapters, configs)` | every adapter: name, GUID, `kind` (wifi / ethernet / other from the NDIS medium), connected, `virtual` (Hyper-V, WSL, VMware, VirtualBox, VPN), IPv4, gateway, DNS, MAC; `MSFT_NetAdapter` joined to `Win32_NetworkAdapterConfiguration` | `nmcli dev show`, `nmcli con show --active` |
| `wifi.py` | `scan(rescan)`, `connect(ssid, password)`, `disconnect()`, `connected_ssid()`, `adapter_guid()`, `access()` | WinRT `WiFiAdapter`: one row per access point (signal 0-100 from dBm, security label, channel from the frequency), joins with a `PasswordCredential`, `(ok, wrong_password, error)` | `nmcli dev wifi` |
| | `forget(ssid, interface_guid)` | Native Wifi `WlanDeleteProfile` on the profile named after the SSID (netsh exits 0 whether or not it found it) | `nmcli con delete` |
| `radios.py` | `enabled(kind)`, `set_enabled(kind, on)`, `read_state(state)` | WinRT `Radio` for `WIFI` and `BLUETOOTH`: the quick-settings switches; `RadioBlocked` when disabled below Windows | `nmcli radio wifi`, rfkill |
| `bluetooth.py` | `paired_devices()`, `scan(seconds)`, `pair(mac)`, `unpair(mac)`, `format_address(raw)` | WinRT association endpoints of the classic and LE protocols (address, connected, paired); a scan watches both for `seconds` and drops unnamed LE advertisers (random-address phones and beacons, 20 of 28 in one measured scan); pairing answers Windows' confirm / display-PIN requests | `bluetoothctl` |
| `media_types.py` | `pin_media_types()` | the content type of everything served (`SERVED`: bundle, fonts, pictures, sounds, clips) registered over the Windows registry's; called once by `main.py` at import | `/etc/mime.types`, which Linux keeps consistent; a bare Windows has no `.webp` and some PCs map `.js` to `text/plain` |

Callers: `process_identity.py` (`boot_id`, `start_time`), `process_manager.py` (`jobs.py`, `kill_tree`, `physical_memory`), `session.py` (`console_session.py`), `window_focus.py` and `fullscreen_enforcer.py` (`window_control.py`), `profile_save_paths.py`
(the junction a profile's save folder becomes), `main.py` (`pin_media_types`), `routers/settings/audio.py` (`audio.py`), `routers/settings/wifi.py` (`wifi.py`, `network.py`), `wifi_radio.py` (`radios.py`), `routers/settings/bluetooth.py` (`bluetooth.py`). `shutil.rmtree` and
`Path.unlink()` remove a junction without entering it; the test suite's own
`catalog/` link depends on that (`test_platform_filesystem.py`).

### `configgen/` — the controller pipeline

`__init__.py` (apply/release/scan/forget), `controllers.py` (SDL resolution,
`Pad`, `ResolvedName`), `snapshots.py`, `mapping_db.py`, `seed.py`, `derive.py`,
plus `helpers/`. One generator per emulator lives in `catalog/<id>/generator.py`,
not here. All of [8](08-controller-pipeline.md).

### `installer/` — obtaining what a pack declares

`providers.py`, `fetch.py`, `applier.py`, `manifest.py`. The applier is what
refuses a pack that names a file it does not carry — the check the repository did
not have when a refactor deleted a directory `arch.sh` still read, and an install
died at 66 % on a fresh machine months later.

### `bios.py` — three verdicts, not two

Whether the system file an emulator needs is present **and right**. The support
ticket it exists to delete: a missing or corrupt BIOS produces no message a player
can act on — the emulator refuses to start, or starts on a black screen, and
nothing on screen names a file. Every case cost three round trips before anyone
knew what was being discussed.

The verdicts are deliberately distinct, because "absent" and "present but wrong"
need different sentences. What a pack declares is data (`bios` in `pack.json`);
how it is checked is here. `required: false` matters: a BIOS gate that blocks a
launch it should not is GameCore inventing a fault.

### `pergame.py` — one game's settings

`<DATA>/config/per-game/<system>/<id>.json` is the original; the emulator's file
is **derived**. Nothing here knows what a setting *means* — no table maps
"internal resolution" onto thirteen vocabularies, because chasing that map across
emulator releases is what makes Batocera's configgen impossible to keep current.
Every write records what it displaced so removal can put it back key by key.
Detail in [10](10-catalog-and-install.md#pergame--and-why-it-is-required-on-every-emulator-pack).

### `gameid.py` — which game this is

A per-game config is a file named after a game, so something must answer "which
game is this" before anything can be written. The answer differs per system only
in **where it is read from**:

| strategy | source |
|---|---|
| `ps3`, `ps4`, `psp` | a Title ID in `PARAM.SFO` |
| `gcwii` | the 6 characters at the top of a disc image |
| `playstation` | the serial in `SYSTEM.CNF` |
| `wiiu` | the title id in the dump's own `meta.xml` |
| `hash` | the CRC32 of the file |
| `filename` | the normalised name — nothing else was available |

Pluggable per system, because an N64 cartridge dump carries no serial at all.

### `controller_capture.py` — the mapping wizard's engine

Turns "the owner pressed this" into an SDL mapping line. What arrives from the
kernel is an evdev code (`BTN_SOUTH`, 0x130); what must be written is SDL's
vocabulary (`b0`, `a3`, `h0.1`), where the numbers are SDL's joystick indices, not
the kernel's codes.

**Nothing bridges those two by inspection.** SDL assigns indices by walking the
device's declared capabilities in a fixed order, so index 0 is "the first button
this device declares" — and the same physical button is a different number on a
pad that declares one extra key. `sdl_layout()` reproduces that walk. Guessing
here produces a mapping that looks plausible and binds the wrong buttons.

### `usb_devices.py` — the peripherals that are not SDL gamepads

The autoconfig pipeline knows exactly one kind of device: a pad declaring
`BTN_SOUTH` on an evdev node. Everything else — the GameCube adapter Dolphin
drives over raw libusb, light guns, dance mats — is invisible to it. This module
is that second roster, declared per pack under `usb`. It **never refuses a
launch**: a USB accessory is optional by nature, so blocking would be GameCore
inventing a fault. It only speaks.

### `logs.py` — the logs directory

One folder per section under `paths.logs_dir()` (`<DATA>/logs`), so the box can
be debugged without `journalctl`:

| Section | Written by | Cap |
|---|---|---|
| `logs/backend/backend.log` | `install()`, called by the lifespan: a `SectionFile` on the root logger, WARNING and above from every module | 2 MB × 3 |
| `logs/<section>/<section>.log` | `install()`: one `SectionFile` per entry of `SECTIONS` (controllers, media, session, network, ota, addons, ui), on the loggers of the backend modules listed there, from INFO. `ota` is every line of `update/linux.sh` the backend reads; `addons` the `gamecore-addon` output and notify events; `ui` what the interface reports (`POST /api/logs/ui`) | 2 MB × 3 |
| `logs/packs/<system>/` | a launched pack script, told by `child_env()` (`GAMECORE_LOG_DIR`); it must append | 4 MB, by `run()` |
| `logs/launch/<system>/<time>-<game>.log` | `launch_output()`, used by `process_manager.launch`: the emulator's stdout and stderr, written by the child itself (append mode) | newest 10 per system, by name; `run()` empties one past 4 MB every 30 s while it is written |

`SectionFile` creates its directory when it opens, so a purge never needs a
restart. `purge()` holds every section file's lock until the files are gone (a
record in between would reopen a file the delete then removes), and empties
rather than deletes what another process may still write: the newest launch log
of each system and the pack logs. `POST /api/logs/ui` text goes through
`one_line()`, so a newline cannot forge a record. `launch_output()` falls back to `DEVNULL`:
a log must not cost the player the game. Nothing here names an emulator.

A section's modules are raised to INFO, so their INFO lines also reach the
journal, as `standby`'s already did. A new module joins a section by adding its
logger name to `SECTIONS`. MAC addresses go through `mask_mac()`
(`AA:BB:CC:xx:xx:FF`). Wi-Fi passwords are never logged.

### `profiles.py` — who plays on this box

One file, `paths.profiles_dir()/profiles.json`: `{active, auto_login, profiles: [{id, name,
color, avatar, created, primary, theme, controllers}]}`, read and written under one lock,
atomically.

| Function | Does |
|---|---|
| `list_profiles()` | the state plus `PALETTE`; with no file, creates one unnamed primary profile that owns everything the box held before profiles. Unnamed = no profiles yet |
| `create(name, color, avatar)` | refuses (409) while the primary is unnamed; trims the name, refuses empty, over `NAME_MAX`, non-printable, or taken (case-insensitive); colour from `PALETTE` (default: the first unused), theme `themes.shipped()` (the look a new box shows, so it never inherits another profile's pick); avatar from `AVATARS` (the animals `frontend/src/settings/avatars.js` draws; None draws the initial, and a picture an older version offered reads as None) |
| `update(id, fields)` | the same checks, and `theme` through `themes.check`; the id never changes, so nothing keyed on it moves |
| `remember_theme(theme_id)` | the theme just picked in Settings → Themes becomes the active profile's; nothing without profiles |
| `add_controller(id, pad)`, `remove_controller(id, controller_id)` | the controllers shown on a profile's page (`{id, name}`); a pad is on one profile at most, so adding it to one takes it off the other. Display only: nothing (saves, login, theme) follows the pad. MACs are masked in the log (`logs.mask_mac`) |
| `delete(id)` | the record only (its saves folder stays on disk); refuses the last profile and the primary one (it owns the saves beside the ROMs); `active` passes to the primary |
| `set_auto_login(enabled)` | "Log in automatically" (`auto_login` in the file, off by default): the start screen is skipped and the box starts as the last profile |
| `active()`, `set_active(id)` | the profile the interface is used as. A switch, or deleting the active profile, is refused (409) while a game is on screen or suspended: a suspended game resumes without its saves being placed again, so the new profile would play in the old one's save. A profile saved without `theme` (made before this rule): when it leaves, it keeps the one on screen; when it arrives, it gets `themes.shipped()`, never the theme another profile just picked |

A file that does not parse, or lists no profile, is renamed `profiles.json.broken-<time>` and a fresh
primary profile is created: the old one stays readable for a repair by hand.
Favourites, recently played and playtime are not per profile yet.


### `profile_saves.py` — saves that follow the profile

Packs opt in with `profileSaves` ([10](10-catalog-and-install.md#profilesaves--saves-per-profile)).
At launch the active profile is player 1. A pack that `sharesEmulator` uses
its owner's declaration, folder and lock.

| Function | Does |
|---|---|
| `mode(pack, packs?)` | `per-instance`, `p1` (also for an object declaration), or None (shared, or `supported: false`) |
| `save_dir(profile_id, system_id)` | `paths.profile_saves_dir()/<id>/<system>/`; refuses an id that is not alphanumeric (`profiles.json` is hand-editable) |
| `player_dirs(players, system_id, mode)` | one entry per slot (4): the profile's folder, created on demand, or None for the primary profile, no profile, and players 2-4 in `p1` mode |
| `place(system_id)` | the owner's `place_saves(dirs, root, opts)` (generator hook, or the object declaration through `profile_save_paths`) with `player_dirs([active profile])`; raises when it cannot be told |
| `release(system_id, started)` | the same with every slot None, once the game that started at `started` has exited: the emulator started outside GameCore saves where it always did. Skipped when a later launch placed that emulator again; `place` and `release` share one lock |
| `release_session(session)` | `release` for an ended process-manager session; never raises, skips apps |
| `separate_systems(packs)`, `shared_systems(packs)` | labels of the systems whose saves follow the profile, and of the emulators that share theirs, for `GET /profiles` |

Never moves, copies or deletes a save: only the emulator's options change, or
a folder is renamed in place. Runs whether or not autoconfig is on for the
pack, because saves are not controller config.

### `profile_save_paths.py` — the object form of `profileSaves`

| Function | Does |
|---|---|
| `apply_keys(entries, folder, root)` | each `(file, entry)` option set to the profile's value, the file's own values remembered first in `root/.primary.json`; `folder` None puts the remembered values back (an option that was absent is removed) and forgets them. A missing file is created with the profile's options alone (never for the primary) |
| `apply_dir(path, target)` | `path` renamed `<name>.gamecore-primary` and replaced by a directory link (a junction, `platform/filesystem.py`) to `target`; None removes the link and renames the folder back. Refuses when its parent is missing, or when a real folder and its `.gamecore-primary` both exist (whose saves are whose cannot be told) |
| `read_key`, `write_key` | one `key = value` in an INI section, or a flat file; a new option in a flat file goes before its first `#include` |

### `playtime_rows.py` — playtime per profile

| Function | Does |
|---|---|
| `source()` | `(FROM target, params)` for the active profile: `playtime` for the primary (every figure from before profiles, untouched, and the only table the playtime repair and `split-systems` re-key), else that profile's rows of `profile_playtime`, with the same columns |
| `record(db, game_key, system_id, elapsed, when)` | adds a finished session to the active profile's row; the profile cannot change while a game is held, so it is the one that started it |

### `storage.py` — external disks

"I plug my ROM disk in" is one of the first three things anyone expects from a
console in a living room, and before this there was no udisks, no mount, nothing
anywhere in the repository — a disk plugged into the box did exactly nothing.

### `storage_monitor.py` — reacting to a disk arriving or leaving

Mounts an arrival, re-points its stable link, tells the frontend. **Nothing is
invalidated on the way, and that is not an omission**: a system's games are
scanned per request from its `romsPath`, and `romsPath` points at the stable link
rather than at a mount point — so the next scan already reads the new disk. There
is no cached library to clear, and adding a hook for one that does not exist would
be a line nobody could ever prove still works.

### `wifi_radio.py` — the Wi-Fi radio switch

| Function | Does |
|---|---|
| `radio_enabled()` | Windows' Wi-Fi radio (`platform/radios.py`): `True`/`False`, `None` with no Wi-Fi hardware, or when it fails or hangs |
| `set_radio(enabled)` | switches it and reads the state back; `ServiceError` 502 (Windows refused, its sentence as detail), 504 (no answer in `RADIO_SECS`), 409 (blocked below Windows: hardware switch, firmware, or an "on" that did not take) |
