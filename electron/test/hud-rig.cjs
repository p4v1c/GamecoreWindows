// The real HUD module, with Electron's window and the timers replaced.
const { createHud } = require('../hud.js')

module.exports = function rig() {
  const windows = [], ipc = new Map(), timers = new Map()
  let serial = 0
  class BrowserWindow {
    constructor(options) {
      this.options = options
      this.handlers = new Map()
      this.calls = []
      this.webContents = { once() {} }
      windows.push(this)
    }
    setIgnoreMouseEvents(value) { this.clickThrough = value }
    setAlwaysOnTop(flag, level) { this.topmost = { flag, level } }
    loadURL(url) { this.html = decodeURIComponent(url.slice(url.indexOf(',') + 1)) }
    once(event, fn) { this.handlers.set(event, fn) }
    on() {}
    show() { this.calls.push('show') }
    showInactive() { this.calls.push('showInactive') }
    moveTop() { this.calls.push('moveTop') }
    focus() { this.calls.push('focus') }
    destroy() { this.destroyed = true }
    /** What Electron does once the toast's first frame can be painted. */
    readyToShow() { this.handlers.get('ready-to-show')?.() }
  }
  const hud = createHud({
    BrowserWindow,
    screen: { getPrimaryDisplay: () => ({ workAreaSize: { width: 1920, height: 1080 } }) },
    timers: {
      setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id },
      clearTimeout: id => timers.delete(id),
    },
  })
  hud.register({ on: (name, fn) => ipc.set(name, fn) })
  return { context: hud, windows, ipc, timers }
}
