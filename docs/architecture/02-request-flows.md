# 2 — Request flows

The paths worth knowing end to end. Every arrow names the function that runs.

## 1. Launching a game

```mermaid
sequenceDiagram
    participant ui as LibraryScreen
    participant r as routers/games.py + services/launch.py
    participant pm as process_manager
    participant ws as ws.py
    participant el as Electron
    participant mon as overlay_monitor
    participant emu as emulator

    ui->>r: POST /api/games/launch {system_id, rom_path, game_key}
    r->>r: list_all() → system, else 404
    r->>pm: is_running? → 409 if busy
    r->>r: rom_path.resolve().relative_to(roms_root) → 403 if outside
    r->>pm: launch(exec_path, exec_args, rom_path, …)
    pm->>pm: session.launch_blocker() → 409 if not on the TV's desktop
    pm->>pm: _launching = True (synchronous claim)
    pm->>pm: command_line.launch_argv() + session.display_env()
    pm->>emu: jobs.spawn(): created suspended, put in a new Job Object, resumed
    pm->>ws: set_current_game() + broadcast("game:started")
    pm->>pm: create_task(_watch()) — polls the job until it is empty
    r->>r: window_focus.present() or fullscreen_enforcer.enforce()
    ws-->>ui: game:started
    ui->>el: window.gamecore.overlayStart(system_id)
    el->>el: loadOverlayConfig() → config/overlays.json
    el->>mon: spawn + {"cmd":"watch","system_id":…,"config":…}
    mon->>mon: find_window(wm_classes) — poll until it appears
    mon-->>el: {"event":"window:ready","rect":{…}}
    el->>el: createOverlayWindow() — transparent, always-on-top
    r-->>ui: {ok: true, game_key}
```

After the spawn, `launch.py` schedules one fire-and-forget task for the new
game's window: `window_focus.present()` brings its first window to the front
(Windows keeps the foreground for the interface, which received the player's
last input), or, when the system entry carries `"fullscreen": {…}`,
`fullscreen_enforcer.enforce()` makes it borderless fullscreen first.
`"gamepadTrigger"` is no longer acted on: it re-fired udev for Flatpak, and
Windows has neither.

## 2. Ending a game

Two ways in, one way out.

```mermaid
sequenceDiagram
    participant pad as controller
    participant gm as gamepad_monitor
    participant r as routers/games.py
    participant pm as process_manager
    participant ws as ws.py
    participant ui as React

    alt player presses PS twice within 1 s
        pad->>gm: evdev BTN_MODE
        gm->>gm: _on_guide_pressed()
        gm->>r: POST /api/games/kill
    else emulator exits on its own
        Note over pm: _watch() was already awaiting
    end
    r->>pm: kill()
    pm->>pm: job.terminate() — TerminateJobObject, the whole tree, frozen or not
    pm->>pm: _watch() sees the job empty: elapsed = played_secs()
    alt elapsed > 5 s
        pm->>pm: INSERT/UPDATE playtime (ON CONFLICT DO UPDATE)
    end
    pm->>ws: broadcast("game:finished", {elapsed})
    ws-->>ui: game:finished → clears session, hides overlay
```

Why it is built this way:

- **evdev, not the browser.** Chromium often hides the Guide button, and the
  UI is buried under a fullscreen emulator anyway.
- **Double press.** A single Guide press must never kill a game by accident
  (`GUIDE_DOUBLE_PRESS_MS`, mirrored in the frontend hook).
- **The job, never one process.** A launcher that starts the game and exits,
  or a game with helper processes, dies whole.
- **Terminate, never a polite close.** Several emulators answer a close
  request with a confirmation dialog nobody can click from a gamepad.
- **The 5 s floor.** An emulator that dies instantly is not a play session.

## 2b. Suspending and resuming a game

```mermaid
sequenceDiagram
    participant ui as React
    participant pm as process_manager
    participant wf as window_focus
    participant job as the game's Job Object

    ui->>pm: POST /api/games/background (double Home)
    pm->>wf: hide() — the interface's window to the front
    pm->>job: suspend() — NtSuspendProcess on every process
    pm-->>ui: game:backgrounded + state_snapshot
    ui->>pm: POST /api/games/foreground
    pm->>wf: hide() + outgoing.suspend() — if an app held the screen
    pm->>job: resume() — NtResumeProcess on every process
    pm->>wf: activate() — the game's windows to the front
    pm-->>ui: game:foregrounded + state_snapshot
```

