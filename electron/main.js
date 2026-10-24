'use strict'
const { app, BrowserWindow, ipcMain, powerMonitor, screen } = require('electron')
const { exec, execFile, spawn } = require('child_process')
const path = require('path')
const fs   = require('fs')
const { leaveToDesktop } = require('./desktop.js')

// Settings → Display → Scale is the interface's only zoom. Windows' own
// display scaling (150 % on most 4K TVs) would multiply it a second time.
app.commandLine.appendSwitch('high-dpi-support', '1')
app.commandLine.appendSwitch('force-device-scale-factor', '1')

// Chromium keeps WebAudio suspended until a "user gesture" — and gamepad
// buttons don't count as one (only mouse/keyboard do). On a controller-only
// kiosk the UI sounds would stay silent forever without this.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')

// This window talks to one thing: the backend on this machine's loopback.
// Nothing it loads is on the internet — the scraper runs in the backend, the
// themes are served by the backend, and the kiosk browsers for YouTube and
// Twitch are separate processes. So a proxy lookup here can only ever cost.
//
// It cost two minutes fourteen. Measured on the reference box, 2026-09-09:
// the backend was serving at 22:08:06, the interface asked for /api/ready and
// got nothing, logged "the backend is not answering" at +20s, and the first
// request actually reached the backend at 22:10:59 — 0.3 s after NetworkManager
// finished associating with the Wi-Fi. Chromium had been resolving the proxy
// configuration for a request to 127.0.0.1, and could not finish until the box
// had a route it never needed.
//
// A console must come up on a box with no network at all. The Wi-Fi being slow
// is not a reason for the interface to be absent.
app.commandLine.appendSwitch('no-proxy-server')

const DEBUG = false

const DEV = DEBUG && process.env.ELECTRON_DEV === '1'
// The installer lets the operator pick the backend port and passes it through
// gamecore-ui.service (Environment=GAMECORE_BACKEND_PORT). Hardcoding 8765
// here meant any other choice left the kiosk on a permanent black screen.
const BACKEND_PORT = process.env.GAMECORE_BACKEND_PORT || '8765'
// The address, not a name for it. `main.py` binds 127.0.0.1 and nothing else,
// so `localhost` bought a DNS lookup and an ambiguity — most resolvers answer
// ::1 first, where there is no listener — for a host we already know the
// number of. The proxy switch above is the other half of the same fix; this
// half is the one that also rules out the IPv6 attempt.
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
const DEV_URL     = 'http://127.0.0.1:5173'

let mainWindow     = null
let backendProcess = null

// Set only by Settings → Desktop (system:quit). Everything else that empties
// the window list is a fault, and window-all-closed rebuilds instead of dying.
let quitting = false
// Timestamps of recent rebuilds. A kiosk must never exit on its own, but it
// must not spin either: if the window cannot stay up, stop and let the
// watchdog that started it decide.
let rebuilds = []
const REBUILD_LIMIT  = 3
const REBUILD_WINDOW = 10_000


/**
 * The colour the box boots to — the active theme's, read from disk.
 *
 * Not a constant, and the reason is measurable: Shelf's boot animation paints
 * `#F4F2ED`, near-white paper. A shell hardcoded to a dark ground therefore
 * flashed dark-to-white at every single boot on the theme the box actually
 * runs — the exact "white frame between two windows" the console boot exists
 * to remove.
 *
 * Read from two files, both on disk, neither needing the backend: the active
 * theme's id, then that theme's manifest. A theme that declares nothing gets
 * the default below, which is what every theme got before this existed.
 *
 * Validated hard, because the value ends up in a window's `backgroundColor`
 * and in the interface's own cover: anything that is not a plain hex colour is
 * refused rather than passed along.
 */
const DEFAULT_BOOT_BG = '#09090f'
const HEX_COLOUR = /^#[0-9a-fA-F]{3,8}$/

