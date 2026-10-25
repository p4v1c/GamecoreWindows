# 5 — Frontend

React 18 + Vite + Zustand + Framer Motion. The typeface (Source Sans 3) ships
in the bundle via `src/fonts.css`; nothing is fetched from the internet, and
`src/DESIGN.md` records the built-in look. **No CSS files in React
components** — styling is inline style objects next to the markup. Two
exceptions own their markup and ship a stylesheet: a *theme*
(`docs/themes/README.md`) and the shared settings screen
(`frontend/src/settings/`, SDK idiom, `settings.css`, `gcs-*` classes).

```
src/
  main.tsx          createRoot
  App.tsx           the kernel — see below
  store/index.ts    Zustand store
  api/index.ts      typed fetch wrappers, BASE = "/api"
  hooks/
    useGamepad.ts   Gamepad API → CustomEvents + live state
    useWebSocket.ts backend push → handler registry
    useTheme.ts     loads the active theme, crash counting, L1+R1 rescue
  lib/
    themeLoader.ts  imports a theme module, validates its surfaces
    themeSdk.ts     the object a theme receives — the whole contract
  settings/         the shared settings screen (SDK idiom, plain JS + index.d.ts)
  components/…
```

## Kernel, shell, views

Picking a theme swaps the frontend. Three layers, and the boundaries are what
keep a theme from breaking the launcher:

