// Evaluate the actual HUD section without starting Electron or touching a display.
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

module.exports = function rig(source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8')) {
  const windows = [], ipc = new Map(), timers = new Map()
  let serial = 0
  class BrowserWindow {
    constructor(options) {
      this.options = options
      this.webContents = { once() {} }
      windows.push(this)
    }
    setIgnoreMouseEvents(value) { this.clickThrough = value }
    loadURL(url) { this.html = decodeURIComponent(url.slice(url.indexOf(',') + 1)) }
    on() {}
    destroy() { this.destroyed = true }
  }
  const context = vm.createContext({
    hudContract: require('../hud-tokens.json'), BrowserWindow,
    ipcMain: { on: (name, fn) => ipc.set(name, fn) },
    screen: { getPrimaryDisplay: () => ({ workAreaSize: { width: 1920, height: 1080 } }) },
    setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id },
    clearTimeout: id => timers.delete(id),
  })
  vm.runInContext(source.slice(source.indexOf('let hudToastWindow'), source.indexOf('let sessionOwnsScreen')), context)
  return { context, windows, ipc, timers }
}