function bootBackground() {
  const dataRoot = process.env.GAMECORE_DATA
    || process.env.GAMECORE_PATH
    || path.join(__dirname, '..')
  try {
    const state = JSON.parse(fs.readFileSync(path.join(dataRoot, 'config', 'theme.json'), 'utf8'))
    const id = String(state.active || '')
    // The id names a directory; anything with a separator in it is not an id.
    if (!id || !/^[A-Za-z0-9._-]+$/.test(id)) return DEFAULT_BOOT_BG
    const manifest = JSON.parse(fs.readFileSync(
      path.join(dataRoot, 'config', 'themes', id, 'theme.json'), 'utf8'))
    const declared = manifest && manifest.boot && manifest.boot.background
    if (typeof declared === 'string' && HEX_COLOUR.test(declared.trim())) {
      return declared.trim()
    }
  } catch { /* no theme, no manifest, unreadable JSON — the default is correct */ }
  return DEFAULT_BOOT_BG
}

// ── Main window ───────────────────────────────────────────────────────────────
function createWindow() {
  const bootBg = bootBackground()
  mainWindow = new BrowserWindow({
    width: 1920,
    height: 1080,
    fullscreen: true,
    // Presented on `ready-to-show`, never before: a window shown while its
    // first document is still empty is a white rectangle over the desktop.
    show: false,
    kiosk: !DEBUG && !DEV,
    frame: false,
    autoHideMenuBar: true,
    // Painted before any document exists, and again in the frame between the
    // boot screen and the interface. `boot/boot.html` is transparent so this is
    // the only place the boot colour is decided.
    backgroundColor: bootBg,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
      // Handed to the preload as an argument rather than fetched over IPC: the
      // interface's own cover is drawn on its very first render, and a value
      // that arrives one round trip later is a value that arrives after the
      // frame it was needed for.
      additionalArguments: [`--gamecore-boot-bg=${bootBg}`],
      // This window is the WebSocket bridge for battery alerts even while
      // buried under an emulator — never
      // let Chromium throttle it in the background.
      backgroundThrottling: false,
    },
  })

  // The local boot screen, from disk, before anything is waited for.
  //
  // This window used to be created only after the backend answered, so what
  // covered the television during that wait was the desktop: wallpaper, panel,
  // and whatever the player had left open. Now the window exists first and the
  // interface is loaded INTO it, which also means there is no second window to
  // stack, focus or cross-fade — the two things a compositor is free to get
  // wrong.
  mainWindow.loadFile(path.join(__dirname, 'boot', 'boot.html'))

  // Shown when it has something to show. Without this, Electron presents the
  // window as soon as it exists and the first frame is white — a white flash
  // on a dark boot is more noticeable than the desktop it replaced.
  mainWindow.once('ready-to-show', () => { mainWindow?.show() })

  // A renderer that dies takes the interface with it, and what is left is a
  // window showing the last frame it painted — a console that looks frozen
  // rather than broken. Put the boot screen back and start the sequence over:
  // the backend is asked again, and `boot:ready` decides again.
  //
  // Bounded by the same rebuild budget the window itself uses, so a renderer
  // that cannot survive its first frame stops rather than spinning.
  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    if (quitting) return
    console.error('[boot] the renderer is gone:', details.reason)
    rebuilds = rebuilds.filter(t => Date.now() - t < REBUILD_WINDOW)
    if (rebuilds.length >= REBUILD_LIMIT) {
      console.error('[boot] too many renderer failures — stopping, the watchdog will decide')
      app.quit()
      return
    }
    rebuilds.push(Date.now())
    restartBoot()
  })

  if (DEBUG) mainWindow.webContents.openDevTools({ mode: 'detach' })

  mainWindow.on('closed', () => { mainWindow = null })
  // Windows is signing out or shutting down: the windows closing next are a
  // teardown, not a fault to rebuild from.
  mainWindow.on('session-end', () => { quitting = true })
}

/**
 * Hand the window over to the interface.
 *
 * A navigation inside the same window rather than a second window: the frame
 * between two documents is painted with the window's own `backgroundColor`,
 * which is the same ground the boot screen uses, so the handover is a change
 * of content on an unchanged colour. Two windows would have been a change of
 * WINDOW, and which one the compositor draws on top — and which one has the
 * pad's focus — is not something this code gets to decide.
 *
 * What the player then sees is the theme's own boot animation, which holds its
 * last frame until the interface is ready (SDK 4). The local screen above is
 * deliberately still, so the sequence is one animation, not two.
 */
/**
 * Start the boot again, in the same window.
 *
 * A new generation, so anything still waiting for the previous one — a poll
 * against `/api/ready`, a `boot:ready` from a renderer that has since died —
 * finds its number stale and decides nothing.
 */
