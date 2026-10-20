/**
 * electron/power.js: the reboot/shutdown commands, and the window after a wake.
 * Electron is a double; nothing is suspended or rebooted.
 */
const assert = require('node:assert/strict')
const { test } = require('node:test')
const { register, registerPowerIpc, watchResume } = require('../power.js')

function fakeWindow({ fullscreen = false } = {}) {
  const calls = []
  return {
    calls,
    isFullScreen: () => fullscreen,
    setFullScreen: (v) => { fullscreen = v; calls.push(`fullscreen:${v}`) },
    show: () => calls.push('show'),
    focus: () => calls.push('focus'),
    webContents: {
      send: (channel) => calls.push(`send:${channel}`),
      invalidate: () => calls.push('repaint'),
    },
  }
}

function fakeMonitor() {
  const handlers = new Map()
  return { on: (ev, fn) => handlers.set(ev, fn), emit: (ev) => handlers.get(ev)?.() }
}

test('reboot and shutdown ask Windows, at once', () => {
  const ipc = new Map()
  const execs = []
  registerPowerIpc({ ipcMain: { on: (k, fn) => ipc.set(k, fn) }, exec: (c) => execs.push(c) })
  ipc.get('system:reboot')()
  ipc.get('system:shutdown')()
  assert.deepEqual(execs, ['shutdown /r /t 0', 'shutdown /s /t 0'])
})

test('a wake puts the interface back in fullscreen and focused', () => {
  const win = fakeWindow({ fullscreen: false })
  const monitor = fakeMonitor()
  watchResume({ powerMonitor: monitor, getWindow: () => win,
                sessionOwnsScreen: () => false, log: () => {} })
  monitor.emit('resume')
  assert.deepEqual(win.calls, ['fullscreen:true', 'show', 'focus', 'send:power:resume', 'repaint'])
})

test('a wake leaves a session that owns the screen alone', () => {
  const win = fakeWindow()
  const monitor = fakeMonitor()
  watchResume({ powerMonitor: monitor, getWindow: () => win,
                sessionOwnsScreen: () => true, log: () => {} })
  monitor.emit('resume')
  assert.deepEqual(win.calls, [])
})

test('an Electron without powerMonitor is not an error', () => {
  assert.equal(watchResume({ powerMonitor: undefined, getWindow: () => null,
                             sessionOwnsScreen: () => false }), false)
})

test('register wires the commands now and the resume hook once the app is ready', async () => {
  const ipc = new Map()
  const monitor = fakeMonitor()
  const win = fakeWindow()
  let ready
  const app = { whenReady: () => new Promise((r) => { ready = r }) }
  register({ app, ipcMain: { on: (k, fn) => ipc.set(k, fn) }, exec: () => {}, powerMonitor: monitor,
             getWindow: () => win, sessionOwnsScreen: () => false })
  assert.ok(ipc.has('system:reboot') && ipc.has('system:shutdown'))
  monitor.emit('resume')
  assert.deepEqual(win.calls, [], 'subscribed before app ready')
  ready()
  await new Promise((r) => setImmediate(r))
  const quiet = console.log
  console.log = () => {}
  monitor.emit('resume')
  console.log = quiet
  assert.deepEqual(win.calls, ['fullscreen:true', 'show', 'focus', 'send:power:resume', 'repaint'])
})