The interface goes in front **before** the freeze: a frozen window that has
the foreground never answers the activation messages, and Windows then
refuses to put it behind. Suspends and resumes are serialised (`_switching`);
each one awaits a window move before it freezes.

## 3. Resolving a cover

```mermaid
flowchart TD
    start["GET /api/covers/{system}/{file}"] --> cache{"emu/covers/&lt;system&gt;/&lt;name&gt;<br/>.png / .jpg exists?"}
    cache -->|yes| serve["FileResponse"]
    cache -->|no| miss{".miss file<br/>younger than 7 days?"}
    miss -->|yes| none["404 — UI shows the fallback tile"]
    miss -->|no| t2["local_media.extract_icon()"]
    t2 -->|"PS3 ICON0.PNG · PS4 icon0.png<br/>PSP ICON0.PNG via iso9660"| serve
    t2 -->|nothing| t3["gamemedia.resolve()<br/>(skipped if no source configured)"]
    t3 -->|"ScreenScraper by CRC/MD5/SHA1<br/>or PARAM.SFO title · then LaunchBox"| serve
    t3 -->|nothing| t4["local_media.disc_id()"]
    t4 -->|"GC/Wii header · PS1/PS2 SYSTEM.CNF"| fetch["_fetch_by_id() → GameTDB / xlenore"]
    fetch -->|hit| serve
    fetch -->|miss| t5["scraper.fetch_cover()"]
    t5 -->|"libretro CDN (_name_variants)"| serve
    t5 -->|"then TheGamesDB if key set"| serve
    t5 -->|nothing| write["write .miss"] --> none
```

Entry point: `services/cover_pipeline.py:resolve(system, filename, refresh)`.
`?refresh=1` skips the cache and the `.miss` check.

Tiers 2 and 4 are **offline and exact** — they read the game itself, so they
never mis-identify. Tier 3 is exact too when the ROM is a real file: a
CRC32 the ScreenScraper database recognises identifies the game outright, which
is the only exact method available on a cartridge (no embedded icon, no serial
to read). Tier 5 is fuzzy name matching and is only reached when the game
carries no identity anywhere.

Tier 3 is also the one that can be **absent**: with no ScreenScraper account
and no LaunchBox index, `gamemedia.available()` is False and the flow is the
one that ran before it existed.

## 3b. Resolving any other media

```mermaid
flowchart TD
    start["GET /api/media/{system}/{file}/media/{type}"] --> man{"manifest cached?"}
    man -->|no| scrape["gamemedia.resolve() — one jeuInfos,<br/>downloads the cover, defers the rest"]
    man -->|yes| have{"type in the manifest?"}
    scrape --> have
    have -->|no| l404["404 + the list of types this game has"]
    have -->|"file on disk"| serve["FileResponse, immutable"]
    have -->|"deferred (URL kept, credential-free)"| fetch["fetch_media() — one download,<br/>credentials restored, 1.2 s limiter"]
    fetch --> serve
    fetch -->|failed| e502["502 — URL kept, retried next time"]
```

The manifest is scraped once per game and holds every media the sources have.
Only what is displayed is downloaded: a first request for `box-3d` costs one
HTTP call and **no ScreenScraper quota**, because the URL was already recorded.

## 4. OTA update

```mermaid
sequenceDiagram
    participant ui as UpdatePage
    participant r as routers/update.py
    participant sh as update/linux.sh
    participant gh as GitHub
    participant sd as systemd

    ui->>r: GET /api/update/check
    r->>gh: latest release
    r->>r: _version_int() — tolerant compare
    r-->>ui: {update_available, current, latest}
    ui->>r: POST /api/update/apply
    r->>sh: spawn, _pump() streams stdout
    r-->>ui: WS progress lines
    sh->>gh: download gamecore-ota.tar.gz
    sh->>sh: rsync -a --exclude .venv/ emu/ config/ emu-configs/ assets/{overlays,logos}/
    sh->>sh: rsync --delete frontend/dist/ and frontend/src/
    sh->>sh: write VERSION, pip install, clear Electron cache
    sh->>sd: systemctl start --no-block gamecore-restart.service
    Note over sh,sd: detached on purpose — the script lives in the<br/>backend's cgroup and would otherwise kill itself
```