async function restartBoot() {
  if (!mainWindow) return
  const generation = ++bootGeneration
  setBootState(BOOT.STARTING)
  mainWindow.loadFile(path.join(__dirname, 'boot', 'boot.html'))
  const up = await waitForBackend(generation)
  if (!up || generation !== bootGeneration) return
  // A start is a return from the desktop as often as a boot.
  announceDesktop(false)
  presentApp()
}

function presentApp() {
  if (!mainWindow) return
  setBootState(BOOT.LOADING_UI)
  mainWindow.loadURL(DEV ? DEV_URL : BACKEND_URL)
}

/**
 * A session owns the screen right now, so the interface must stay out of it.
 *
 * Set by the renderer from the SAME value the input guard reads, and it is what
 * makes resuming a suspended game work at all: on a resume there is no new
 * window to take the screen, the game's window is already there, underneath
 * GameCore. Every `mainWindow.show()` in this file asks this first.
 */
let sessionOwnsScreen = false

/**
 * Who has the screen: the session, or the interface.
 *
 * `show()` is followed by `focus()`, which Windows honours only when this
 * process may take the foreground. The backend raises this window itself when
 * it suspends a game (`window_focus`, dev log D19); this is the second try.
 */
ipcMain.on('shell:session-screen', (_, { owned }) => {
  sessionOwnsScreen = !!owned
  if (!mainWindow) return
  if (sessionOwnsScreen) {
    if (mainWindow.isVisible()) mainWindow.hide()
  } else if (!mainWindow.isVisible()) {
    mainWindow.show()
    mainWindow.focus()
  }
})

// ── Backend startup ───────────────────────────────────────────────────────────
/**
 * Is GameCore usable — not "is something listening".
 *
 * `/api/ready` answers 200 only once the backend's required startup is done,
 * and 503 with the outstanding step until then. This used to ask
 * `/api/sysinfo` and treat ANY response as success: an endpoint that opens a
 * UDP socket towards 8.8.8.8 to find the box's address, walks the disk, reads
 * the controller batteries and lists the BIOS files — polled, on the boot
 * path, to answer a question it was never written for. It also answered
 * perfectly well while the backend was still opening its database, so "alive"
 * arrived several seconds before "usable".
 */
function backendReady() {
  return fetch(BACKEND_URL + '/api/ready', { signal: AbortSignal.timeout(1500) })
    .then(r => (r.ok ? r.json() : null))
    .then(body => !!(body && body.ready))
    .catch(() => false)
}

/**
 * Who owns the backend process.
 *
 * On an installed box, install/bin/gamecore-session does (the logon task), with its own
 * watchdog and environment. Electron spawning a second uvicorn on the same
 * port produced an EADDRINUSE crash loop next to a working backend, and the
 * guard against it was a race: "nothing answered in the last 1.5 s" is true
 * of a backend that is merely still starting.
 *
 * The task that starts the interface sets GAMECORE_MANAGED=1. Unset, this is
 * a checkout run by hand, which may start its own backend.
 */
/**
 * Tell the backend the player is on the Windows desktop (true) or back (false):
 * Windows' idle timers decide there, GameCore's standby takes them back on return.
 */
function announceDesktop(active) {
  return fetch(BACKEND_URL + '/api/standby/desktop', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active }), signal: AbortSignal.timeout(1500),
  }).catch((err) => console.warn('[desktop] the backend was not told:', err.message))
}

function backendIsManaged() {
  return process.env.GAMECORE_MANAGED === '1'
}

async function startBackend() {
  if (DEV) return  // dev: backend is started manually
  if (await backendReady()) return

  // Managed: wait for it, never compete with it. A backend that does not come
  // up is a fault to report — see waitForBackend and the RECOVERING state —
  // not a reason to start a second one.
  if (backendIsManaged()) return

  const root   = path.join(__dirname, '..')
  const venv   = path.join(root, '.venv', 'Scripts', 'python.exe')
  const python = fs.existsSync(venv) ? venv : 'python'

  console.log('[boot] no managed backend — starting one from', root)
  backendProcess = spawn(
    python, ['-m', 'uvicorn', 'backend.main:app',
             '--host', '127.0.0.1', '--port', BACKEND_PORT,
             '--log-level', DEBUG ? 'debug' : 'warning'],
    // UTF-8 mode, as the installed backend runs (dev log D14).
    { cwd: root, detached: false, stdio: 'ignore',
      env: { ...process.env, PYTHONUTF8: '1' } }
  )
}

