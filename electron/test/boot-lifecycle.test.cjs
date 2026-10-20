/**
 * The boot, driven against the real `electron/main.js`.
 *
 * Two things are asserted, and they are the two the shell used to get wrong:
 *
 *   · **who owns the backend.** On an installed box systemd does. Electron
 *     spawning a second uvicorn on the same port produced an EADDRINUSE crash
 *     loop next to a working backend, and the guard against it — "nothing
 *     answered in the last 1.5 s" — is true of a backend that is merely still
 *     starting.
 *   · **what "ready" means.** `/api/sysinfo` answers while the backend is
 *     still opening its database, so "alive" arrived seconds before "usable",
 *     and the window was created against a backend that could not serve it.
 *
 * Electron, the child processes and the network are doubles; no window is
 * created and no process is spawned.
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')

const MAIN = path.join(__dirname, '..', 'main.js')

/**
 * @param answers  the sequence /api/ready gives back, `true` = 200 {ready}
 * @param env      the process environment main.js sees
 */
function rig({ answers = [true], env = {}, execRefuses = [] } = {}) {
  const windows = []
  const spawned = []
  const switches = []       // every Chromium flag the shell asked for
  const ipc = new Map()
  const asked = []
  const execs = []          // every shell command the shell asked for, in order
  const quits = []
  let readyResolve
  const appReady = new Promise((r) => { readyResolve = r })

  class Window {
    constructor(options) {
      this.options = options
      this.loaded = []          // every document this window was given, in order
      this.shown = false
      this.handlers = new Map()
      windows.push(this)
      // `on` as well as `once`: the window listens for a renderer that dies.
      this.rendererHandlers = new Map()
      this.webContents = {
        isLoading: () => false, send: () => {}, once: () => {}, openDevTools: () => {},
        on: (event, fn) => this.rendererHandlers.set(event, fn),
      }
    }
    setIgnoreMouseEvents() {} setAlwaysOnTop() {} setBounds() {}
    getBounds() { return { x: 0, y: 0, width: 1920, height: 1080 } }
    loadFile(p) { this.loaded.push({ kind: 'file', at: p }) }
    loadURL(u) { this.loaded.push({ kind: 'url', at: u }) }
    once(event, fn) { this.handlers.set(event, fn) }
    on() {} close() {} hide() {} isVisible() { return true }
    show() { this.shown = true }
    /** What Electron does once the first document can be painted. */
    readyToShow() { this.handlers.get('ready-to-show')?.() }
    /** What Electron does when the renderer process dies. */
    rendererGone(reason = 'crashed') {
      this.rendererHandlers.get('render-process-gone')?.({}, { reason })
    }
  }

  const child = { stdout: { on: () => {} }, stdin: { write: () => {} }, on: () => {} }
  const electron = {
    app: {
      commandLine: { appendSwitch: (...a) => { switches.push(a.join('=')) } },
      whenReady: () => appReady,
      on: () => {}, quit: () => { quits.push(Date.now()) },
    },
    BrowserWindow: Window,
    ipcMain: { on: (key, fn) => ipc.set(key, fn) },
    screen: { getPrimaryDisplay: () => ({ scaleFactor: 1, bounds: { x: 0, y: 0, width: 1920, height: 1080 } }) },
  }

  const stubs = {
    electron,
    'child_process': {
      spawn: (bin, args) => { spawned.push({ bin, args }); return child },
      exec: (cmd, cb) => {
        execs.push(cmd)
        const bad = execRefuses.some(frag => cmd.includes(frag))
        setTimeout(() => cb?.(bad ? new Error('sudo: a password is required') : null), 0)
      },
    },
    // The real fs: this bench is partly ABOUT what main.js reads from disk —
    // the active theme and its manifest — and a stub that answers `{}` to
    // every read would make that test pass against a shell reading nothing.
    fs,
    path,
    os: { uptime: () => 999 },
  }

  const queue = answers.slice()
  const signals = new Map()
  const context = vm.createContext({
    // The shell's own modules load for real; Electron and Node are doubles.
    require: (id) => id.startsWith('./') ? require(path.join('..', id)) : stubs[id],
    __dirname: path.dirname(MAIN),
    process: { env, on: (sig, fn) => signals.set(sig, fn) },
    console: { log: () => {}, warn: () => {}, error: () => {} },
    setTimeout, clearTimeout, URLSearchParams, AbortSignal, AbortController,
    fetch: (url) => {
      asked.push(String(url))
      const ok = queue.length > 1 ? queue.shift() : queue[0]
      return Promise.resolve({ ok, status: ok ? 200 : 503, json: async () => ({ ready: !!ok, state: ok ? 'ready' : 'starting' }) })
    },
  })
  vm.runInContext(fs.readFileSync(MAIN, 'utf8'), context, { filename: MAIN })
  return {
    context, windows, ipc, asked, execs, quits, switches,
    /** What systemd sends on `systemctl stop`. */
    signal: (sig = 'SIGTERM') => signals.get(sig)?.(),
    hasSignal: (sig) => signals.has(sig),
    /** What the renderer sends when the player picks Settings → Mode bureau. */
    quit: () => ipc.get('system:quit')?.(),
    // Only a second uvicorn is the defect this file is about.
    backends: () => spawned.filter(s => s.args.join(' ').includes('uvicorn')),
    start: () => { readyResolve(); return appReady },
    // The wait for a backend deliberately has no deadline — it slows down and
    // reports, it never gives up. Ending the generation is how a test stops it
    // without the code learning to abandon a boot.
    stop: () => vm.runInContext('bootGeneration += 1', context),
  }
}

