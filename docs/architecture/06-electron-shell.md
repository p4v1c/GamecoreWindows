# 6 — Electron shell

`electron/`: `main.js`, `desktop.js`, `fullscreen.js`, `hud.js`, `power.js`, `preload.js`, `boot/boot.html`,
`start-ui.sh`. It owns two windows: the kiosk window and the HUD toast.


## Chromium switches, set before anything

```js
app.commandLine.appendSwitch('high-dpi-support', '1')
app.commandLine.appendSwitch('force-device-scale-factor', '1')
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')
app.commandLine.appendSwitch('no-proxy-server')
```

| Switch | Why |
|---|---|
| `force-device-scale-factor=1` | Settings → Display → Scale (a page zoom, `setUiScale`) is the interface's only zoom. Windows runs a 4K TV at 150 % or more and would scale the interface a second time |
| `autoplay-policy` | Chromium keeps WebAudio suspended until a "user gesture", and **gamepad buttons do not count**. On a controller-only kiosk the UI sounds would stay silent forever |
| `no-proxy-server` | the window only talks to the loopback backend; a proxy lookup stalled the first request until the network was up |

The Linux `enable-transparent-visuals` switch is gone: it is X11-only, and
Windows composes transparent windows (the HUD) without it.

## The two windows

| Window | Created by | Nature |
|---|---|---|
| main | `createWindow()` | kiosk, created on the primary display's bounds, loads `BACKEND_URL` (or `DEV_URL` when `DEV`); `createFullscreenGuard()` (`electron/fullscreen.js`) puts it back on its monitor's full bounds on `show`, `resize`, `restore`, `leave-full-screen` and every display change, at most 5 times in 10 s: Windows hides the taskbar only for a window covering the whole monitor, and a fullscreen round trip left it 1920x1079 |
| HUD toast | `showHudToast()` (`electron/hud.js`) | transparent, click-through, `focusable: false`, created hidden and shown with `showInactive()` at the `screen-saver` level, `data:` URL, auto-closes after `HUD_TOAST_MS` (10 s) |

Both use `nodeIntegration: false`, `contextIsolation: true`. The renderer
reaches Electron only through `preload.js`.

### What is on the screen while the box starts

`createWindow()` runs FIRST, before the backend is waited for, and its first
document is `electron/boot/boot.html` — from disk, with no backend, no network
and no bundle. The window is created `show: false` and presented on
`ready-to-show`, so its first visible frame is that document rather than white.

Once the backend answers, the shell posts `/api/standby/desktop` `{active: false}`
(a start is often a return from Desktop mode) and the interface is loaded into
the SAME window (`presentApp()`). Not a
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
onResume(cb)  wakeWindow()
```

Typed for the UI in `frontend/src/gamecore.d.ts` (`GamecoreAPI`).
Every call is a one-way `ipcRenderer.send` except the `onResume` listener.

The preload also hides the pointer over the window: from the first frame, back
on a mouse move, hidden again after `CURSOR_IDLE_MS` (5 s) without one, through
a `gc-cursor-idle` class on `<html>`. On Linux `start-ui.sh` ran `unclutter`
X-wide; on Windows a game hides its own pointer.

## IPC handlers

| Channel | Handler |
|---|---|
| `system:reboot` | `exec('shutdown /r /t 0')` (`electron/power.js`) |
| `system:shutdown` | `exec('shutdown /s /t 0')` (`electron/power.js`) |
| `system:quit` | `leaveToDesktop()` (`electron/desktop.js`): posts `/api/standby/desktop` `{active: true}` (Windows' idle timers back to Windows), then `app.quit()` (exit code 0), bounded by `LEAVE_DEADLINE_MS` (5 s). Opening the desktop is `gamecore-session`'s and `gamecore-shell`'s ([1](01-runtime-topology.md#the-console-users-desktop)): Explorer started here would be elevated, and a folder window under the GameCore shell |
| `notify:battery` | `showBatteryToast(data)` (`electron/hud.js`) |
| `notify:controller` | `notifyController(data)` → `showHudToast(...)` (`electron/hud.js`) |
| `shell:wake-window` | the renderer heard `power:resumed`: unless a session owns the screen, the main window fullscreen, shown, focused and repainted (`registerWakeWindowIpc`, `electron/power.js`); Windows sends `powerMonitor` no resume for Modern Standby |

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

The toast never takes the foreground: `show()` would activate it, pull the game
out of fullscreen and move the pad's input away from it. It is drawn over a
**borderless** fullscreen game only; Windows composes nothing over an
**exclusive** fullscreen DirectX or Vulkan surface, so packs must ask their
emulator for borderless fullscreen. Windows toast notifications are not an
option: Focus Assist hides them during games.

## Backend fallback

| Function | Role |
|---|---|
| `backendReady()` | asks `GET /api/ready` |
| `backendIsManaged()` | `GAMECORE_MANAGED=1`: the backend belongs to someone else, never start one |
| `startBackend()` | spawns uvicorn from `.venv\Scripts\python.exe` (else `python`), with `PYTHONUTF8=1`, **only if unmanaged and nothing answers** |

On an installed PC the logon task owns the backend and must start the
interface with `GAMECORE_MANAGED=1` (port step 7); this then never fires. It
exists so `npx electron .` works in a checkout with nothing else running. The
fallback runs the backend's real lifespan, which changes this PC's power plan
and display settings: for a look at the interface, point the shell at the
read-only dev server instead (`GAMECORE_BACKEND_PORT=8766`, `GAMECORE_MANAGED=1`).
Linux used systemd's `INVOCATION_ID` as the signal; it means nothing here.

## `start-ui.sh`

Run by `gamecore-ui.service` before Electron. It:

1. exports `XDG_RUNTIME_DIR` — without it Chromium has **no audio at all**
   under a systemd service (emulators are unaffected, Flatpak sets it);
2. probes `:1`, `:0`, `:2` with `xdpyinfo` to find the live X display;
3. locates the matching `XAUTHORITY` cookie under `/run/user/<uid>/xauth_*`,
   falling back to `~/.Xauthority`.
