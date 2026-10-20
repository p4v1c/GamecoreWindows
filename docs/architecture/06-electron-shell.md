# 6 — Electron shell

`electron/`: `main.js`, `hud.js`, `power.js`, `preload.js`, `boot/boot.html`,
`start-ui.sh`. It owns two windows: the kiosk window and the HUD toast.


## Chromium switches, set before anything

```js
app.commandLine.appendSwitch('enable-transparent-visuals')          // X11 per-pixel alpha
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')
```

The second one is not cosmetic. Chromium keeps WebAudio suspended until a
"user gesture", and **gamepad buttons do not count** — only mouse and
keyboard. On a controller-only kiosk the UI sounds would stay silent forever.

## The two windows

| Window | Created by | Nature |
|---|---|---|
| main | `createWindow()` | kiosk, loads `BACKEND_URL` (or `DEV_URL` when `DEV`) |
| HUD toast | `showHudToast()` (`electron/hud.js`) | transparent, always-on-top, `data:` URL, auto-closes after `HUD_TOAST_MS` (10 s) |

Both use `nodeIntegration: false`, `contextIsolation: true`. The renderer
reaches Electron only through `preload.js`.

### What is on the screen while the box starts

`createWindow()` runs FIRST, before the backend is waited for, and its first
document is `electron/boot/boot.html` — from disk, with no backend, no network
and no bundle. The window is created `show: false` and presented on
`ready-to-show`, so its first visible frame is that document rather than white.

The interface is then loaded into the SAME window (`presentApp()`). Not a
second window: the frame between two documents is painted with the window's own
`backgroundColor`, which is the ground the boot screen uses, whereas two
windows would leave the compositor to decide which is on top and which has the
pad — neither of which is this code's decision.

The boot screen is deliberately still, and has no JavaScript. The theme's own
animation plays a few seconds later and holds its last frame until the
interface is ready; two animations back to back read as a stutter, not as a
start.

### The boot, and what ends it

`waitForBackend()` polls `GET /api/ready` and creates the window only once it
answers 200. There is deliberately no deadline that gives up and carries on:
carrying on means a dashboard built from nothing. `BACKEND_PATIENCE_MS` (20 s)
ends the silence, not the wait — the boot moves to `RECOVERING`, says so, and
slows its polling.

The renderer then sends `boot:ready` when the interface is worth looking at.
Both halves are facts; neither is a timer.

The old `splashHoldMs()` held the splash's first black frame for 4 s whenever
uptime was under 180 s, while X switched the mode and the TV re-synced HDMI. It
is gone: the splash holds its last frame instead, for exactly as long as the
interface behind it is not ready.

## The preload bridge — `preload.js`

The methods on `window.gamecore` (plus the boot ones: `bootBackground`, `bootReady`, `setUiScale`, and `sessionScreen`):

```js
reboot()  shutdown()  quit()
batteryToast(data)  controllerToast(data)
onResume(cb)
```

Typed for the UI in `frontend/src/gamecore.d.ts` (`GamecoreAPI`).
Every call is a one-way `ipcRenderer.send` except the `onResume` listener.

## IPC handlers

| Channel | Handler |
|---|---|
| `system:reboot` | `exec('sudo systemctl reboot')` (`electron/power.js`) |
| `system:shutdown` | `exec('sudo systemctl poweroff')` (`electron/power.js`) |
| `system:quit` | `app.quit()` |
| `notify:battery` | `showBatteryToast(data)` (`electron/hud.js`) |
| `notify:controller` | `notifyController(data)` → `showHudToast(...)` (`electron/hud.js`) |

`powerMonitor` `resume` (`electron/power.js`, `watchResume`, registered with the IPC by `register()` and subscribed once the app is ready): unless a session
owns the screen, the main window is set fullscreen again, shown and focused,
then gets `power:resume` (the renderer covers itself in black, `onResume`) and a
full repaint (`webContents.invalidate()`): the frame kept from before the sleep
comes back with regions gone black.
Rest mode itself is the backend's (`POST /api/power/rest`), not an IPC.

## HUD toasts and untrusted text

`electron/hud.js` (`createHud()`, registered by `main.js`). `showHudToast({icon, title, body, accent})` builds an HTML string for a
`data:` URL. Its inputs come from the renderer, which got them from WebSocket
broadcasts — including `POST /api/addons/notify` (reachable by any addon) and
**Bluetooth device names**. Two guards exist for that reason:

| Function | Guard |
|---|---|
| `escHtml(s)` | escapes anything interpolated into markup |
| `safeColor(c)` | the accent lands inside `style=""`, so it is only ever accepted as a plain colour token |

Never interpolate a broadcast string raw into that template.

## Backend fallback

| Function | Role |
|---|---|
| `backendReady()` | asks `GET /api/ready` |
| `startBackend()` | spawns uvicorn **only if nothing answers** |

On a real box the systemd unit owns the backend and this never fires. It
exists so `npx electron .` works on a dev machine with nothing else running.

## `start-ui.sh`

Run by `gamecore-ui.service` before Electron. It:

1. exports `XDG_RUNTIME_DIR` — without it Chromium has **no audio at all**
   under a systemd service (emulators are unaffected, Flatpak sets it);
2. probes `:1`, `:0`, `:2` with `xdpyinfo` to find the live X display;
3. locates the matching `XAUTHORITY` cookie under `/run/user/<uid>/xauth_*`,
   falling back to `~/.Xauthority`.