// ── The boot, as a state machine ─────────────────────────────────────────────
//
// STARTING → WAITING_BACKEND → LOADING_UI → PRESENTABLE → RUNNING, plus
// RECOVERING when something has gone wrong for long enough to say so.
//
// The rule the whole of this file now follows: **no timer promotes anything**.
// Each transition is a fact — the backend answered `/api/ready`, the renderer
// said `boot:ready`. Durations appear in exactly one role, bounding a failure
// so it can be reported rather than waited on for ever.
const BOOT = {
  STARTING: 'STARTING',
  WAITING_BACKEND: 'WAITING_BACKEND',
  LOADING_UI: 'LOADING_UI',
  PRESENTABLE: 'PRESENTABLE',
  RUNNING: 'RUNNING',
  RECOVERING: 'RECOVERING',
}
let bootState = BOOT.STARTING
// Which boot this is. A window reloaded, or a backend restarted underneath a
// running shell, starts another one — and the answers of the previous one must
// not decide anything for it.
let bootGeneration = 0
const bootStartedAt = Date.now()

function setBootState(next) {
  if (bootState === next) return
  bootState = next
  console.log(`[boot] ${next} (+${Date.now() - bootStartedAt}ms)`)
}

/** How long before a backend that is not answering stops being "slow". */
const BACKEND_PATIENCE_MS = 20000
/** Between two polls. Short enough not to add to the boot, long enough not to
 *  be a load of its own on the machine it is measuring. */
const BACKEND_POLL_MS = 250

/**
 * Wait for the backend, for as long as it takes.
 *
 * There is deliberately no deadline that gives up and carries on: carrying on
 * means showing a home screen built from nothing, which is the one outcome
 * this whole step exists to prevent. `BACKEND_PATIENCE_MS` does not end the
 * wait — it ends the SILENCE, moving the boot into RECOVERING so the shell can
 * say what is wrong. The polling then slows down rather than stopping.
 */
async function waitForBackend(generation) {
  setBootState(BOOT.WAITING_BACKEND)
  let slow = false
  for (;;) {
    if (generation !== bootGeneration) return false
    if (await backendReady()) {
      if (slow) console.log('[boot] the backend answered after all')
      return true
    }
    if (!slow && Date.now() - bootStartedAt > BACKEND_PATIENCE_MS) {
      slow = true
      setBootState(BOOT.RECOVERING)
      console.warn('[boot] the backend is not answering /api/ready — still waiting')
    }
    await new Promise(r => setTimeout(r, slow ? BACKEND_POLL_MS * 8 : BACKEND_POLL_MS))
  }
}

// ── IPC handlers ──────────────────────────────────────────────────────────────
//
// The renderer saying it has something worth looking at: the theme resolved or
// its fallback took over, the home data settled, the input bindings are armed
// and the first view has been painted. Decided in the host — see
// frontend/src/lib/boot.ts — because a condition each theme defined for itself
// would be a different condition per theme, and one of them would be a timer.
ipcMain.on('boot:ready', (_, payload) => {
  if (bootState === BOOT.RUNNING) return
  console.log('[boot] the interface is ready',
              payload && payload.steps ? JSON.stringify(payload.steps) : '')
  setBootState(BOOT.PRESENTABLE)
  setBootState(BOOT.RUNNING)
})

require('./hud.js').createHud({ BrowserWindow, screen }).register(ipcMain)

require('./power.js').register({ app, ipcMain, exec, powerMonitor,
  getWindow: () => mainWindow, sessionOwnsScreen: () => sessionOwnsScreen })