| Layer | File | What lives there |
|---|---|---|
| **Kernel** | `App.tsx` | the input bus, the WebSocket, `gp:guide` (reserved with `gp:menu` and `gp:power`, [themes §6a](../themes/README.md#6a-the-hosts-buttons-and-the-themes)), the session-screen claim to Electron (`hooks/useSessionScreen.ts`), the splash slot and its watchdog, the error boundaries. A theme cannot take any of it. |
| **Shell** | `components/DefaultShell.tsx` | the whole frontend body: stacking, the modal stack, which button opens which screen. A theme renders *one* of these — the default one with parts overridden, or its own tree. |
| **Views** | `HomeScreen/DefaultHomeView.tsx`, `LibraryScreen/DefaultLibraryView.tsx` | markup only. |

The view seam is the important one. `HomeScreen` and `LibraryScreen` keep every
decision — paging, focus, sorting, search, launching, the d-pad bindings — and
hand a plain props object to a view component (`HomeScreen/types.ts`,
`LibraryScreen/types.ts`). A theme supplies `homeView` / `libraryView` and
nothing else, so **a themed screen cannot behave differently from the default
one**: it has no code that could. Every navigation bug in the first version of
the theme system came from a theme reimplementing this logic slightly wrong.

`ThemeSurface.tsx` mounts the theme's shell behind an error boundary; if it
throws, the default shell takes over and the crash is recorded (three strikes
and the theme is refused at boot — `useTheme.ts`).

## Store — `store/index.ts`

One Zustand store, no context providers.

| Slice | Fields | Actions |
|---|---|---|
| Navigation | `screen`, `selectedSystemId`, `selectedGameIdx`, `gridFocusIdx`, `gridPage` | `goHome()`, `goLibrary(id)`, `setGridFocus`, `setGridPage`, `setSelectedGameIdx` |
| Focus lock | `modalDepth` | `openModal()`, `closeModal()` |
| Power | `powerPending` | `setPowerPending(action)` |
| Session | `sessionGameKey`, `sessionSystemId`, `backgroundSessions`, `launchConflict`, `sessionMenuRequest` | `setSession`, `setSessionState`, `setLaunchConflict`, `requestSessionMenu()` (the power menu's row; `SessionBar` opens once `modalDepth` is 0) |
| Options button | `gameOptions` opens the settled library game's options; `ShellParts.homeGameOptions` identifies a focused home game | `setGameOptions` (LibraryScreen); `DefaultShell`'s ≡ handler opens the host game options panel for that game, else Settings |

`modalDepth` is the mechanism that keeps gamepad handlers from firing twice.
Every modal increments it on mount and decrements on unmount
(`closeModal` clamps at 0). Screens bail out when it is non-zero:

```ts
const blocked = () => screenRef.current !== 'home' || modalDepthRef.current > 0
```

`powerPending` freezes the UI while the OS is shutting down, so nothing pops
back on screen mid-poweroff.

`frontend/src/lib/launchGate.ts` is the common game-launch gate. It resumes the same
background game, records a conflict for a different one, and lets a normal
launch continue when no game is held. `DefaultShell` renders that conflict with
the host `Overlay`, whose CSS variables are supplied by each theme.

## The gamepad event bus — `hooks/useGamepad.ts`

`useGamepad()` runs one `requestAnimationFrame` poll loop and dispatches
`CustomEvent`s on `window`. Anything in the tree subscribes with
`onGp(event, handler)` and gets a cleanup function back. The dispatch runs
inside `flushSync`, so what a handler sets is drawn in the poll's own frame.

```mermaid
flowchart LR
    gp["navigator.getGamepads()"] --> poll["poll() @60fps"]
    poll -->|edge detected| emit["emit(name)"]
    emit --> snd["playSound(soundForGpEvent(name))"]
    emit --> win["window.dispatchEvent(CustomEvent)"]
    win --> onGp["onGp(...) subscribers"]
    poll -->|raw snapshot| frame["frameListeners"]
    frame --> state["useGamepadState()"]
    state --> art["PadDiagram"]
```

### Events

```
gp:dpad-up   gp:dpad-down   gp:dpad-left   gp:dpad-right
gp:confirm (A/✕)   gp:back (B/○)   gp:y (Y/△)   gp:x (X/□)
gp:menu (Start/Options)   gp:power (Select/Share)   gp:guide (PS/Home)
gp:l1   gp:r1   gp:l2   gp:r2
gp:connected(name)   gp:disconnected
```

### The three invariants

1. **While a game runs, every event is suppressed except `gp:guide`.**
   `isPlaying()` (`store/index.ts`) reads Zustand synchronously. Otherwise emulator input would
   drive the launcher behind the game. Mirrors the old C++ behaviour.
   `playSound` applies the same test, so no caller (settings rows, theme
   handlers, `sdk.system.playSound`) can play a UI sound over a game.
2. **`gp:guide` requires a double press within `GUIDE_DOUBLE_PRESS_MS` (1 s).**
   One press must never kill a running game by accident. Its detail
   `{fromGame}` is what held the screen at the first press: `SessionBar` opens
   its menu only for a pair begun in the interface.
3. **The left stick is edge-triggered into d-pad events** with `DEAD_ZONE = 0.5`,
   so a held stick emits once, not 60 times a second.

### Two APIs, on purpose

| API | Nature | Use for |
|---|---|---|
| `onGp(event, handler)` | edge-triggered | navigation, actions — "□ was pressed" |
| `useGamepadState()` / `onGamepadFrame(cb)` | continuous | drawing — "□ is held", "the stick is at 40 %"; also `index`, `id`, `mapping` of the pad being read |

`GamepadState` = `{connected, pressed[], values[], axes[]}`, indexed by
`GP_BTN` (the standard mapping). Values are quantised to 1/50th so a resting
stick re-renders nothing. The only consumer is the controller screen.

### Button indices — `BTN`

```
A 0   B 1   X 2   Y 3   L1 4   R1 5   L2 6   R2 7
SHARE 8   OPTIONS 9   L3 10   R3 11
DPAD ↑12 ↓13 ←14 →15   GUIDE 16
```

## WebSocket — `hooks/useWebSocket.ts`

Module-level singleton socket at `ws://<host>/ws`, reconnecting every 3 s on
close. `onWsEvent(event, handler)` registers into a `Map<string, Set<Handler>>`
and returns an unsubscribe.

### The WebSocket event table

| Event | Emitted by | Payload | UI effect |
|---|---|---|---|
| `game:started` | `process_manager.launch()` | `game_key`, `system_id`, `session` | the session takes the screen; Electron hides its window |
| `game:finished` | `process_manager._watch()` | + `elapsed`, `session` | removes that run; Electron shows its window again when it was foreground |
| `game:running` | `ws.connect()` | foreground plus `background[]` | late-joining client catches up |
| `game:backgrounded` / `game:foregrounded` | `process_manager` | transition plus complete state snapshot | atomically moves the run between screen and session bar |
| `game:failed` | `services/launch.py` | `game_key`, `system_id`, `detail` | launch refused (catalogue, BIOS, exec) — shows the reason |
| `game:notice` | `services/launch.py` | `game_key`, `system_id`, `detail` | non-blocking warning: missing USB accessory, late pad profile, pack `prepare_launch` notice |
| `gp:connected` | `gamepad_monitor` | `player`, `label`, `vendor`, `product`, `unmapped`… | arrival toast |
| `gp:disconnected` | `gamepad_monitor` | `player`, `label` | departure toast |
| `gp:battery` | `battery.run()` | `name`, `level`, `threshold` | toast, or native HUD in-game |
| `gp:controllers` | `battery.run()` | `controllers[]` | battery levels for the controllers screen |
| `gp:guide` | `gamepad_monitor` | `action` (`backgrounded`, `home`, `failed`), `gesture` | double press: suspends a game; `home` (a pair begun in the interface) opens the session menu in `SessionBar` |
| `standby:screensaver` / `standby:sleep` / `standby:exit` | `standby._enter()` / `exit_standby()` | — | `frontend/src/lib/standbyFade.ts` crossfades the page into the stage; drives `Screensaver`, or the theme's standby |
| `power:sleeping` | `resume.announce_sleep()` | — | `store.setAsleep(true)`: `App.tsx` draws plain black over everything, splash included (Rest mode's second press sets it locally first) |
| `power:resumed` | `resume.on_resume()`, once the TV is back | `resumes` | `frontend/src/lib/wake.ts` → `store.wake()`: black layer off, Home, modals closed, splash replayed from the start, and `window.gamecore.wakeWindow()` (the shell's window back in front); `syncWake()` re-reads `GET /api/power/state` (`resumes`, `sleeping`) on every reconnect, puts the black layer back while the backend still says `sleeping` or `resting` (a `power:sleeping` lost with its socket) and drops one it no longer stands behind. The shell's `onResume` (`onShellResumed()`) covers at once and runs `syncWake()`. Rest mode's POST never gets an answer before the sleep (Chromium drops every socket as the network goes down): a `TypeError` keeps the black, only an HTTP refusal gives the menu back |
| `theme:changed` | `routers/themes.py` | `active` | reloads the theme |
| `profiles:changed` | `routers/profiles.py` | `active` (the active profile) | `frontend/src/lib/players.ts` re-reads the profiles and the pad roster (`refreshPlayers`) |
| `update:log` / `update:done` | `routers/update.py` | `line` / `success`, `code` | OTA progress |
| `catalog:log` / `catalog:done` | `routers/catalog.py` | `line` / `action`, `id`, `success` | pack install/remove progress |
| `catalog:updated` | `routers/catalog.py` | OTA summary | catalogue refreshed over the air |
| `addon:log` / `addon:done` | `routers/addons.py` | `line` / `action`, `name`, `success` | addon install/remove progress |
| `playtime:rekeyed` | `main.py` lifespan; `routers/profiles.py` on a profile switch | `moved` | playtime rows moved to new game keys, or another profile's playtime: reload it |
| addon events | `POST /api/addons/notify` | free-form | e.g. refresh after a ROM upload |

## Components

| File | Lines | Role |
|---|---|---|
| `App.tsx` | 108 | the kernel: splash slot + `SPLASH_WATCHDOG_MS`, `gp:guide`, session-screen claim |
| `components/DefaultShell.tsx` | 190 | the default frontend as one component; `ShellParts`, `ModalScope`, `CONTROLLER_CLOSE_MS` |
| `components/ThemeSurface.tsx` | 44 | `ThemeProvider` + `Shell` — one themed tree, behind a boundary |
| `components/defaults.tsx` | 89 | what a theme may reuse: `Shell`, `DefaultSettingsPages`, `launchApp`, … |
| `components/Splash.tsx` | 374 | rAF boot animation; `T_IMPACT`, `HOLD_MS` and `FRAGMENTS` drive the timeline |
| `components/HomeScreen/index.tsx` | 197 | **behaviour**: 4×2 grid (`COLS`, `ROWS`, `PER_PAGE`), paging, focus, launching |
| `components/HomeScreen/DefaultHomeView.tsx` | 120 | **markup** of the default dashboard |
| `components/HomeScreen/types.ts` | 32 | `HomeViewProps` — the seam a theme plugs into |
| `components/HomeScreen/SystemCard.tsx` | 96 | one tile; `getColor(system)` falls back to `SYSTEM_COLORS` |
| `components/LibraryScreen/index.tsx` | 212 | **behaviour**: sorting, search, selection, launching, the keyboard modal |
| `components/LibraryScreen/DefaultLibraryView.tsx` | 244 | **markup** of the default library |
| `components/LibraryScreen/types.ts` | 66 | `LibraryViewProps`, `SORT_KEYS`, `SORT_LABELS` |
| `components/LibraryScreen/CoverImage.tsx` | 57 | cover art + missing-art fallback; handed to the view. Optional `type` prop draws any media type (`box-3d`, `clear-logo`, `screenshot-gameplay`…) — omitted, it is the jacket from `/api/covers`, byte for byte what it always was |
| `components/LibraryScreen/GameMetaPanel.tsx` | 40 | year/genres/players; handed to the view |
| `components/TopBar/index.tsx` | 134 | clock, IP, storage, `ControllerBattery`, `TBtn` |
| `components/Screensaver.tsx` | 136 | standby slideshow, `ROTATE_MS = 9000` |
| `components/CrtStandby/index.tsx` | — | the CRT standby (`sdk.defaults.CrtStandby`, SDK 11): stage, playlist (`api.standby.videos`), fallbacks video → stills → snow. Orbit and Shelf opt in; see [CRT standby](#crt-standby) |
| `hooks/useLocalWake.ts` | — | mouse move / key press ends standby; shared by both standby screens |
| `hooks/useWakeSplash.ts` | — | `App.tsx`'s "splash done" flag, derived per `wakeCount`: the store write that lifts the black layer after a wake renders the splash in the same frame (an effect-based reset showed one frame of Home first) |
| `hooks/useSessionScreen.ts` | — | tells Electron (`window.gamecore.sessionScreen`) whether a session owns the screen, from `sessionGameKey`; a resumed game maps no new window, so the shell must hide itself |
| `components/modals/SettingsModal.tsx` | 85 | menu; pages live in `settings/`. **Every id in `ITEMS` needs a matching `page === …` line** — `themes` was missing one and the button was silently dead. |
| `components/modals/PowerModal.tsx` | — | **flow**: Rest mode (cursor opens on it), Turn off, Restart, Return to desktop (led by In the background), two-press confirm, ↑↓ and ←→ both move, `POWER_FAILSAFE_MS = 10000`, `REST_FAILSAFE_MS = 35000`; Rest mode is `api.power.rest()`, a refusal shows in the row's subtitle |
| `components/modals/power/DefaultPowerView.tsx` | 68 | **markup** of the default power menu |
| `components/modals/power/types.ts` | 32 | `PowerViewProps` |
| `components/modals/GamepadModal.tsx` | 178 | **flow**: the pad being read (the bus's active pad), the roster (`api.controllers.pads`), live state, the △ hold to the wizard |
| `components/modals/gamepad/DefaultGamepadView.tsx` | 100 | **markup** of the controller screen: drawing left, quick guide right; classes only (`gcs-pad-*`, `settings/css/gamepad.css`, repainted through `--pad-*`); themes dress it as `sdk.defaults.GamepadView` |
| `components/modals/gamepad/types.ts` | 98 | `GamepadViewProps`; hands the view a bound `Art` and `Position` |
| `components/modals/gamepad/PadDiagram.tsx` | 135 | the universal pad diagram — see below |
| `components/ui/index.tsx` | 144 | `Overlay`, `OverlayLabel`, `BackHeader`, `Toggle`, `SliderRow`, `Chip`, `Bars`, `hexToRgb`, `fmtTime`, `fmtDate` |
| `components/ui/VirtualKeyboard.tsx` | 205 | on-screen keyboard (WiFi passwords, library search) |
| `components/ui/Toasts.tsx` | 117 | top-right stack, `TOAST_MS = 10000` |
| `components/WhoIsPlaying.tsx` | — | "Who's using this controller?" at start, in the theme's dress when it returns the optional `whoIsPlaying` surface (`sdk.defaults.createWhoIsPlaying(sdk, { skin })`, `OPTIONAL_SURFACES` in `frontend/src/lib/themeLoader.ts`), else the host's, drawn by the kernel over every shell (z 950, under the splash): decided during the boot (`GET /profiles` and `GET /games/session` from mount) and drawn under the splash before the `who` boot step (`frontend/src/lib/boot.ts`) is marked, so the splash hands over straight to it with no frame of the home; the pad answers it only once the splash is gone (`interactive`); no question, a read that fails or one already asked marks the step at once, so a box with one profile or an unreachable backend boots as before; asked once per interface start (sessionStorage survives a theme reload; marked only once the list arrived, so a backend still starting does not cost the question) when `shouldAskWhoIsPlaying()` (2+ profiles, `auto_login` off) and no game holds the screen; a pick closes it only once the switch is saved, a refusal (a game is open) is shown on it; raises `modalDepth`; markup in `settings/whoIsPlaying.js`, built-in palette |

### Settings pages — `components/modals/settings/`

`WifiPage` (218), `AudioPage` (233), `BluetoothPage` (189), `StandbyPage`
(102), `ThemesPage` (156), `UpdatePage` (143), `DesktopPage` (33). All share
`useSubPageGamepad(onBack, onClose, enabled)`, which binds ○ → back
and □ → close consistently, so no page reimplements it.

**Each page wraps itself in `<Overlay>`.** They are not fragments: a page *is* a
full-screen fixed layer. Putting one inside another box nests a `position:
fixed` layer in a flex container and shatters its layout — which is exactly what
happened when a theme tried to give them its own panel. A theme reuses them bare
and restyles them through CSS variables instead (below).

### Themable tokens

The settings surface is drawn with inline styles, so a stylesheet cannot reach
it. These variables are the hook, and every one falls back to the value the dark
UI has always used — nothing changes unless a theme defines them:

| Variable | Default | Used by |
|---|---|---|
| `--gc-overlay-scrim` | `rgba(5,5,12,0.88)` | the full-screen backdrop behind a settings page, and behind the session menu (where it falls back to `rgba(4,7,14,0.72)`) |
| `--gc-overlay-blur` | `blur(24px)` | same (`blur(18px)` behind the session menu) |
| `--gc-overlay-panel` | `rgba(255,255,255,0.035)` | the card itself |
| `--gc-overlay-border` | `rgba(255,255,255,0.09)` | its hairline |
| `--gc-overlay-radius` | `20px` | its corners |
| `--gc-accent` | `#b8501b` | focus rings, toggles, sliders, the keyboard, the theme picker's marker |
| `--gc-accent-soft` | `#f2a46a` | secondary accent text |
| `--gc-accent-bright` | `#f8cfa9` | figures and emphasis |

Write `var(--gc-accent, #b8501b)` — never a bare `var(--gc-accent)` inside
`color-mix()`. Without the fallback the whole function is invalid when no theme
is active, the declaration is dropped, and the *default* UI loses its accent.

`AudioPage` names its rows (`ROW_VOLUME`, `ROW_OUTPUT`, `ROW_UI_TOGGLE`,
`ROW_UI_VOLUME`, `ROW_COUNT`) rather than indexing by number — worth copying
when adding a page.

### `PadDiagram.tsx` — the pad drawing

The standard layout (W3C / SDL GameController) **by position**: four face
buttons (south, east, west, north) marked with the ✕ ○ △ □ symbols the
on-screen prompts use, d-pad, two sticks, L1/R1, L2/R2, Select, Start, Home.
Lit by position, so any mapped pad lights the right spot.
`frontend/src/lib/padLayout.ts` decides what it is fed:

| Function | Role |
|---|---|
| `buildPads(browser, roster, activeIndex)` | browser pads joined to `GET /api/controllers/pads` by vendor:product, the one being read marked |
| `presentControls(state, entry)` | the pad's SDL controls when known, else the browser's (16 buttons = no Home); absent ones are drawn faded (dashed, sticks faded only) |
| `pressedControls(state)` | held buttons by name, in standard-mapping order |
| `padStatus(pad)` | the one-line verdict: recognised, from the known-pads table, mapped on this box, not recognised, or raw |
| `missingSentence(has, analog)` | "Not on this pad: …", and "Triggers are buttons, not analog." |

`STICK_TRAVEL = 13` is the stick deflection at full axis, the calibration knob.
Colours are `--pd-*` variables a theme sets from its stylesheet.

## CRT standby

`components/CrtStandby/`: a pre-rendered room (`frontend/src/assets/standby/room-off.webp`)
with the game's clip on its TV.

| File | Job |
|---|---|
| `index.tsx` | `screensaver` stage only mounts the room; `sleep` is black with nothing mounted; leaving unmounts it, which pauses and empties both videos. Fetches the playlist once per standby. Reduced motion: no dust, no flicker, a fade instead of snow |
| `TvPicture.tsx` | the reel. Two `<video>` slots: one plays, the next loads paused behind it, so one decodes at a time. A clip plays `clipSeconds()` (its length, 25 s at most), then 450 ms of snow. Every item failing → stills, then snow |
| `useTvPlacement.ts` | `matrix3d` from `frontend/src/assets/standby/room-screen.json` and the cover-fit plate, recomputed on resize |
| `useTvLight.ts` | the picture's mean colour, 16x12 `drawImage` 4x a second; writes `--tv-tint` / `--tv-light` on the root (no re-render). `room-tvlight.webp` is tinted by it (multiply) and screened over the room |
| `TvStatic.tsx`, `DustMotes.tsx`, `StandbyCaption.tsx` | snow canvas, dust canvas, the caption card |
| `crtStandby.css` | layout and the CRT look; the caption reads `--crt-cap-*`, which a theme sets on its `skin` class |

Pure helpers, tested: `frontend/src/lib/homography.ts` (corners → `matrix3d`),
`frontend/src/lib/standbyPlaylist.ts` (mode, clip length, "played 3 days ago"),
`frontend/src/lib/tvLight.ts` (mean colour → tint and amount). The plate is re-rendered
from `docs/dev-log/crt-standby-scene/`.

## `lib/`

| File | Exports |
|---|---|
| `sounds.ts` | `playSound(name)`, `soundForGpEvent(event)`, `soundSettings`, `getAudioContext`. Sounds are **synthesised** with `note()` on a shared `AudioContext` — no audio assets. Silent while `isPlaying()`. Settings persist in `localStorage` (`gc:uiSounds`, `gc:uiSoundsVolume`) |
| `systemColors.ts` | `SYSTEM_COLORS` fallback palette per system id |
| `standbyFade.ts` | `fadeToStandby(stage)`: store write inside a view transition (whole-page crossfade, every theme); after the fade into `sleep`, `api.standby.dark()` |
| `formatGameName.ts` | `formatGameName(raw)` — strips trailing region and language-sequence noise (`REGION_RE`, `LANG_SEQ_RE`) |

## `api/index.ts`

`BASE = '/api'`, generic `get<T>` / `post<T>`, and the `api` object grouping
`systems`, `games`, `metadata`, `media`, `playtime`, `sysinfo`, `update`,
`wifi`, `audio`, `bluetooth`, `addons`, `standby` (`standby.videos(favourites)`
lives in `api/standby.ts`, with `StandbyItem` / `StandbyPlaylist`).
`wifi.setRadio(enabled)` switches the radio and rejects with the backend's
`detail`; `wifi.status().radio` is the state the Wi-Fi pages
(`frontend/src/settings/wifi.js`, `WifiPage.tsx`) draw their switch row from.
No row when it is not a boolean (no hardware, older backend). Turning it off
with no Ethernet takes two presses; right after off → on, an empty scan reads
as "scanning" until the next periodic refresh (NetworkManager has not scanned
yet). Types exported for the UI:
`SystemEntry`, `GameEntry`, `GameMeta`, `MediaEntry`, `GameMediaIndex`,
`PlaytimeEntry`, `SysInfo`. The controller types (`RosterPad`, `UsbDevice`,
the autoconfig and mapping-wizard shapes) live in `api/controllers.ts` and are
re-exported; `api.controllers.pads()` is the controller screen's roster.
The HTTP verbs (`get`, `put`, `post`, `postDetailed`, `del`) live in `api/http.ts`.
`StorageVolume` lives in `api/storage.ts`, and `api.logs` (`usage`, `purge`, `ui`)
in `api/logs.ts`: Settings → System → Logs in `frontend/src/settings/system.js`,
the page every theme draws (the legacy `DefaultSettingsList` menu has
`components/modals/settings/LogsPage.tsx`; Logs is not a `DefaultSettingsPages`
entry, since a theme reaches it through its System page). `frontend/src/lib/reportErrors.ts` (installed in `main.tsx`, and
called by `components/ErrorBoundary.tsx`) sends uncaught errors, rejected promises
and render errors to `api.logs.ui`, 50 per page load at most.
`api.profiles` (`list`, `create`, `update`, `remove`, `setActive`, `addController`, `removeController`) lives in
`api/profiles.ts`; its errors carry the backend's sentence (`sendDetailed` in
`api/http.ts`). `frontend/src/lib/players.ts` names player slots (`playerLabel`, `playerTitle`,
`controllerTitle`): a pad linked to a profile (its page → Controllers) is
shown by that profile's name whichever profile is active, any other pad by
`P<n>`. `refreshPlayers()` reads `GET /api/profiles` and `GET /api/controllers/pads`
into `store.profileName`, `store.padRoster` and `store.padOwners` (player slot →
owner, `padOwners()` matches `RosterPad.id`); `usePlayerNames()` in `App.tsx`
runs it on mount and on `profiles:changed`, `gp:connected`, `gp:disconnected`
and `gp:controllers`. Settings → Controllers meets browser pads to slots with
`matchRoster` (`frontend/src/lib/padLayout.ts`); the arrival toast waits for the read.
Themes reach the labels as `sdk.players`. `store.profileKey` ('' for the primary) gives a theme its
per-profile storage: `sdk.players.storageKey(base)`, re-read on
`sdk.players.onChange` (Jelly's and Orbit's favourites). `sdk.players.Avatar`
is `components/ProfileAvatar.tsx`: the active profile's picture (or initial)
on its colour, for a top bar, rendering nothing without profiles; the shell's
`onProfile` opens Settings with `initialCategory="profiles"`. The pictures are
`settings/avatars.js` (`AVATARS`, animal drawings as SVG markup), kept equal to `profiles.AVATARS` by a test. Settings → Profiles is `frontend/src/settings/profiles.js`, the
tenth rail category every theme draws; its subtitle names the systems whose
saves follow the profile (`separate_saves`, `savesLine()`). The list is a card
per profile (`frontend/src/settings/profileCards.js`: Switch, Edit, "Add
profile", then the "Log in automatically" row); Edit opens the profile's page
over the whole screen (`frontend/src/settings/profileDetail.js`: Switch, Rename,
Delete on the left, the playtime of the profile playing, and picture, colour and
theme, with each theme's `preview`, in view on the right, walked in two
directions with the pad; under them the controllers the profile shows,
`frontend/src/settings/profilePads.js`: each pad with its live state from
`GET /controllers/pads` (re-read on `gamepadconnected`), ✕ removes, "Add
controller" lists the connected pads it does not show and ○ cancels that list.
Display only. Its line icons come from `frontend/src/settings/icons.js`, shared
with the settings rail). Both use the screen's `--set-*` tokens; a theme whose
settings do not set them maps its own there (`config/themes/summer/css/profile.css`).
`frontend/src/settings/themeNames.js` names a profile's theme on the cards and on
"Who's using this controller?".

`api.media` is the one to read before drawing artwork that is not a jacket:

| Call | Returns |
|---|---|
| `api.media.list(systemId, filename)` | `GameMediaIndex` — what this game actually has, each entry with `category`, `kind`, `region`, `cached` |
| `api.media.url(systemId, filename, type)` | a plain string, for an `<img src>` or a `<video src>` |

`list()` first, always: what a game carries depends on the game — a PS3 dump
has 28 media, an obscure cartridge three. `found: false` with
`available: false` means no source is configured on this box, which is not the
same as an unknown game; `unreachable: true` means the question could not be
asked and a retry later is worth something.

`GameMeta` gained optional fields on the same call — `developer`, `publisher`,
`released`, `score` (0–1), `classifications`, `requirements` (a PC game's
minimum spec: `os`, `cpu`, `ram`, `gpu`, `disk`, from the Steam store for packs
with `scraper.steamStore`). They are optional because a
box answering from TheGamesDB has none of them. The seven original keys did not
move.

Same-origin by construction — the SPA is served by the backend, so no base URL
and no CORS.