## 5. Standby, and waking up

```mermaid
stateDiagram-v2
    [*] --> Active
    Active --> Screensaver: idle > screensaver delay
    Screensaver --> Asleep: idle > sleep delay
    Asleep --> Active: on_input() from evdev
    Screensaver --> Active: on_input()
    Active --> Active: a game is running (standby blocked)
```

`services/standby.py:run()` polls. `_enter(stage)` drives the transitions,
`_screen(False)` cuts the display over DPMS, `_governor("powersave")` drops the
CPU (optional, needs a sudoers rule). `on_input()` is called from
`gamepad_monitor`'s evdev loop — which is why a controller wakes the box even
though the UI is asleep and the browser is not listening.

Every stage change reaches the UI as a crossfade: `frontend/src/lib/standbyFade.ts`
writes the stage inside `document.startViewTransition` (500 ms, set in
`frontend/index.html`; skipped under reduced motion), so every theme's standby
fades in and out without knowing it. Going to sleep, `_enter("sleep")`
broadcasts, then waits for the TV window's `POST /api/standby/dark` (sent when the
fade to black ends) before DPMS, so the TV loses signal on a black frame. It
waits at most `_UI_DARK_WAIT_SECS` (3 s), not at all with no WebSocket client,
and does not cut the screen if a press woke the box meanwhile.

## 5b. Rest mode, and the wake

```mermaid
sequenceDiagram
    participant ui as PowerModal
    participant be as rest_mode.enter()
    participant pm as process_manager
    participant ld as logind
    ui->>ui: store.setAsleep(true): black over everything
    ui->>be: POST /api/power/rest (second press)
    be->>be: launch.close_gate(), wait for a launch in flight (15 s)
    be->>pm: kill() every session, frozen ones too
    be->>be: poll each process group; SIGKILL survivors after 8 s; 2 s more
    be->>ld: sudo -n systemctl suspend
    ld-->>be: PrepareForSleep(true): power:sleeping (any sleep path), sleep_guard sweep
    Note over ld: deep sleep; PS button wakes the box over Bluetooth
    ld-->>be: PrepareForSleep(false) (resume.py, gdbus monitor)
    be->>be: sweep again, open the gate, screen on
    be->>be: wait for a lit DRM connector (10 s max), display mode, settle delay
    be-->>ui: WS power:resumed (or GET /api/power/state after a reconnect)
    ui->>ui: store.wake(): black layer off, Home, splash from the start with its boot sound
```

- Nothing sleeps while a session is alive: every group is checked gone before
  `systemctl suspend`. A group still there after the forced kill is logged and
  the box sleeps anyway (a process in D state cannot be killed).
- **Every sleep closes the games**, not only Rest mode's: the backend holds a
  logind delay lock for `sleep` (`services/sleep_guard.py`, `systemd-inhibit
  --mode=delay … cat`, released by closing its stdin). On `PrepareForSleep(true)`
  it closes the launch gate, runs Rest mode's sweep with shorter waits (6 s,
  then SIGKILL, 2 s) and releases; logind holds the sleep up to
  `InhibitDelayMaxSec` (15 s with `/etc/systemd/logind.conf.d/90-gamecore.conf`,
  5 s without). On the wake it sweeps again (a sleep that outran the lock) and
  takes the lock back. The Rest mode button sweeps first, so the second sweep
  finds nothing and releases at once.
