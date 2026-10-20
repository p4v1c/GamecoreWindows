# 1 — Runtime topology

What runs, where it listens, who starts it, and in what order.

## The three processes

| Process | Started by | Listens | Owns |
|---|---|---|---|
| **Backend** | `gamecore-backend.service` (system unit); on Windows a logon task in the console session ([below](#on-windows-the-backend-runs-in-the-console-users-session)) | `127.0.0.1:8765` | the emulator process, playtime DB, covers, standby, evdev |
| **Electron** | `gamecore-ui.service` (system unit) | — | the kiosk window, HUD toasts |
| **The emulator** | Backend, `platform/jobs.py` `spawn()` | — | its own Job Object (the whole process tree) |

Plus, out of band:

| Process | Unit | Listens |
|---|---|---|
| rom-manager | `gamecore-addon-rom-manager.service` (**user** unit) | `127.0.0.1:8770` |
| rpcs3-manager | `gamecore-addon-rpcs3-manager.service` | `127.0.0.1:8771` |
| save-manager | `gamecore-addon-save-manager.service` | `127.0.0.1:8772` |
| bios-manager | `gamecore-addon-bios-manager.service` | `127.0.0.1:8773` |
| Caddy | `caddy.service` | `:8443` — **the only LAN-facing port** |

Ports 8770-8799 are reserved for addons. Vite (`:5173`) exists in development
only.

## The shape of it, including the sandbox boundary

```mermaid
flowchart TB
    tv["TV — physical access<br/><i>this is the trust boundary</i>"]
    lan["LAN client (phone, PC)"]

    subgraph box["the box"]
        subgraph host["host processes — all bound to 127.0.0.1"]
            be["<b>backend</b> :8765<br/>gamecore-backend.service<br/>REST · WebSocket · serves frontend/dist"]
            el["<b>Electron</b><br/>gamecore-ui.service<br/>kiosk window · HUD"]
            addons["addons :8770-8773<br/><i>systemd user units</i>"]
            caddy["<b>Caddy</b> :8443<br/>TLS + shared password<br/><i>the only LAN-facing port</i>"]
        end

        subgraph sandbox["Flatpak sandbox — a different filesystem view"]
            emu["<b>the emulator</b><br/>own process group"]
        end
    end

    el -->|"http://localhost:8765"| be
    el --> tv
    be -->|"spawn · killpg"| emu
    be -.->|"WebSocket events"| el
    addons -->|"POST /api/addons/notify"| be
    lan -->|https| caddy
    caddy -->|forward_auth| be
    caddy --> addons
    caddy -. "/api/* → 403" .-> be

    style sandbox stroke-dasharray: 6 4,stroke:#b3261e,stroke-width:2px
    style caddy stroke-width:2px
```

**The dashed box is the part that catches people out.** The emulator does not
share the backend's filesystem view: it sees only what its Flatpak permissions
grant. That is why the installer adds `--filesystem` for the ROM directory and
`--device=all` for controllers per app, why a config written to
`~/.var/app/<id>/…` is the emulator's real config and one written elsewhere is
not, and why `flatpak kill <app-id>` has to come before any signal — a signal to
the wrapper never reaches inside.

**Everything except Caddy binds `127.0.0.1`.** The TV reaches the backend over
loopback with no authentication at all: physical access *is* the trust boundary.
The LAN only ever sees Caddy, and `/api/*` is 403 through it. If you add an
endpoint, assume the LAN can never call it — and see [9](09-gotchas.md) for why
"not LAN-exposed" still is not "not reachable" on a box that runs browsers.

## Boot sequence

```mermaid
sequenceDiagram
    participant sd as systemd
    participant be as backend
    participant el as Electron
    participant ui as React

    sd->>be: start gamecore-backend.service
    be->>be: lifespan() → init_db()
    be->>be: create_task ×4<br/>gamepad_monitor · battery · standby · prefetch
    Note over be: uvicorn now serving 127.0.0.1:8765
    sd->>el: start gamecore-ui.service (after display-manager)
    el->>el: backendAlive()? else startBackend()
    el->>el: createWindow() → loadURL(localhost:8765)
    be-->>ui: frontend/dist (SPA)
    ui->>be: WS /ws  +  GET /api/systems, /api/sysinfo…
    Note over ui: Splash plays while data loads,<br/>then Home is already populated
```

`backend/main.py:lifespan()` is the single place background tasks are created
and cancelled:

```python
monitor_task  = asyncio.create_task(gamepad_monitor.run())
battery_task  = asyncio.create_task(battery.run())
standby_task  = asyncio.create_task(standby.run())
prefetch_task = asyncio.create_task(prefetch.run())
```

Electron's `startBackend()` is a **fallback for development only**: on a
managed box systemd owns the backend, and Electron never competes for the port.
"Managed" is read from `INVOCATION_ID`, which systemd sets in every service it
runs — `start-ui.sh` is the unit's `ExecStart`, so Electron inherits it.
`GAMECORE_MANAGED` overrides it either way.

## When the interface appears

Nothing is timed. The shell waits on `GET /api/ready` — 200 once the backend's
required startup is done, 503 with the outstanding step until then — and the
interface then tells the shell it is worth looking at through `boot:ready`
(`electron/preload.js`), decided in the host by `frontend/src/lib/boot.ts`:
the theme resolved, the dashboard's own data settled, "Who's using this
controller?" decided (not asked, or drawn under the splash), and a frame painted.

The states are `STARTING → WAITING_BACKEND → LOADING_UI → PRESENTABLE →
RUNNING`, plus `RECOVERING`. Durations appear in exactly one role: bounding a
failure so it can be reported. Nothing is promoted by a clock.

A fixed 4 s hold used to be appended to the URL whenever the machine had booted
recently (`splashHoldMs()`), so the boot animation would not play against a
screen the TV had not finished re-syncing. The reason was real; the mechanism
was a duration measured on one box and paid by all of them. The splash now
holds its LAST frame until the host says the interface is ready — see
`docs/themes/README.md` §8.

## Who talks to whom

```mermaid
flowchart LR
    ui["React UI"]
    pre["preload.js<br/>window.gamecore"]
    main["Electron main"]
    api["Backend /api"]
    ws["Backend /ws"]
    emu["emulator"]
    ev["/dev/input/event*"]

    ui -->|fetch| api
    ws -->|broadcast| ui
    ui -->|contextBridge IPC| pre --> main
    api -->|spawn| emu
    ev -->|evdev| api
    api -->|"POST /api/games/kill"| emu
```

Three separate channels, deliberately:

1. **HTTP `/api`** — request/response, UI-initiated.
2. **WebSocket `/ws`** — backend-initiated push (game started/finished,
   battery, standby, addon notifications).
3. **IPC over `contextBridge`** — UI → Electron only, for things the browser
   cannot do (reboot, shut down, hide the window while a game plays).
   `preload.js` exposes a fixed set of methods and nothing else; `nodeIntegration` is off.

## Systemd units

`gamecore-backend.service` — system unit, written by `install/arch.sh`:

```ini
Environment=GAMECORE_PATH=/opt/GameCore
ExecStart=/opt/GameCore/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

The TheGamesDB key is added as a local drop-in
(`systemctl edit gamecore-backend` → `Environment=THEGAMESDB_API_KEY=…`), never
committed.

On a box with a separate data root — what the ISO produces — the installer adds
a second drop-in, `10-data.conf`, with `Environment=GAMECORE_DATA=/userdata`.
That one variable is the split: `GAMECORE_PATH` is code (root-owned, replaced
wholesale by an OTA), `GAMECORE_DATA` is everything the player accumulates
(`config/`, `emu/`, `assets/logos/`, `addons/`). When the
drop-in is absent, `backend/services/paths.py` falls back to `GAMECORE_PATH` — the
pre-split single-root layout every older box still runs. The unit drop-in is
the **source of truth** for the data root: `gamecore-ui.service` gets the same
`Environment=` line, and the three consumers that cannot inherit it — the
updater, the `gamecore-addon` CLI, addon units — each read it back from
`systemctl cat gamecore-backend` rather than guessing
([13-release-and-ota.md](13-release-and-ota.md) shows the exact mechanism).
The Flatpak sandbox is the seventh consumer, and the one that fails silently:
each emulator's override must list the data root
(`flatpak override --user --filesystem=/userdata`), or every ROM under it is
"not found" from inside the sandbox while every path check on the host passes
([09-gotchas.md](09-gotchas.md)).

`gamecore-ui.service` — starts `electron/start-ui.sh` after the display
manager. That wrapper is not decoration: it exports `XDG_RUNTIME_DIR` (without
it Chromium has **no audio at all** under a systemd service) and probes
`:1`, `:0`, `:2` with `xdpyinfo` to find the live X display, plus the matching
`XAUTHORITY` cookie under `/run/user/<uid>/`.

Addons are **user** units (`systemctl --user`), so they inherit the graphical
session and stop with it.

`gamecore-wake.service` — oneshot at boot, runs `/usr/local/bin/gamecore-wake-setup`
(source `install/bin/gamecore-wake-setup`): enables USB `power/wakeup` on the
Bluetooth adapter and on USB pads, so Rest mode can be woken by a pad. The
attribute resets at every boot and replug; `/etc/udev/rules.d/90-gamecore-wake.rules`
(source `install/system/90-gamecore-wake.rules`) re-runs the helper for a pad or
adapter plugged in later. `/etc/systemd/logind.conf.d/90-gamecore.conf` (source
`install/system/logind-gamecore.conf`): power key short press = suspend, and
`InhibitDelayMaxSec=15` for the backend's sleep lock. Changed only by a logind
**reload** where logind supports it; never a restart, which would end the
session. Installed by `install/steps/setup-rest-mode.sh`, see
[Rest mode](02-request-flows.md#5b-rest-mode-and-the-wake).

## The session the kiosk runs in

### On Windows: the backend runs in the console user's session

A window opens on the desktop of the session its process belongs to, and only
the active console session is on the TV. A Windows service lives in session 0,
which has no visible desktop, so the backend is **not** a service: it runs in
the console user's session, started at logon by a scheduled task with highest
privileges, restarted by a watchdog (the task and the watchdog are step 7;
dev-log D8). A game it starts inherits that desktop; nothing is rebuilt.

`session.launch_blocker()` (`backend/services/session.py`, over
`backend/services/platform/console_session.py`: `ProcessIdToSessionId`,
`WTSGetActiveConsoleSessionId`, `WTSQuerySessionInformationW`) refuses a
launch when that is not true: nobody logged on to the console, or the backend
in another session (started as a service, or the console taken over by Remote
Desktop). `session.desktop()` names the session, the user and the primary
display (`EnumDisplayDevicesW`) for logs. The display is not checked: a TV
that is off still lists its output.

### On Linux (until step 7)

SDDM auto-logs the gaming user into **the machine's own X11 desktop session**,
and `gamecore-ui.service` draws the kiosk over it. The whole stack is X11-only —
the (since removed) bezel overlays, the fullscreen enforcer, `gamecore-xsetup`'s 1080p pin and
the gamepad→keyboard bridge's window detection all speak X11 — so a Wayland
session is not a supported target.

The session name is **not hardcoded anywhere**. `install/bin/gamecore-session-select
pick-desktop --x11` ranks what `/usr/share/xsessions/` offers (Plasma first,
then the other full desktops), `install/arch.sh` writes the winner into
`/etc/sddm.conf.d/zz-gamecore-autologin.conf` and records it in
`/var/lib/gamecore/manifest.env` as `KIOSK_SESSION`. `check-install.sh` reads it
back from there rather than comparing to a literal.

> The kiosk used to get a bare openbox session installed for the purpose. That
> was the wrong half of the requirement: GameCore needs X11, not an empty
> session. Hosting it on the desktop costs nothing and means **closing the kiosk
> reveals a usable desktop** — under openbox it revealed a root window with no
> panel and no menu, reported as a black screen.

`zz-` in that filename is load-bearing. SDDM reads `/etc/sddm.conf.d/*` in name
order and the **last** `[Autologin]` wins; Manjaro Plasma ships
`kde_settings.conf`, and `k` sorts before `z`. The installer also strips the
competing `User`/`Session`/`Relogin` keys out of that file (keeping a backup in
the manifest directory), because the Login Screen KCM rewrites it whenever
someone opens it.

The console session (`install/bin/gamecore-session`) hands `kwin_x11` a config
directory of its own, `$XDG_RUNTIME_DIR/gamecore/wm`: no window-manager key
bindings, and none of KWin's window animations (`fade`, `glide`, `scale`,
`fullscreen`). The theme draws the handovers; KWin's zoomed and faded the
interface in over them on every suspend, because the kiosk window is unmapped
while a game runs.

`sudo gamecore-session-select desktop` / `… gamecore` toggles the kiosk. There is
only one session, so it changes **no SDDM configuration at all** — it is
`systemctl enable|disable --now gamecore-ui.service` behind an argument-narrow
sudoers rule. `enable`/`disable`, never `start`/`stop`: the unit is enabled at
install, so merely stopping it brought the kiosk back over the desktop at the
next boot.

## Environment reconstruction

On Windows the backend already runs in the console user's session, so a game
gets the backend's own environment. `session.display_env()`
(`backend/services/session.py`) adds one variable:
`SDL_GAMECONTROLLERCONFIG_FILE`, the served controller database
(`configgen/mapping_db.py`). `electron/start-ui.sh` (the UI) is replaced at
step 4.

## Development topology

```bash
uvicorn backend.main:app --port 8765            # backend (no --reload on Windows, see below)
cd frontend && npm run dev                      # Vite :5173
cd electron && ELECTRON_DEV=1 npx electron .    # shell → :5173
```

On Windows, `--reload` and `--workers` make uvicorn run the Selector event
loop, which cannot start subprocesses (`NotImplementedError` from
`asyncio.create_subprocess_exec`, used by the update router and Rest mode);
without them it runs the Proactor loop (`test_event_loop.py`). Games do not
depend on it: `platform/jobs.py` starts them with `subprocess.Popen`.

`DEBUG` exists twice — `backend/config.py` and `electron/main.js:16` — and
must be `false` on a device. `DEV` is `DEBUG && ELECTRON_DEV=1`; only then does
Electron load `:5173` instead of `:8765`.