// Settings → Desktop mode. `quitting` lets window-all-closed tell this apart
// from an accidental close.
ipcMain.on('system:quit', () => {
  quitting = true
  leaveToDesktop({ execFile, spawn, quit: () => app.quit(), announce: () => announceDesktop(true) })
})

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // There used to be a `session.defaultSession.clearCache()` here, on the
  // reasoning that a localhost cache buys nothing while a stale index.html
  // after an OTA pins the OLD frontend bundle. The second half was right; the
  // first was not. The cache holds the library's cover art, and wiping it on
  // every start meant the box re-downloaded and re-decoded 47 MB of it on
  // every single boot — measured here, 89 files, and the decode is the part
  // the player waits on.
  //
  // What replaces it is a rule instead of a hammer (backend/services/http_cache.py):
  //
  //   · index.html          no-store    — never kept, so it can never pin
  //   · assets/<name>-<hash>.<ext>      immutable — a new build is a new URL
  //   · covers, media, logos            no-cache — kept, revalidated, 304
  //
  // The bundle is immutable BY CONSTRUCTION: Vite names it by content hash, so
  // an old file is unreachable rather than merely unused. The only unhashed
  // file that decides which code runs is index.html, and it is the one file
  // the browser is now forbidden to store. That is why this may go.
  //
  // If an update ever fails to show up on the first launch again, this comment
  // is the place to start — but put the header back, not the wipe.
  const generation = ++bootGeneration
  // The screen is covered first, and everything else happens behind it. This
  // is the whole of the visible change: the order used to be "wait, then show
  // something", and the wait is exactly when there was nothing to see.
  createWindow()

  await startBackend()
  const up = await waitForBackend(generation)
  if (!up || generation !== bootGeneration) return
  // A start is a return from the desktop as often as a boot.
  announceDesktop(false)
  presentApp()
})

// Electron's boilerplate here is `app.quit()`, which is right for a desktop
// app and wrong for a kiosk: a console does not exit because a window went
// away. It did exactly that — cleanly, with code 0 — so systemd's
// Restart=on-failure never fired, and the box came back only because the
// desktop launcher happened to start it again, whole, splash and all. That is
// the "the splash reappears while I'm on the board" report.
//
// The window lifecycle is driven by game launches (mainWindow.hide() on start),
// which is why it struck at random and more often when a launch failed.
//
// So: leaving is a decision, taken in Settings → Desktop, and nowhere else.
// Anything else that empties the window list is a fault to recover from —
// rebuild the window rather than take the whole app down with it.
app.on('window-all-closed', () => {
  if (quitting || process.platform === 'darwin') {
    if (quitting) app.quit()
    return
  }
  const now = Date.now()
  rebuilds = rebuilds.filter((t) => now - t < REBUILD_WINDOW)
  if (rebuilds.length >= REBUILD_LIMIT) {
    console.error(`[gamecore] main window closed ${rebuilds.length} times in ` +
                  `${REBUILD_WINDOW / 1000}s — giving up rather than spinning`)
    quitting = true
    app.quit()
    return
  }
  rebuilds.push(now)
  console.warn('[gamecore] all windows closed with no quit request — rebuilding')
  createWindow()
})

// Any real shutdown passes through here first — Settings → Desktop, a signal,
// or Windows ending the session. Without it, windows closing
// during teardown would look like the fault above and rebuild a window while
// the app is on its way out.
app.on('before-quit', () => { quitting = true })

/**
 * `systemctl stop` has to stop this, and it did not.
 *
 * Every update ends with `gamecore-restart.service` stopping the UI unit.
 * SIGTERM reached the shell and the shell stayed up: systemd waited its full
 * ninety seconds and then SIGKILLed both processes. Measured on the reference
 * box, 08:34:04 → 08:35:35, one minute and forty-five seconds of interface
 * left on the television with its backend already stopped underneath it — long
 * enough to draw whatever a front end draws when nothing answers.
 *
 * The cause is the guard above this: `window-all-closed` cannot tell a
 * teardown from a crash, so windows closing during a shutdown that never went
 * through `before-quit` look like a fault, and it rebuilds one. The app then
 * has a window again and no reason to leave.
 *
 * So the signal says what it means. `quitting` first, `app.quit()` second —
 * in that order, because the quit closes the windows and the flag is what
 * `window-all-closed` reads to let them stay closed. On Windows Node only
 * delivers SIGINT (Ctrl+C) and SIGHUP (console closed); a sign-out arrives as
 * `session-end` instead (createWindow).
 */
for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
  process.on(signal, () => {
    console.log(`[gamecore] ${signal} — leaving`)
    quitting = true
    app.quit()
  })
}

app.on('will-quit', () => {
  if (backendProcess) backendProcess.kill()
})
