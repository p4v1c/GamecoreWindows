/**
 * The bridge the interface sees, and the pointer it does not.
 *
 * `preload.js` runs in a VM with Electron's renderer modules and the DOM
 * replaced by doubles.
 */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')

const PRELOAD = path.join(__dirname, '..', 'preload.js')

function rig() {
  let exposed = null
  const sent = []
  const listeners = new Map()
  const classes = new Set()
  const timers = new Map()
  let serial = 0
  const on = (name, fn) => listeners.set(name, fn)
  const context = vm.createContext({
    require: (id) => {
      assert.equal(id, 'electron')
      return {
        contextBridge: { exposeInMainWorld: (_, api) => { exposed = api } },
        ipcRenderer: { send: (...a) => sent.push(a), on: () => {} },
        webFrame: { setZoomFactor: () => {} },
      }
    },
    process: { argv: [] },
    window: { addEventListener: on },
    document: {
      createElement: () => ({}),
      head: { appendChild: () => {} },
      documentElement: { classList: {
        add: (c) => classes.add(c), remove: (c) => classes.delete(c) } },
    },
    setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id },
    clearTimeout: (id) => timers.delete(id),
  })
  vm.runInContext(fs.readFileSync(PRELOAD, 'utf8'), context, { filename: PRELOAD })
  return { exposed, sent, listeners, classes, timers }
}

test('the bridge carries no overlay call any more', () => {
  const { exposed } = rig()
  assert.deepEqual(Object.keys(exposed).filter(k => /overlay/i.test(k)), [])
  assert.equal(typeof exposed.sessionScreen, 'function')
})

test('the pointer is hidden from the first frame, and shown while the mouse moves', () => {
  const r = rig()
  r.listeners.get('DOMContentLoaded')()
  assert.ok(r.classes.has('gc-cursor-idle'), 'a kiosk boots with a pointer in the middle of the TV')
  r.listeners.get('mousemove')()
  assert.ok(!r.classes.has('gc-cursor-idle'))
  const [idle] = [...r.timers.values()]
  assert.equal(idle.ms, 5000)
  idle.fn()
  assert.ok(r.classes.has('gc-cursor-idle'))
})

test('each move restarts the idle wait instead of stacking timers', () => {
  const r = rig()
  r.listeners.get('DOMContentLoaded')()
  r.listeners.get('mousemove')()
  r.listeners.get('mousemove')()
  assert.equal(r.timers.size, 1)
})
