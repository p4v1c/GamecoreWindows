# 3 — Backend, routers

Every HTTP surface, file by file. Line numbers are indicative — the function
names are the contract.

All routers are mounted with `prefix="/api"` in `backend/main.py:41-55`.
Routers parse, validate and delegate; the logic lives in
[services](04-backend-services.md).

## Wiring — `main.py`

| Symbol | What it does |
|---|---|
| `lifespan(app)` | wakes the screen, adopts an orphaned game, creates the four background tasks; on shutdown cancels them and **awaits** them |
| `cross_origin_guard` | HTTP middleware — 403 on a non-GET write driven from another origin |
| `_origin_ok(headers)` | the rule itself, shared with `/ws` |
| `gc_addons()` | `GET /gc/addons` — same payload as `/api/addons`, on a path Caddy proxies **without auth** (the addon nav bar needs it pre-login) |
| `login_page()` | `GET /login` — self-contained login form for LAN clients |
| `websocket_endpoint(websocket)` | `WS /ws` — checks `Origin`, accepts, then reads forever; every send is a broadcast from `ws.py` |

### The lifespan does three things before starting the tasks

1. **`standby.resume_after_restart()`** — forces the screen back on
   unconditionally. Standby state is a module variable but its effect is not:
   `xset dpms force off` belongs to the X server, which SDDM owns and which does
   not restart with the backend. A box asleep when the backend restarted came
   back believing it was awake with the TV dark, and nothing could wake it (pad
   events arrive over evdev, not X, so DPMS never re-armed). Restarting the
   backend is what a stuck user will try — so that is what now fixes it.
2. **`process_manager.adopt_orphan()`** — re-attaches to a game a previous
   process left running, so the double-PS shortcut can still close it. See
   `config/session.json` in doc 7.
3. On shutdown, the four tasks are cancelled **and awaited**
   (`asyncio.gather(..., return_exceptions=True)`). `cancel()` alone only
   schedules the cancellation, so shutdown used to return with tasks still
   mid-await. The running game is deliberately left alone.

### The cross-origin guard

The core has no auth of its own and the box runs browsers that can reach it. A
page in the Firefox kiosk or in Stremio could auto-submit a form at
`http://127.0.0.1:8765/api/games/kill` and kill the running game. Non-GET
requests are refused when `Origin` names somewhere we are not serving, or when
`Sec-Fetch-Site` is `cross-site`.

It is **same-origin against the forwarded `Host`**, not a localhost allowlist:
`/login` and `/api/auth/*` arrive through Caddy from an address nobody can
predict, so an allowlist would have 403'd every LAN login. `localhost` and
`127.0.0.1` are additionally accepted as the same machine *on the backend's own
port* — Electron says one where the socket reports the other — but another local
app on another port is not the UI. A request with no `Origin` passes: curl and
the install scripts have none, and browsers always send one on a cross-origin
write. Full rationale in `docs/SECURITY.md`.

`/ws` needs the same check for a different reason: a WebSocket handshake is a GET
and is not subject to CORS **at all**, so any page could otherwise open
`ws://127.0.0.1:8765/ws` and read every event the UI sees.

Static mounts, in order: `/covers`, `/assets/logos`,
`/data`, `/themes`, then `/` → `frontend/dist` with `html=True`. The loop
`mkdir`s each directory first — a conditional mount decided at import time used
to leave `/covers` dead until a restart on a fresh checkout.

`/themes` is mounted through `_NoCacheStatic`, a `StaticFiles` subclass that
sets `Cache-Control: no-store` and refuses 304s. A theme is a folder of ES
modules the browser imports directly: the loader can bust the entry point's URL,
but the entry's own relative imports and its stylesheet resolve without that
query, so the browser would pin the first version it ever saw. Editing a theme
would then change nothing on screen, and a fix shipped by update could stay
invisible.

> `@app.websocket("/ws")` **must** stay declared before the `/` mount. The SPA
> catch-all would otherwise swallow the upgrade request.

