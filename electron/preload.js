'use strict'
const { contextBridge, ipcRenderer, webFrame } = require('electron')

/**
 * The colour the box is booting to — the active theme's, decided by main.js
 * from the manifest on disk and handed over as a window argument.
 *
 * An argument rather than an IPC round trip: the interface's own cover is
 * drawn on its first render, and a value that arrives a round trip later
 * arrives after the frame it was needed for.
 */
const BOOT_BG = (process.argv.find(a => a.startsWith('--gamecore-boot-bg=')) || '')
  .slice('--gamecore-boot-bg='.length) || null

contextBridge.exposeInMainWorld('gamecore', {
  /** `null` outside Electron and on an older shell: callers fall back. */
  bootBackground: BOOT_BG,

  // The host, once it has something worth looking at. One signal, sent once,
  // decided in frontend/src/lib/boot.ts — never by a theme and never by a
  // timer. See the `boot:ready` handler in main.js.
  bootReady: (payload) => ipcRenderer.send('boot:ready', payload || {}),
  // Settings → Display → Scale: interface size on this screen. A page zoom,
  // so every theme reflows instead of being cropped. Bounded here too — a
  // factor from nowhere must not make the interface unusable.
  setUiScale: (factor) => {
    const n = Number(factor)
    if (n >= 0.5 && n <= 2) webFrame.setZoomFactor(n)
  },
  reboot:   () => ipcRenderer.send('system:reboot'),
  shutdown: () => ipcRenderer.send('system:shutdown'),
  quit:     () => ipcRenderer.send('system:quit'),

  /**
   * Whether a session owns the screen. Told from the same value the input
   * guard reads, so the window and the pad can never disagree.
   */
  sessionScreen: (owned) => ipcRenderer.send('shell:session-screen', { owned: !!owned }),

  // Alert HUD — shown over fullscreen games where the UI is hidden
  batteryToast:    (data) => ipcRenderer.send('notify:battery', data),
  controllerToast: (data) => ipcRenderer.send('notify:controller', data),

  // The machine woke from sleep (electron/power.js)
  onResume: (cb) => ipcRenderer.on('power:resume', () => cb()),
  // The backend announced a wake: fullscreen, front, repaint (Modern Standby sends no resume)
  wakeWindow: () => ipcRenderer.send('shell:wake-window'),
})

// The pointer hides until the mouse moves, and again after CURSOR_IDLE_MS
// without a move: what unclutter did on Linux. Only over this window; a game
// hides its own.
const CURSOR_IDLE_MS = 5000
const CURSOR_IDLE_CLASS = 'gc-cursor-idle'

window.addEventListener('DOMContentLoaded', () => {
  const style = document.createElement('style')
  style.textContent = `html.${CURSOR_IDLE_CLASS}, html.${CURSOR_IDLE_CLASS} * { cursor: none !important; }`
  document.head.appendChild(style)
  const root = document.documentElement
  let timer = null
  const hide = () => root.classList.add(CURSOR_IDLE_CLASS)
  hide()
  window.addEventListener('mousemove', () => {
    root.classList.remove(CURSOR_IDLE_CLASS)
    clearTimeout(timer)
    timer = setTimeout(hide, CURSOR_IDLE_MS)
  }, { passive: true })
})
