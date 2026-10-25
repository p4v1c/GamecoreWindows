# 9 — Gotchas

Invariants that look like noise until they break something. Each one is here
because it already cost someone a debugging session.

## Process and lifecycle

**`_launching` is claimed synchronously.**
`ProcessManager.launch()` sets it before the first `await`. Two concurrent
launches would otherwise both pass the `is_running` check while the first was
still awaiting the spawn, and the box would run two emulators.

**A game is a Job Object, never a PID.**
`platform/jobs.py` starts it suspended, puts it in a new job, then lets it
run: everything it starts is born in the job. Suspend, resume and kill act on
every process of the job. A launcher that starts the game and exits, or a
helper process, would otherwise keep running (or keep the game running) under
the interface.

**Never close an emulator politely.**
Several answer a close request with a confirmation dialog that cannot be
clicked from a gamepad. `kill()` terminates the job (`TerminateJobObject`),
frozen processes included.

**No kill-on-close on the job, and the game holds its own job handle.**
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` would kill the game every time the
backend restarts (update, crash), and adoption (below) exists precisely so it
does not. A job's name dies with its last handle, so the game's first process
gets a duplicate: without it a restarted backend can never open the job again.

**The interface goes in front before a game freezes, never after.**
A frozen window that has the foreground never answers the activation
messages. Measured on the real PC: Windows then refuses to put it behind
anything, and attaching to its input thread (`AttachThreadInput`) made the
next `BringWindowToTop` wait for as long as the game stayed frozen.
`ProcessManager._freeze()` raises the interface first; `bring_to_front()`
never attaches to a thread that does not answer `WM_NULL` within 200 ms.

**The backend must run in the console session.**
A window opens on the desktop of its process's session; a service runs in
session 0, which has no visible desktop. `session.launch_blocker()` refuses a
launch rather than starting a game nobody can see (dev-log D8).

**No `--reload` (or `--workers`) for uvicorn on Windows.**
They switch it to the Selector event loop, where
`asyncio.create_subprocess_exec` raises `NotImplementedError` (the update
router). Games do not depend on it (`subprocess.Popen` in
`jobs.spawn`); `test_event_loop.py` pins the Proactor loop.

**Windows gives about 2 s between the suspend event and the sleep.**
No lock buys more (logind's delay inhibitor did on Linux). Anything added to
`sleep_guard.before_sleep()` must fit in `SLEEP_BUDGET_SECS` (1.5 s) or run
after the wake; `test_sleep_guard.py` checks the budget order.

**On a Modern Standby PC, `SetSuspendState` hibernates.**
Measured on the port's PC: S4, about 30 s to wake, and a pad cannot wake S4.
No call enters Modern Standby itself, so Rest mode's sleep there is every game
closed and the screen dark (dev-log D34).

**On a Modern Standby PC, a display switched off is a PC asleep.**
`SC_MONITORPOWER` off took the port's PC into Modern Standby within a
second (Kernel-Power 506) and paused the backend until a keyboard press,
even with system-required and execution-required power requests held
(measured 25/10/2026, 140 s and 277 s paused). The Linux box keeps running
with DPMS off, so `power.screen_off()` leaves such a display on, black,
under a display-required request (`platform/awake.py`), and Rest mode
announces its own sleep and wake (`resume.dark_rest_began()` / `ended()`).
The TV then shows black rather than going to its own standby.

**Device wake settings need an elevated backend.**
WMI's `MSPower_DeviceWakeEnable` lists nothing to an unelevated process, and
reads as "no device can wake the PC". `bt_wake` treats an empty list as
unknown and retries; a dev run from a normal terminal never arms anything.

**A one-frame crash is not a play session.**
`_watch()` only records playtime past 5 s, or a crash-looping emulator would
inflate the library stats.

**In-memory state whose effect is on disk (or in the X server) needs a
startup reconciliation.** Two instances of the same bug:

- Standby is a module variable, but a screen turned off stays off when the
  backend restarts. A box asleep when the backend restarted came back believing
  it was awake with the TV dark, and no button could wake it. The lifespan now
  forces the screen on unconditionally.
- The running game lived only in `_proc`. A restarted backend saw
  `is_running == False`, so `kill()` returned immediately and the emulator was
  fullscreen and unkillable. The job's name is persisted to
  `config/session.json` and adopted at startup, only when the identity matches.

The general shape: if the backend can be restarted while the effect persists, ask
at startup rather than assuming a clean slate. Note that a **crash** skips the
lifespan's shutdown half entirely, so cleanup-on-shutdown does not cover it —
which is why the game is persisted rather than killed.

**`task.cancel()` only schedules the cancellation.** The lifespan awaits its
four tasks with `asyncio.gather(..., return_exceptions=True)`; without that,
shutdown returned with them still mid-`await`.

**Killing a shell does not kill what it spawned.** The OTA timeout killed `bash`
and left its `rsync`, `pip` and `npm` writing into `/opt/GameCore` after the UI
had been told the update was aborted. `process_manager.kill_process_group()`
kills the whole tree (`processes.kill_tree`).

**A blocking call in a coroutine blocks everything.** The Linux display probe
ran synchronous `subprocess.run(timeout=5)` on the event loop, on every launch
and every standby transition; an unrelated `GET /api/systems` measured 4.7 s.
Window moves (`window_focus`, `fullscreen_enforcer`) run in `asyncio.to_thread`
for the same reason: `SetWindowLongPtrW` waits for the window's thread. Same
reason argon2 goes through `asyncio.to_thread` in the auth router.

**Explorer started by the GameCore shell is a folder window.** Explorer
becomes the desktop (taskbar, Start) only when the Winlogon `Shell` value it
reads names it; started under `gamecore-shell` it opens a File Explorer window.
Desktop mode therefore points the per-user value at `explorer.exe` for the
start and back (`install/bin/kiosk_shell.py`). Do not start Explorer from the
interface: it would also be elevated (`electron/desktop.js`).

**The kiosk window one pixel short.** Windows hides the taskbar only for a
window covering the whole monitor. Electron's frameless fullscreen came back
1920x1079 after a fullscreen round trip, and the taskbar stayed over the
interface. `electron/fullscreen.js` puts the bounds back; keep it wired in
`createWindow()`.

## The data root, seen from inside a sandbox

**A Flatpak's filesystem is a literal list of paths, and moving the data
invalidates it silently.** Every emulator override was granted
`--filesystem=/opt/GameCore` when that was where ROMs lived. After the
migration to `/userdata`, every host-side check still passed — the backend
found the ROM, `test -f` found the ROM, the launch command was correct — and
the emulator reported *ROM not found*, because inside its sandbox `/userdata`
does not exist. Nothing errors at grant time or at mount time; the path is
simply absent. The fix is one override per emulator
(`flatpak override --user --filesystem=/userdata`); `install/arch.sh` grants
**both** roots whenever they differ, and `scripts/migrate-userdata.py`'s
epilogue prints the loop for existing boxes. When a launch fails with a
file-not-found that the host disproves, check the sandbox before anything
else: `flatpak override --user --show <app-id>`.

The same shape applies in reverse to **config files**: a Flatpak emulator
reads `~/.var/app/<id>/config/…`, not `~/.config/…`. Both trees usually
exist, and editing the native one changes nothing
([07-config-and-data.md](07-config-and-data.md)).

**A per-profile save path outlives the launch that wrote it.** melonDS writes
its whole config back on exit, so `[Instance0] SaveFilePath` still names the
last profile's folder (`emu/profile-saves/`) when the next person launches.
`place_saves` therefore runs on **every** launch and empties a path under that
root for the primary profile; skipping it "because nothing changed" puts the
owner's progress in someone else's save. `profile_saves.release` puts the
defaults back when the game exits, so melonDS started outside GameCore saves
beside the ROM. It is skipped when the same emulator was placed again after
that game started: `_reap` frees a dead slot before its watcher runs, so the
next launch can place its saves first, and resetting them would send that
game's progress to the primary profile.

**A suspended game is not relaunched, so its saves are not placed again.**
Switching profile while one is held would have the new profile resume the old
one's game and save into it. `profiles.set_active` refuses while a game is on
screen or suspended (apps do not count).
([10](10-catalog-and-install.md#profilesaves--saves-per-profile))

## Backend wiring

**`/ws` must stay registered before the `/` static mount.**
The SPA catch-all (`html=True`) otherwise swallows the WebSocket upgrade and
the UI silently stops receiving events.

**Static mounts `mkdir` first.**
The mount loop in `main.py` creates each directory before mounting. A
conditional mount decided at import time used to leave `/covers` dead until a
restart on a fresh checkout.

**`_hot_load()` re-reads JSON on every call.**
Editing `systems.json` on the box takes effect with no restart — and a syntax
error breaks the API on the very next request.

**`config/` is the box's identity.**
Never touched by OTA. Not all of it is out of git, though — `systems.json`,
`apps.json` and the bundled themes *are* versioned, and the
installer regenerates the first two from `install/generated/*.dist` on every run. What is
never in git is the **state**: `auth.json` and `auth_secret`, `addons.json`,
`standby.json`, `theme.json`, `session.json`, `playtime.db`. Overwriting one of
those is data loss — the password, the addon registry, the play history.

## Input

**While a game runs, the UI ignores every gamepad event except `gp:guide`.**
Otherwise emulator input drives the launcher hiding behind the game.

**No UI sound while a game holds the screen, whoever asks.** `playSound`
checks `isPlaying()` itself: the bus is not its only caller (settings rows,
theme handlers, `sdk.system.playSound`).

**A test browser on the box hears the real pad and plays through its speakers.**
Headless Chromium against a dev server has no session, so the player's
in-game presses drove the audit pages (their controller screen opened by
itself). `legibility-audit.mjs` runs with `--mute-audio` and an empty
`navigator.getGamepads` unless `--init` fakes pads; do the same in any CDP
script of your own.

**A move must render inside the poll's frame.** A `setState` from a
`requestAnimationFrame` callback renders in a later task, after that frame is
painted: every cursor kept in `useState` (themes, the settings screen) showed
each move a frame late. `emit()` dispatches inside `flushSync`; do not move the
dispatch out of it (`gamepadPolling.test.ts` pins it).

**Guide needs a double press within 1 s.**
Enforced in both `useGamepad.ts` and `gamepad_monitor.py`. A single press must
never kill a running game.

**A PS pair means what held the screen at its first press.** Both paths see
the same presses, and the one that reports second finds the game already
suspended. Judged at the second press, the pair that left the game opened the
session menu on some suspends and not others.

**The browser cannot see the Guide button reliably.**
Chromium never exposes it, and the UI has no focus under a fullscreen emulator.
The SDL reader in the backend (background events allowed) is the primary path;
the browser is the fallback.

**The controller screen closes on a double □, and ○ does not close it.**
Every button there is a test target, so no button may be an action.
`CONTROLLER_CLOSE_MS` in `App.tsx`.

**`onGamepadFrame` is outside the "a game is running" guard, on purpose.** The
controller screen has to keep mirroring the pad, and a *held* combo is not
edge-triggered so it cannot arrive through the `gp:*` events. The consequence is
that any subscriber which **acts** on a frame must call `isPlaying()` itself.

That was learned the hard way: the L1+R1 theme rescue subscribed to frames and
reset the theme. L1+R1 held for two seconds is ordinary play input — Dolphin's
triggers, PS3 and PSP shoulders — so the box reset its own theme mid-game.

It looked random because it depended on the (since removed) Linux bezel. A
system **with** an overlay hid `mainWindow`, which suspends
`requestAnimationFrame` and stops the poll dead; a system **without** one left
the window sitting behind the emulator, still polling. So it happened on dolphin, rpcs3, ryujinx, cemu,
ppsspp, xenia and shadps4, and never on melonds, azahar, mgba, gopher64,
duckstation or pcsx2.

## Switch (Ryujinx)

**Ryujinx's save index must stay sorted.** LibHac loads `imkvdb.arc` into a
flat map and aborts the whole emulator at boot when a key is out of order
(program id, then type, then user). MK8 has an account and a device save
under one program id: written in the wrong order, Ryujinx would not start.
`eden_saves.pack_index` sorts.

**A Switch game must not start once without its update.** A save from a
newer version is rewritten in the old format (Mario Kart 8, v3.0.4 → v1.0.0).
Ryujinx's own library scan is too late on its first start: `title_updates.py`.

**Eden's profile folder and Ryujinx's must not share a name.** The Eden-era
pack linked `nand/user/save` to `<profile>/switch/save`; Ryujinx's dirs are
`user-save` and `save-index` (`as`). Without `as` Ryujinx would have been
pointed at Eden's layout.

**A box that switched Ryujinx's autoconfig off finds the Switch's off.**
`ryujinx` shares the `switch` pack's emulator, so its old exception is read
as an alias (`own_switch`). The reference box set `ryujinx: false` only while
Ryujinx was uninstalled: turn the Switch back on in Settings.

## Display

**Gamepad buttons are not a "user gesture".**
Chromium keeps WebAudio suspended until mouse or keyboard input. On a
controller-only kiosk the UI would be mute forever, hence
`autoplay-policy: no-user-gesture-required` in `electron/main.js`.

**`XDG_RUNTIME_DIR` or no audio at all.**
`start-ui.sh` exports it before launching Electron. A systemd service does not
inherit it, and Chromium reaches PipeWire through it.

## Security

### Untrusted strings reach the HUD

HUD toast text comes from WebSocket broadcasts, which include
`POST /api/addons/notify` (any addon can call it) and Bluetooth device names.
`escHtml()` and `safeColor()` exist for that. Never interpolate raw.

**Any path built from a route parameter is validated by containment, not by
pattern.** `Path(p).resolve().relative_to(root.resolve())`, in `launch_game()`,
`cover_pipeline._rom_in_root()` and `metadata._search_name()`. A
`{filename:path}` converter accepts slashes and `..`, so the rule has to hold
everywhere and not be re-derived per call site — the cover pipeline was the one
place it had been left out.

**An empty path component is a path too.** `PurePosixPath(".").parts` is the
empty tuple, so an entry id of `"."` passed both the `is_absolute()` and the
`".."` checks in the save-manager addon, resolved to the collection directory
itself, and `DELETE` then `rmtree`'d the whole collection. Guard `not rel.parts`
alongside the other two, and refuse `target == root` outright.

**The core is never LAN-exposed.**
`/api/*` is 403 through Caddy. The TV reaches it over loopback only. If you
add an endpoint, assume the LAN can never call it.

**…but "not LAN-exposed" is not "not reachable".** The box runs browsers — the
Firefox kiosk profiles and Stremio — and they can reach `127.0.0.1:8765`. A page
in one of them could auto-submit a form at `/api/games/kill`. Hence the
cross-origin middleware in `main.py`, and the same check on `/ws`, which needs it
most: a WebSocket handshake is a GET and is not subject to CORS at all. Adding a
non-GET endpoint requires nothing from you; adding another *transport* does.

**A rate limiter that can be tripped by an attacker is a denial of service.**
The global login breaker applied to every caller, so 25 failures spread over
throwaway keys — trivially produced by any unauthenticated LAN client — returned
429 to the owner with the correct password. It now only weighs on addresses
already known to have failed.

## OTA

### The OTA rebuild trap

The OTA archive used to ship `frontend/dist` **without** `frontend/src`. A box
therefore ran a CI-fresh bundle on top of sources frozen at first install, and
nothing reported the drift.

That holds until something rebuilds *on the box* (the Linux updater's own
fallback when a release shipped no `dist/`, or a hand-run `npm run build`).
Either regenerates `dist/` from months-old sources and silently reverts the UI.

It cost a real feature: the "Scan mapping" button shipped in v1.0.62 was
missing from a box running v1.0.66, while its backend route was live and
answering the whole time.

The archive ships `frontend/` whole (`scripts/package-release.py`) and the
update swaps the whole code root. **Keep it that way**, and remember the
consequence: local edits to a box's code root are gone after the next update
(they wait in `<code>.prev` until the one after). Push them.

**The updater cannot replace the code root from inside it.**
Windows refuses to rename a folder a running program lives in, and the
session, the backend, the shell and Caddy all run from `<code>`. Stage 1
(`update/windows.py`, run by the backend) only downloads and unpacks beside
it; stage 2 (`update/apply-update.ps1`) runs detached from the work folder,
stops them all, swaps, restarts ([13](13-release-and-ota.md)).

**Stop the shell with TerminateProcess, never `WM_CLOSE`.**
A plain close makes the kiosk shell rebuild its window instead of leaving
(found at port step 4); stage 2 uses `Stop-Process -Force`, like `taskkill /F`.

**Windows PowerShell turns stderr into errors.**
Under `$ErrorActionPreference = 'Stop'`, `& program 2>&1` throws on the first
line a program writes to stderr, even when it exits 0: an npm or pip warning
failed the install step, and would have rolled a good update back.
`Invoke-GcNative` and `Invoke-GcUpdateNative` relax it around the call.

**`VERSION` comes from the release archive, not from git.**
The repository pins `v0.0.0` (a checkout, older than every release); the real
version lives in the tags and in the archive's `VERSION`.

**`rsync -a` implies `-t`, so "is it newer than me?" is not a freshness test.**
A file the OTA installs arrives carrying the mtime it had *in the archive*, which
can be **older** than the derived file already sitting on the box. The measured
case is the SDL mapping database: `mapping_db.served()` merges the vendored
community file with the owner's captures, and a "newer than me" check answered
*no* to a database that had genuinely just changed — so the box kept serving the
previous release's merge for ever, silently, because the file was present and
looked right. Freshness is now a recorded **fingerprint** (size and mtime of both
sources, written into the served file's header) and any mismatch in either
direction is a rebuild.

Anything derived from a file an OTA replaces has this problem. Compare
fingerprints, not dates.

## Guards that do not guard

**A declarative guard only checks what it is asked to check.** `check-catalog.py`
used to test seeds against `seedMustNotContain`, a list each pack declares. Two
packs declared nothing, so their seeds shipped a real DualShock 4's SDL GUID for
months under a cheerful `17 pack(s) OK`.

What that cost is worth spelling out, because it is invisible from the box: both
packs are `snapshot-restore`, so their `generate()` only restores a snapshot if
one exists. **No code rebuilds the slot** the way Dolphin's or RPCS3's does. On
any box whose owner does not happen to own that exact pad, those two configs
describe a device that does not exist, and the controller is simply dead in those
two emulators until a manual mapping — with nothing anywhere to diagnose it.

The guard is now **non-declarative**: any 32-hex GUID whose vendor:product decodes
to a known pad is refused, in any seed, whether or not the pack asked. A rule
each subject opts into protects the subjects that were already careful.

**A shipped seed must name no controller.** This lesson was paid for twice. The
Dolphin generator still carries the first telling: the seed used to pin
`Device = SDL/0..3/PS4 Controller`, which is dead input on any box without a
DualShock 4. It was applied to dolphin, rpcs3, cemu, melonds and ryujinx — and not
to the other two, which is exactly how it came back.

**`\b` is not a boundary for a GUID.** The scanner matches a 32-hex SDL GUID
wherever it appears, using explicit lookarounds:

```python
re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")
```

`\b` would not fire after an **underscore**, which is a word character — and that
is precisely how Cemu writes its own (`0_0500…`). Azahar is worse: it escapes `:`
as `$0` inside a compound binding, so a stick direction's GUID reads `guid$00500…`
and the `0` of the escape runs into the GUID. A pattern that looks obviously
correct silently matches nothing on the two formats that needed it most.

## Build and release

**npm ≥ 11.6 does not run a dependency's install scripts, and exits 0 anyway.**
Unless `package.json` declares them under `allowScripts`. Electron's `postinstall`
is the one that downloads the ~180 MB binary; blocked, it downloads nothing, npm
reports success, and the build fails forty minutes later at an unrelated-looking
guard. The only trace is one `npm warn install-scripts` line.

**Do not generate that entry with `npm install-scripts approve <pkg>`.** By
default it writes a version-**pinned** entry (`"electron@31.7.7": true`). The
dependency is `^31.0.0`, so the first Electron bump stops matching, the postinstall
is blocked again, and the build breaks exactly as before — a month later, with
nothing having been touched. Write it by name, or pass
`--no-allow-scripts-pin`.

**Electron 31 cannot unpack itself under Node 26, and also exits 0.** extract-zip
2.0.1 / yauzl 2.10 hang without ever resolving their promise; node drains its
event loop and exits **zero** having written `dist/locales` and nothing else. No
error, no non-zero status, and the symptom is the same guard with the same
message as the npm problem above — which is why fixing only the first cause would
have left the job red and cost another full release cycle to learn. The workflow
pins `nodejs-lts-jod` (22.x), and `build.sh` now refuses a Node outside 18–22
*before* the forty minutes of `pacstrap` rather than letting it surface where it
is indistinguishable.

Two independent defects, stacked, the first hiding the second, both exiting 0.
When a build produces nothing and reports success, suspect more than one cause.

## Testing

See [`../TESTING.md`](../TESTING.md) for how to run the suite. The traps that
belong here:

**`TestClient(main.app)` runs the lifespan, and the lifespan writes real
emulator configs.** Not hypothetical: a `pytest` run rewrote Player 1 of this
machine's RPCS3 `Default.yml` and emptied Ryujinx's `input_config`. The chain is
short and nothing in it looks dangerous — the lifespan starts
`gamepad_monitor.run()`, the monitor reads the **real** pads, finds
whatever pad the developer left plugged in, and profiles it against a `HOME`
read at import time. `conftest.py` points `HOME` at a throwaway root before any
import; `test_home_isolation.py` now guards that it stays pointed there, because
nothing did.

**An experiment that cannot fail proves nothing.** The same question had been
asked once before and cleared: a run under a sentinel `HOME` came back empty. But
the write only happens when a pad is connected, and none was — the experiment
would have come back empty whatever the code did. Before trusting a green run,
ask what result would have been produced by the *broken* version. If it is the
same result, nothing was measured.

That is the general shape of the worst failures in this repository, and it has
appeared in several costumes: a CSS probe matching a `<canvas>` in a splash built
entirely from `<div>`s; a guard that only checks the patterns a pack asks it to
check (below); an `apply_udev()` that existed but was never called from `apply()`,
so the install went green without writing a byte. **A test that would not fail on
its own bug is worse than no test**, because it also stops anyone looking.

**`GAMECORE_PATH` is read at import time**, so whichever test module pytest
imports first would otherwise decide where the whole suite writes. `conftest.py`
is the only place the override is guaranteed to land in time — it runs before
any test module. It *sets* the variable rather than defaulting it: inheriting a
`GAMECORE_PATH` from the shell would point the suite at a real installation.

**A backend started by hand needs `GAMECORE_BACKEND_PORT`, not just `--port`.**
The cross-origin guard accepts a loopback `Origin` on the backend's *configured*
port, so with only `--port 8899` an `Origin: http://localhost:8899` is refused.

**Headless Chromium never fires `requestAnimationFrame` under
`--virtual-time-budget`.**
Both the splash (rAF-driven) and the 60 fps gamepad poll freeze, so nothing
happens and every assertion fails for the wrong reason. Polyfill it:

```js
window.requestAnimationFrame = cb => window.setTimeout(() => cb(performance.now()), 16)
window.cancelAnimationFrame = id => window.clearTimeout(id)
```

**Testing gamepad code needs no hardware.** Override
`navigator.getGamepads` with a fake pad object and drive the real poll loop.

**`npm run build` is the cheapest full check of the UI** — it runs `tsc` first,
so a type error fails the build.

**Two `DEBUG` flags.** `backend/config.py` and `electron/main.js`. Both must be
`false` on a device; `DEV` (Electron loading Vite) is
`DEBUG && ELECTRON_DEV=1`.

**CSS transitions do not progress under `--virtual-time-budget` either.**
The rAF polyfill above does not help: the animation clock is frozen, so a
transitioned property keeps its *starting* value forever. Measuring an element
mid-transition therefore reports "it never moved". Disable the transition in the
harness before asserting on geometry:

```js
const st = document.createElement('style')
st.textContent = '.my-track { transition: none !important; }'
document.head.appendChild(st)
```

Two days of "the carousel is broken" came from this. The carousel was fine.

**Match a component by what it actually renders.** A probe that looked for the
default splash by its `<canvas>` reported "no splash" in every run — that splash
is built entirely from `<div>`s. The test passed while proving nothing. Pin
components on something stable and verified: a root `z-index`, a class the
component owns.

## Themes

**Theme files must not be cached by the browser.** The loader can bust the entry
module's URL, but the entry's own relative imports (`home.js`, `settings.js`, …)
and its stylesheet resolve without that query, so the browser pins the first
version it ever saw. Editing a theme then changes nothing on screen — and a fix
shipped by update stays invisible. `/themes` is therefore mounted through
`_NoCacheStatic` in `backend/main.py`, which sets `Cache-Control: no-store` and
disables 304s.

**The splash must be chosen once, after the theme resolves.** Reading
`theme.splash ?? Splash` on every render mounts the *default* splash first —
the theme is still loading, so its splash is undefined — then swaps in the
theme's mid-animation. Both boot animations play over each other. `App.tsx`
freezes the choice in a ref and shows a plain opaque cover until `theme.loading`
clears.

**`overflow-clip-margin` is not reliably applied**, so do not build a layout that
depends on it. To let a focus ring overflow a clipping container, grow the clip
box with `padding` and pull it back with an equal negative `margin`: the content
box — and everything sized against it — is unchanged, and plain
`overflow: hidden` then clips further out.

**A settings page is a full-screen overlay, not a fragment.** Every page in
`components/modals/settings/` wraps itself in `<Overlay>`. Nesting one inside
another box puts a `position: fixed` layer in a flex container and destroys its
layout. `ThemesPage` was the one page that had forgotten its wrapper, and it
rendered as loose content stacked under the dashboard — invisible for as long as
it also had no route in `SettingsModal`.