## `systems.py` — the catalogue

The listing itself lives in `backend/services/systems.py` (`get_systems`,
`get_apps`, `list_all`, `find`): services need it too, and a service must not
import a router.

| Function | Route | Notes |
|---|---|---|
| `list_systems()` | `GET /systems` | `services.systems.list_all()` — rows re-read when the file's mtime changes — each with `art`: `{name: url}` of the pack's pictures |
| `get_system(system_id)` | `GET /systems/{id}` | `services.systems.find()`, else 404; same `art` field |
| `get_system_art(system_id, name)` | `GET /systems/{id}/art/{name}` | a pack picture, `assets/art/<id>/` first, then `catalog/<id>/art/` (`services/pack_art.py`); 404 on an unknown name |
| `serve_logo(filename)` | `GET /assets/logos/{filename}` | |

## `games.py` — scanning and launching

| Function | Route | Notes |
|---|---|---|
| `scan_roms(roms_path, extensions, scan_dirs, system_id)` | — | wraps `rom_scanner.iter_rom_files`; skips vanished files instead of 500-ing; for `scan_dirs` systems, prefers the title from `local_media.get_title()` over the folder name (a PS3 folder is often just `BLES01234`) |
| `list_games(system_id)` | `GET /systems/{id}/games` | returns `[]` for apps |
| `launch_game(req)` | `POST /games/launch` | 404 unknown system, then `services.launch.launch()`; its `LaunchRefused(status, detail)` becomes the HTTP error: 409 foreground session or a different suspended game · resumes the same suspended game · **403 if the ROM path resolves outside the system's `romsPath`** · 424 catalogue/BIOS gate · 503 emulator missing |
| `kill_game(req?)` | `POST /games/kill` | optional session number; otherwise the foreground or latest suspended session |
| `background_game()` | `POST /games/background` | SIGSTOPs the foreground process group and returns the complete session state |
| `foreground_game(req?)` | `POST /games/foreground` | SIGCONTs the named/latest suspended session |
| `get_session()` | `GET /games/session` | flat foreground fields plus `background[]` |

The path check (`services/launch.py:_check_rom_path`) is the security-critical line:

```python
Path(rom_path).resolve().relative_to(roms_root.resolve())
```

Without it, a crafted `rom_path` turns `/api/games/launch` into "run any
binary on the box".

The session invariant is one resident game, enforced both before launch-side
preparation and again immediately before process creation. A matching suspended
`(system_id, game_key)` is resumed; a different game gets a 409 that names the
held title. Application tiles remain independent and may coexist.

## `covers.py` / `metadata.py`

| Function | Route |
|---|---|
| `get_cover(system_id, filename, refresh=False)` | `GET /covers/{system}/{file:path}` → `cover_pipeline.resolve()` |
| `get_metadata(system_id, filename)` | `GET /metadata/{system}/{file:path}` → `metadata.resolve()` |

`{filename:path}` (not `{filename}`) because ROM names contain slashes for
folder-based games.

## `media.py` — every artwork, not just the jacket

| Function | Route |
|---|---|
| `list_media(system_id, filename, refresh=False)` | `GET /media/{system}/{file:path}` → the catalogue + metadata |
| `get_media(system_id, filename, media_type)` | `GET /media/{system}/{file:path}/media/{type}` → one file |