- The TV first: after a wake the HDMI link is back seconds before the
  picture. `power:sleeping` (and Rest mode's own second press) puts a plain
  black layer over everything; `resume.on_resume()` waits for a connector in
  `/sys/class/drm/card*-*` that is `connected`, `enabled` and `dpms` On
  (`services/display_ready.py`, 10 s at most, then it goes on anyway), then
  the player's `wake_settle_secs` (Settings → System, 0-8 s, 2.5 s default),
  and only then counts the wake and sends `power:resumed`. sysfs rather than
  xrandr: no X connection or privilege needed, and X is the slowest thing to
  come back. The sysfs state can still read "lit" from before the sleep for an
  instant, which the settle delay also covers.
- Power key: the same drop-in sets `HandlePowerKey=suspend`. In GameCore's own
  session nothing else takes the key (no PowerDevil), so a short press is a
  sleep with games closed; in a Plasma desktop session PowerDevil keeps its own
  setting. The long press is the firmware's force-off, untouched.
- Electron's `powerMonitor` `resume` (`electron/power.js`) puts the main
  window back in fullscreen and focus, tells the renderer to cover itself in
  black and forces a full repaint; everything else is the backend's.
- Wake permission: `gamecore-wake-setup` (root, boot + udev) arms the USB
  adapter and USB pads; `services/bt_wake.py` sets BlueZ `WakeAllowed=true` on
  paired pads and `false` on paired Bluetooth mice and keyboards (BlueZ allows
  HID wake by default; a moved mouse woke the box), at start, after each wake
  and whenever a new pad joins the roster. USB keyboards and the power button
  are not BlueZ's and keep waking it.
- What woke the box: `services/wake_source.py` keeps the wakeup counters
  from the sleep announcement and logs the ones that moved after the wake
  (`journalctl -u gamecore-backend | grep wake_source`). `3-3 (…)` is the
  Bluetooth adapter's USB port: a paired device paged the box.
- Pads off before any sleep: `sleep_guard.before_sleep()` runs
  `bt_wake.turn_off_pads()` next to the sweep, under the delay lock: BlueZ
  `Device1.Disconnect` on every connected paired pad (never audio, mice or
  keyboards), awaited, each call bounded by `BUSCTL_SECS`. That ends the link
  with HCI reason 0x13 "remote user terminated" (BlueZ → `MGMT_OP_DISCONNECT`
  → `hci_abort_conn(conn, HCI_ERROR_REMOTE_USER_TERM)`), which turns a pad
  off. Left to the kernel, the link ends at suspend with 0x15 "power off"
  (`hci_suspend_sync`), and an Xbox pad answers that by paging straight back,
  which woke the box at once. `WakeAllowed` stays set: the home button wakes it.

## 6. LAN authentication

```mermaid
sequenceDiagram
    participant c as browser (LAN)
    participant cad as Caddy :8443
    participant core as backend /api/auth
    participant addon as save-manager :8772

    c->>cad: GET /saves/
    cad->>core: forward_auth → GET /api/auth/verify (cookie)
    alt no / bad cookie
        core-->>cad: 302 /login?next=/saves/
        cad-->>c: login page (proxied without auth)
        c->>core: POST /api/auth/login {password}
        core->>core: blocked_for(ip)? argon2 verify
        core->>core: _set_session() → gc_session cookie
    else valid
        core-->>cad: 200 + X-GC-User
        cad->>addon: proxied request + X-GC-User
    end
```

The core's own `/api/*` is **403 from the LAN, always**. Addons contain no
auth code — Caddy is the enforcement point. The TV bypasses all of this over
loopback.

## 7. Overlay lifecycle

```mermaid
sequenceDiagram
    participant ui as React
    participant el as Electron main
    participant mon as overlay_monitor.py
    participant x as X11

    ui->>el: overlayStart(system_id)
    el->>mon: {"cmd":"watch", system_id, config}
    loop until timeout (watch_timeout_s)
        mon->>x: find_window(wm_classes) via _NET_CLIENT_LIST
    end
    mon-->>el: window:waiting … then window:ready {rect}
    el->>el: createOverlayWindow() + loadURL(/overlay?…)
    mon->>x: force_rect() — ClientMessage to leave fullscreen, then configure
    Note over mon,x: writing _NET_WM_STATE directly clears every state<br/>and desyncs the WM — always use the ClientMessage
    mon-->>el: window:closed (emulator exited)
    el->>el: destroyOverlayWindow()
```

The whole feature is disabled when `WAYLAND_DISPLAY` is set
(`_WAYLAND_SESSION`), silently. A dev machine on Wayland will never reproduce
an overlay bug.