/** Let the boot's promise chain and its polling run. */
const settle = (ms = 60) => new Promise(r => setTimeout(r, ms))

test('the screen is covered before anything is waited for', async () => {
  // The order is the whole visible change. The window used to be created after
  // the backend answered, so what covered the television during that wait was
  // the desktop: wallpaper, panel, and whatever was left open.
  const r = rig({ answers: [false], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(120)
  r.stop()
  assert.equal(r.windows.length, 1, 'nothing was on screen while the backend started')
  const first = r.windows[0].loaded[0]
  assert.equal(first.kind, 'file', 'the first document came from the network')
  assert.match(first.at, /boot[\\/]boot\.html$/)
  assert.equal(r.windows[0].options.show, false, 'a window shown before its first paint is a white flash')
})

test('the interface replaces the boot screen in the same window', async () => {
  // A second window would be a second thing for the compositor to stack and
  // to focus, and neither is this code's decision to make.
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(200)
  r.stop()
  assert.equal(r.windows.length, 1)
  const kinds = r.windows[0].loaded.map(l => l.kind)
  assert.deepEqual(kinds, ['file', 'url'])
})

test('a backend that never answers leaves the boot screen up', async () => {
  const r = rig({ answers: [false], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(900)
  r.stop()
  assert.deepEqual(r.windows[0].loaded.map(l => l.kind), ['file'],
    'the interface was loaded over a backend that cannot serve it')
})

test('the window is shown when it has something to show', async () => {
  const r = rig({ answers: [false], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(120)
  r.stop()
  assert.equal(r.windows[0].shown, false)
  r.windows[0].readyToShow()
  assert.equal(r.windows[0].shown, true)
})

test('a managed box never starts a second backend', async () => {
  // INVOCATION_ID is what systemd sets in every service it runs; start-ui.sh is
  // the unit's ExecStart, so Electron inherits it.
  const r = rig({ answers: [false, false, true], env: { INVOCATION_ID: 'abc123' } })
  r.start()
  await settle(900)
  r.stop()
  assert.deepEqual(r.backends(), [], 'Electron competed with systemd for the port')
})

test('an unmanaged box still starts one when nothing answers', async () => {
  const r = rig({ answers: [false, false, true], env: {} })
  r.start()
  await settle(900)
  r.stop()
  assert.equal(r.backends().length, 1)
})

test('readiness is asked of /api/ready, not of the diagnostic endpoint', async () => {
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(200)
  r.stop()
  assert.ok(r.asked.length > 0)
  assert.ok(r.asked.every(u => u.includes('/api/ready')), r.asked.join(' '))
  assert.ok(!r.asked.some(u => u.includes('/api/sysinfo')))
})

test('the interface saying it is ready is what ends the boot', async () => {
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(200)
  r.stop()
  assert.equal(typeof r.ipc.get('boot:ready'), 'function',
    'the host has no way to say the interface is ready')
  r.ipc.get('boot:ready')(null, { steps: { theme: true, systems: true } })
})

test('the boot screen needs nothing but itself', () => {
  // It is what covers the television when the backend is down, the network is
  // absent and the bundle has not been built. Anything it has to fetch is a
  // way for it to fail at exactly the moment it exists for.
  const html = fs.readFileSync(path.join(__dirname, '..', 'boot', 'boot.html'), 'utf8')
  assert.ok(!/https?:\/\//i.test(html), 'the boot screen reaches for the network')
  assert.ok(!/<script/i.test(html), 'a boot screen that can hang is not a boot screen')
  assert.ok(!/src=|href=/i.test(html), 'the boot screen loads a file of its own')
})

test('a renderer that dies puts the boot screen back', async () => {
  // What is left otherwise is a window showing the last frame it painted: a
  // console that looks frozen rather than broken, with no way back but the
  // power switch.
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(200)
  const window = r.windows[0]
  assert.deepEqual(window.loaded.map(l => l.kind), ['file', 'url'])

  window.rendererGone('crashed')
  await settle(200)
  r.stop()
  assert.deepEqual(window.loaded.map(l => l.kind), ['file', 'url', 'file', 'url'],
    'the boot screen did not come back, or the interface never returned')
})

// ── the colour the box boots to ─────────────────────────────────────────────

/** A data root on disk: an active theme, and a manifest that may declare a ground. */
function themeTree(active, manifest) {
  const os = require('node:os')
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gamecore-boot-'))
  fs.mkdirSync(path.join(root, 'config', 'themes', active), { recursive: true })
  fs.writeFileSync(path.join(root, 'config', 'theme.json'), JSON.stringify({ active }))
  if (manifest !== null) {
    fs.writeFileSync(path.join(root, 'config', 'themes', active, 'theme.json'),
                     JSON.stringify(manifest))
  }
  return root
}

/** main.js evaluated with a real data root, stopping at the window options. */
function backgroundFor(dataRoot) {
  const r = rig({ answers: [false], env: { GAMECORE_DATA: dataRoot, INVOCATION_ID: 'x' } })
  r.context.createWindow()
  return r.windows[0].options.backgroundColor
}

test('the boot colour is the active theme’s, not a constant', () => {
  // Shelf's splash paints near-white paper. A shell hardcoded to a dark ground
  // flashed dark-to-white at every boot on the theme the box actually runs.
  const root = themeTree('shelf', { id: 'shelf', boot: { background: '#F4F2ED' } })
  assert.equal(backgroundFor(root), '#F4F2ED')
})

test('a theme that declares nothing gets the old default', () => {
  const root = themeTree('plain', { id: 'plain' })
  assert.equal(backgroundFor(root), '#09090f')
})

test('anything that is not a colour is refused rather than passed on', () => {
  // The value reaches a window's backgroundColor and the interface's own cover.
  for (const declared of ['red; }', 'url(http://x/y)', '', 42, null, '#zzz']) {
    const root = themeTree('odd', { id: 'odd', boot: { background: declared } })
    assert.equal(backgroundFor(root), '#09090f', `accepted ${JSON.stringify(declared)}`)
  }
})

test('a missing or unreadable theme costs the colour, never the boot', () => {
  const os = require('node:os')
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), 'gamecore-boot-'))
  assert.equal(backgroundFor(empty), '#09090f')

  const broken = themeTree('shelf', null)              // no manifest at all
  assert.equal(backgroundFor(broken), '#09090f')

  fs.writeFileSync(path.join(broken, 'config', 'theme.json'), '{ not json')
  assert.equal(backgroundFor(broken), '#09090f')
})

test('an active id that names a path is not followed', () => {
  const root = themeTree('shelf', { id: 'shelf', boot: { background: '#F4F2ED' } })
  fs.writeFileSync(path.join(root, 'config', 'theme.json'),
                   JSON.stringify({ active: '../../../etc' }))
  assert.equal(backgroundFor(root), '#09090f')
})

test('the interface is handed the same colour, before its first frame', () => {
  const root = themeTree('shelf', { id: 'shelf', boot: { background: '#F4F2ED' } })
  const r = rig({ answers: [false], env: { GAMECORE_DATA: root, INVOCATION_ID: 'x' } })
  r.context.createWindow()
  const args = r.windows[0].options.webPreferences.additionalArguments
  assert.ok(args.includes('--gamecore-boot-bg=#F4F2ED'),
    'the renderer would draw its cover in a different colour than the window')
})

test('the boot screen paints no ground of its own', () => {
  // It is transparent so the window's colour shows through — one decision, one
  // place, and no frame where the two disagree.
  const html = fs.readFileSync(path.join(__dirname, '..', 'boot', 'boot.html'), 'utf8')
  assert.match(html, /background:\s*transparent/)
  assert.ok(!/#09090f/i.test(html), 'the boot screen hardcodes a colour again')
  // `<title>` is not the screen; a wordmark drawn in the page is.
  assert.ok(!/class="mark"/.test(html) && !/letter-spacing/.test(html),
    'the boot screen draws a wordmark — the theme owns the intro')
})

// ── Leaving the console session ──────────────────────────────────
//
// On the reference box "Mode bureau" blacked the screen and came straight back
// to GameCore. The shell asked one variable, spelled one way:
//
//     if (process.env.XDG_SESSION_DESKTOP !== 'gamecore') { app.quit() }
//
// and SDDM sets that one from `DesktopNames=`, which is `GameCore`. So every
// press took the early exit, the auto-login was never handed back, and Relogin
// brought the console up again. The three variables below are what the real
// session actually carries — checked with `tr '\0' '\n' < /proc/<pid>/environ`
// on the box, not assumed.

const CONSOLE_ENV = { DESKTOP_SESSION: 'gamecore', XDG_SESSION_DESKTOP: 'GameCore',
                      XDG_CURRENT_DESKTOP: 'GameCore', INVOCATION_ID: 'x' }

const runtimeDir = () => fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'gc-rt-'))
const marker = (dir) => path.join(dir, 'gamecore', 'leave-to-desktop')

test('leaving the console session asks the session, and quits', async () => {
  // The switch is the session teardown's job now — see main.js. What this
  // process owes it is the marker and then its own exit, in that order.
  const rt = runtimeDir()
  const r = rig({ env: { ...CONSOLE_ENV, XDG_RUNTIME_DIR: rt } })
  r.quit()
  await settle(20)
  assert.ok(fs.existsSync(marker(rt)), 'the session was never told to leave')
  assert.deepEqual(r.execs, [], 'it ran the switch in the cgroup that is about to be torn down')
  assert.equal(r.quits.length, 1, 'the app stayed up after asking to leave')
})

test('the capital letters in XDG_SESSION_DESKTOP are not a different session', async () => {
  // SDDM fills XDG_SESSION_DESKTOP from DesktopNames=, which is `GameCore`,
  // while the session script exports the file's name, `gamecore`. A shell that
  // compares against one spelling answers wrongly in the other session.
  const rt = runtimeDir()
  const r = rig({ env: { XDG_SESSION_DESKTOP: 'GameCore', XDG_RUNTIME_DIR: rt, INVOCATION_ID: 'x' } })
  r.quit()
  await settle(20)
  assert.ok(fs.existsSync(marker(rt)), 'it quit without asking for the desktop')
})

test("a window on somebody else's desktop just closes", async () => {
  const rt = runtimeDir()
  const r = rig({ env: { DESKTOP_SESSION: 'plasma', XDG_SESSION_DESKTOP: 'KDE',
                         XDG_CURRENT_DESKTOP: 'KDE', XDG_RUNTIME_DIR: rt, INVOCATION_ID: 'x' } })
  r.quit()
  await settle(20)
  assert.ok(!fs.existsSync(marker(rt)), 'it asked a desktop session to hand itself over')
  assert.deepEqual(r.execs, [])
  assert.equal(r.quits.length, 1)
})

test('a session that cannot be told is switched the old way instead', async () => {
  // No runtime directory means no marker, which would leave the box in the
  // console session with no way out from the sofa. The direct call is worth
  // trying even though it is the one that failed on the reference box: it
  // works everywhere else, and there is nothing better left to do.
  const r = rig({ env: { ...CONSOLE_ENV, XDG_RUNTIME_DIR: '' } })
  r.quit()
  await settle(30)
  assert.equal(r.execs.length, 1, r.execs.join(' | '))
  // `--once`, like the marker path: this fallback is still the player asking
  // for the desktop, not asking to stop booting the console. Leaving it out
  // here would make the one box that needs the fallback the one box the trip
  // is permanent on.
  assert.match(r.execs[0], /gamecore-session-select desktop --once --restart-dm$/)
  assert.equal(r.quits.length, 1)
})

test('systemctl stop actually stops the shell', async () => {
  // It did not. Every update ends by stopping this unit, SIGTERM reached the
  // shell, and the shell rebuilt its window and stayed: systemd waited ninety
  // seconds and SIGKILLed it, with the backend already gone underneath. That
  // is the minute and forty-five of dead interface the box showed after each
  // update.
  const r = rig({ env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(60)
  r.stop()
  const before = r.windows.length

  r.signal('SIGTERM')

  assert.equal(r.quits.length, 1, 'SIGTERM did not ask the app to leave')
  r.context.eval?.('')
  assert.equal(r.windows.length, before, 'it built a new window on the way out')
})

test('the interrupt signals are all handled, not just the one we tested', () => {
  const r = rig({ env: { INVOCATION_ID: 'x' } })
  for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
    assert.ok(r.hasSignal(sig), `${sig} would kill the shell without a clean exit`)
  }
})

// ── the interface must not need the network to reach its own backend ────────

test('nothing the shell loads is addressed by a name that has to be resolved', async () => {
  /*
   * Measured on the reference box, 2026-09-09. The backend was serving at
   * 22:08:06. The interface asked for /api/ready, got nothing, and said "the
   * backend is not answering" at +20s. The first request actually reached the
   * backend at 22:10:59 — 0.3 seconds after NetworkManager finished associating
   * with the Wi-Fi. Two minutes fourteen of black screen, for a request to a
   * port on the same machine.
   *
   * `main.py` binds 127.0.0.1 and nothing else, so the number was never in
   * doubt. Asking for `localhost` bought a resolver lookup, an ::1 attempt
   * where nothing listens, and — the expensive part — Chromium's proxy
   * resolution, which cannot finish on a box that has no route yet.
   */
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(120)
  r.stop()

  const addressed = [...r.asked, ...r.windows.flatMap(w => w.loaded.map(d => d.at))]
  assert.ok(addressed.length > 0, 'the shell asked for nothing at all')
  for (const at of addressed) {
    assert.ok(!/\/\/localhost[:/]/.test(at),
      `${at} is addressed by name; the backend is bound to 127.0.0.1 and a box ` +
      'with no network cannot always resolve one')
  }
})

test('and it does not look for a proxy to reach its own loopback', async () => {
  // The other half. Even addressed by number, Chromium consults the proxy
  // configuration before it connects, and that is what actually stalled: it
  // could not complete until the box had a default route. This window talks to
  // one thing — the backend on this machine — so there is nothing a proxy could
  // ever be for.
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  r.start()
  await settle(60)
  r.stop()
  assert.ok(r.switches.includes('no-proxy-server'),
    `the shell did not switch the proxy lookup off; flags were ${JSON.stringify(r.switches)}`)
})

test('Windows display scaling does not zoom the interface a second time', async () => {
  // Settings → Display → Scale is a page zoom. A 4K television runs Windows at
  // 150 % or more, and the interface would otherwise be scaled by both.
  const r = rig({ answers: [true], env: { INVOCATION_ID: 'x' } })
  assert.ok(r.switches.includes('force-device-scale-factor=1'),
    `the device scale is left to Windows; flags were ${JSON.stringify(r.switches)}`)
  assert.ok(!r.switches.includes('enable-transparent-visuals'),
    'an X11-only switch is still set')
})