`/covers` answers *"give me a cover"* and answers it exactly as it always has.
This router answers *"what does this game have?"* — `box-3d`, `clear-logo`,
`screenshot-gameplay`, `mix-rbv2`, `video`, `manual`, 54 types in all — so a
theme can be built on something other than a flat box front. Backed by
[`services/gamemedia`](04-backend-services.md#gamemedia--screenscraper--launchbox).

The catalogue is what makes it usable. Each entry carries `category`
(`box`, `cart`, `logo`, `screenshot`, `mix`, `marquee`, `artwork`, `icon`,
`bezel`, `video`, `document`, `theme`, `pinball`), `kind` (`image`, `video`,
`document`, `archive`) and `cached`. Without them a theme would have to
recognise 54 type names by hand to know which one is a box.

Three answers a theme has to tell apart, and the reason the JSON is shaped this
way rather than as a bare 404:

| Response | Meaning |
|---|---|
| `available: false` | no ScreenScraper account and no LaunchBox index on this box. Nothing is wrong with the game |
| `found: false, unreachable: true` | quota spent, or network down. Retrying later is worth something |
| `found: false, unreachable: false` | the sources answered and do not have this game. Final |

**Nothing is downloaded before it is asked for.** A scrape fetches the cover
and records the other ~27 media with their URL; the first request for a 3D box
costs one HTTP call and no ScreenScraper quota, every request after it costs a
`stat()`. Fetching everything up front would be ~34 s per game at the 1.2 s
ScreenScraper requires between calls.

A 404 on the file route carries the list of types the game *does* have, so a
theme never has to guess twice.

## `playtime.py`

`get_all_playtime()`, `get_system_playtime(system_id)`,
`get_game_playtime(game_key:path)` — reads of the active profile's playtime
(`services/playtime_rows.py`): the `playtime` table for the primary profile,
its `profile_playtime` rows for another
([schema](07-config-and-data.md#playtimedb)).

## `addons.py` — registry and lifecycle

| Function | Route | Notes |
|---|---|---|
| `_cli()` | — | locates the `gamecore-addon` binary |
| `_registry()` | — | reads `config/addons.json` |
| `list_installed()` | `GET /addons` | consumed by the TV **and** by every addon's nav bar |
| `list_available()` | `GET /addons/available` | runs `gamecore-addon list --json`; may clone the repo |
| `notify(body)` | `POST /addons/notify` | generic hook: an addon pushes an event onto the core WebSocket |
| `_run_cli(action, name)` / `_pump()` | — | runs the CLI, streams its output over the WS |
| `_start(action, name)` | — | guards against two concurrent CLI runs |
| `install_addon` / `update_addon` / `remove_addon` | `POST /{name}/install`, `POST /{name}/update`, `DELETE /{name}` | |

The core never touches addon files itself — it shells out to the CLI. That is
why the registry stays consistent whoever ran the command.

> `/api/addons/notify` is reachable from any addon and its payload ends up in
> HUD toast HTML. See [gotchas](09-gotchas.md#untrusted-strings-reach-the-hud).

## `update.py` — OTA

| Function | Route | Notes |
|---|---|---|
| `_version_int(tag)` | — | tolerant `x.y.z` ordering — `v2.1.0-rc1` or a malformed tag must never crash the check |
| `check_update()` | `GET /update/check` | queries the GitHub releases API |
| `update_status()` | `GET /update/status` | `{running: bool}` — the backend is the source of truth |
| `apply_update()` | `POST /update/apply` | spawns `update/linux.sh` in the background; **409** if one is already running |
| `_run_update()` / `_pump()` | — | streams stdout line by line over the WebSocket, which is what the settings page renders live |

Same busy check as `addons.py`: a module-level task handle, tested and assigned
with no `await` in between, which is what makes it atomic. It is needed because
`update/linux.sh` wipes its work directory on entry and rsyncs from it into
`GAMECORE_PATH` — a second run did `rm -rf` underneath the first one's rsync.
And it was easy to trigger: `installing` was `UpdatePage`'s own component state,
so leaving the page and coming back re-mounted it as false and re-enabled the
button, during minutes in which the screen does not change. `UpdatePage` now
polls `/update/status` instead of trusting itself, and treats a 409 as "keep
following the running update".

The script is spawned with `start_new_session=True` and the 10-minute timeout
kills the **process group**, through the same helper `process_manager` uses to
kill a game. Killing only `bash` left its `rsync`, `pip` and `npm` writing into
`/opt/GameCore` after the UI had been told the update was aborted.

## `themes.py` — the theme catalogue

| Route | What it does |
|---|---|
| `GET /api/themes` | `{ sdk_version, active, themes[] }` — one validated manifest per folder in `config/themes/` |
| `POST /api/themes/active` | `{ id }` or `{ id: null }` for the default; persists to `config/theme.json` and becomes the active profile's theme (`profiles.remember_theme`) |

`POST` refuses an incompatible theme with a reason rather than storing it — an
incomplete theme would otherwise be selectable, fail to load, and leave the
player on the default UI wondering why their choice did nothing.

## `sysinfo.py`

`_primary_ip()` + `get_sysinfo()` → `GET /sysinfo`: IP, storage used/total/free,
`APP_VERSION`, and `controller_registry.snapshot()` (the P1…P4 slots with
battery). The TopBar and the controller screen both read it.

## `standby.py`

`get_standby()` (state + config), `set_config(cfg)` (`StandbyConfig` model,
persisted to `config/standby.json`), `wake()` → `standby.exit_standby()`,
`ui_dark()` (`POST /standby/dark`) → `standby.ui_dark()`: the UI has faded to
black, the sleep stage may switch the screen off.

`GET /standby/videos` → `standby_videos.playlist()`: what the CRT standby
plays. `{videos: [...], stills: [...]}`, each entry
`{system_id, filename, display_name, system_name, last_played, media_type, url}`
where `url` is the `/api/media/.../media/<type>` file route. Only files already
on disk; `videos` are clips, `stills` screenshots of games without one (at most
40). Weighted shuffle: played games come up more. `?favourite=<system>:<file>`
(repeatable) doubles a game's weight; favourites live in the theme.

## `power.py`

`POST /power/rest` → `rest_mode.enter()`: closes every session, then suspends.
Answers `{ok, forced}` after the wake (`systemctl suspend` waits for its job),
409 while already going to sleep, 503 when the suspend is refused (no sudoers
rule). `GET /power/state` → `{resting, resumes, sleeping}`; `resumes` grows by
one per wake, counted once the TV shows a picture (`resume.resume_count()`);
`sleeping` is true from the sleep announcement until then.

## `controllers.py`

`GET /controllers/devices` (declared non-SDL peripherals), `GET /controllers/pads`
(the connected pads for the controller screen, `controller_roster.py`), `GET`/`POST
/controllers/autoconfig` (the global switch and per-emulator exceptions), and the
mapping wizard: `POST /controllers/mapping/{start,commit,cancel,forget}`, `GET
/controllers/mapping/saved` and the `/ws/controllers/mapping` socket. See
[8](08-controller-pipeline.md).

`POST`/`DELETE /controllers/scan-mapping` ("Scan mapping" / "Forget mapping")
were removed and answer 404. Snapshots they saved are still restored on connect.

## `auth.py` — shared-password login

| Function | Route | Notes |
|---|---|---|
| `_client_ip(request)` | — | reads `X-Forwarded-For` (the request always arrives via Caddy) |
| `_set_session(resp)` | — | `gc_session` cookie: HttpOnly, Secure, SameSite=Lax, 30 days |
| `login(request)` | `POST /auth/login` | rate-limited by `auth.blocked_for(ip)` |
| `verify(request)` | `GET /auth/verify` | **the `forward_auth` endpoint.** 200 → Caddy passes the request through and copies `X-GC-User`; 302 → login page; 401 |
| `tls_ask(domain)` | `GET /auth/tls-ask` | **Caddy's `on_demand_tls` gate.** 200 approves minting a certificate |
| `logout()` | `POST /auth/logout` | |
| `change_password(request)` | `POST /auth/change-password` | bumps `generation` → every existing session dies |

`verify_password` is argon2id with the library defaults — 64 MiB and real CPU
time, deliberately — so `login` and `change_password` call it through
`asyncio.to_thread`. Inline, a burst of failed logins from the LAN froze the TV,
which talks to this same process.

`tls_ask` approves loopback, this machine's addresses on any interface, its
hostname, and any name that resolves to one of those (which is what keeps
MagicDNS working). The gate used to be Caddy's own admin API, which answers 200
to anything — so any LAN client could make the box mint certificates without
limit.

## `settings/` — the OS wrappers

Each module wraps a CLI and is careful about the environment, because a
systemd service has no session bus. All three define a local `_session_env()`
and an async `_run(*args)`.

### `settings/display.py` — resolution, refresh rate, UI scale

Thin: six endpoints over `backend/services/display.py`, which picks
`kscreen-doctor` (Wayland) or `xrandr`, arms the revert timer and writes
`config/display.json` / `config/ui-scale.json`. `DisplayError(status, detail)`
becomes the HTTP error.

| Route | Notes |
|---|---|
| `GET /settings/display` | modes, current, backend, `pending`, `revert_secs` |
| `POST /settings/display/mode` | 409 while a game is resident · 400 mode not advertised · arms the revert timer |
| `POST /settings/display/confirm` | keeps the mode and persists it — the only writer of `display.json` |
| `POST /settings/display/revert` | goes back now |
| `GET/POST /settings/display/scale` | one of `SCALES`; applied by the front end as a zoom |

### `settings/wifi.py` — WinRT and WMI

| Function | Route |
|---|---|
| `scan_networks()` | `GET /wifi/networks` — rescans; one entry per SSID (`best_per_ssid`: connected first, then strongest) |
| `wifi_details()` | `GET /wifi/details` — security, channel, band per SSID (`details_per_ssid`, strongest access point); `rate` is always `""` (WinRT reports none) |
| `wifi_status()` | `GET /wifi/status` — SSID, IP **and wired status** (`ethernet_status`: first connected physical wired adapter), so the UI can skip the Wi-Fi flow on ethernet; `radio` true/false (null: no Wi-Fi hardware); gateway, DNS, MAC of the Wi-Fi adapter, same keys when disconnected |
| `set_radio(req)` | `POST /wifi/radio` `{enabled}` → `{enabled}` read back; logic in [`wifi_radio.py`](04-backend-services.md#wifi_radiopy--the-wi-fi-radio-switch) |
| `connect_wifi(req)` | `POST /wifi/connect` — returns `wrong_password` distinctly (`INVALID_CREDENTIAL`); bounded by `CONNECT_TIMEOUT` |
| `disconnect_wifi()` | `DELETE /wifi/connect` |

The calls are in [`platform/wifi.py` and `platform/network.py`](04-backend-services.md#platform--every-call-into-windows).
A connect with a password deletes the saved profile named after the SSID
first and again after a failure (`_forget`): a profile holding an old or
refused key would be retried instead of the one just typed. The SSID and the
password travel to WinRT as values; no command line is built from them.

### `settings/audio.py` — Core Audio

`get_audio()`, `list_sinks()`, `set_volume(req)`, `set_sink(req)`. The calls are
in [`platform/audio.py`](04-backend-services.md#platform--every-call-into-windows);
a sink `id` is the Windows endpoint id string. A Core Audio refusal answers an
empty list, volume 100, or `{ok: false, error}`: the page shows what it has.

### `settings/bluetooth.py` — `bluetoothctl`

`list_devices()`, `start_scan()` (scans 10 s, answers what is in range and not
paired), `pair_device`, `connect_device`, `disconnect_device`, `remove_device(mac)`.

> Pairing runs one *interactive* `bluetoothctl --agent=NoInputNoOutput`
> (`_pair_with_agent`): in direct subcommand mode (`-- pair`) bluetoothctl 5.87
> registers no agent, and bluetoothd refused every device that needed a
> confirmation or an authorization ("No agent available"). A `(yes/no)`
> prompt is answered yes: the player picked the device.

> Bluetooth device names are attacker-controlled strings that reach the UI.
> They are one of the reasons `escHtml()` exists in Electron.

---

## Routers added by the recent phases

These four arrived with features that did not exist when the inventory above was
written. Each is listed with what it serves and the decision that is not visible
from the endpoint list.

### `catalog.py` — installing while the box is running

`GET /catalog`, `POST /catalog/{pack_id}/install`, `POST /{pack_id}/remove`,
`POST /{pack_id}/reconfigure`, `GET /catalog/busy`, `GET /catalog/ota/status`,
`POST /catalog/ota/refresh`.

`busy` exists because installing a Flatpak is minutes long and the player is
holding a gamepad: the screen has to be able to say "already working" rather than
queue a second install behind the first. The `ota/*` pair drives the signed
catalogue channel — see [10](10-catalog-and-install.md#three-tiers-and-the-signed-remote-one).

### `bios.py` — one row per system that needs a system file

`GET /bios`. Thin on purpose; the verdicts come from
[`services/bios.py`](04-backend-services.md#biospy-253-l--three-verdicts-not-two).
Every row carries `installed`; Settings → BIOS lists only the installed ones,
as one row each with the file detail of the selected one beside it
(`frontend/src/settings/bios.js`).

### `pergame.py` — per-game settings from the sofa

`GET /pergame/{system_id}`, `POST /pergame/{system_id}/profile`,
`POST /pergame/{system_id}/open`.

`open` launches the emulator's **own** settings window. That is the deliberate
half of the design: GameCore never translates a setting's meaning across thirteen
emulators, so the escape hatch is opening the real UI rather than approximating
it. See [10](10-catalog-and-install.md#pergame--and-why-it-is-required-on-every-emulator-pack).

### `logs.py` — the logs directory

`GET /logs` → `{files, bytes}`; `DELETE /logs` → `{ok, freed}`, every file under
`<DATA>/logs/`. `POST /logs/ui` `{message, source}` (2000 / 300 chars max) → an
error line in `logs/ui/ui.log`. Logic in `services/logs.py`.

### `profiles.py` — who plays on this box

| Route | Does |
|---|---|
| `GET /profiles` | `{active, auto_login, profiles, palette, separate_saves, shared_saves}`; the first call creates the primary profile. `separate_saves`: labels of the systems whose saves follow the profile; `shared_saves`: the emulators whose saves every profile shares (`profile_saves`) |
| `POST /profiles` `{name, color?, avatar?}` | a new profile; 400 on a bad name, colour or avatar, 409 on a name taken or while the primary profile has no name |
| `PATCH /profiles/{id}` `{name?, color?, avatar?, theme?}` | rename, recolour, picture, theme (null: the built-in look; 400 for one that cannot load); the id never changes. A new theme on the active profile is put on now (`theme:changed`) |
| `POST /profiles/{id}/controllers` `{id}` | show a connected pad (`id` from `GET /controllers/pads`) on this profile, and off any other; 404 when the pad is not connected now. Display only |
| `DELETE /profiles/{id}/controllers/{controller_id}` | take it off the profile, connected or not; 404 when it is not there |
| `DELETE /profiles/{id}` | the record only, saves stay on disk → `{active}`; 409 on the last profile |
| `PUT /profiles/auto-login` `{enabled}` | "Log in automatically": start as the last profile without asking who is playing → `{auto_login}` |
| `GET /profiles/active`, `PUT /profiles/active` `{id}` | the profile the interface is used as; 409 while a game is on screen or suspended. A switch puts on the theme the profile wore last (`theme:changed`) |

Every write broadcasts `profiles:changed` `{active}`; a change of active profile also broadcasts `playtime:rekeyed`, so whatever lists playtime and recents reloads them for the new profile. Logic in `services/profiles.py`.

### `storage.py` — external disks

`GET /storage/volumes`, `POST /storage/mount`, `POST /storage/unmount`.
