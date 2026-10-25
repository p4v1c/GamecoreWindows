'use strict'
// The kiosk window covers its whole monitor, or Windows leaves the taskbar on top of it.
const assert = require('node:assert/strict')
const { test } = require('node:test')
const { createFullscreenGuard, MAX_CORRECTIONS, CORRECTION_WINDOW_MS } = require('../fullscreen.js')

const MONITOR = { x: 0, y: 0, width: 1920, height: 1080 }

function fakes({ bounds = { ...MONITOR, height: 1079 }, fullScreen = true, visible = true } = {}) {
  const win = {
    bounds, fullScreen, visible, listeners: new Map(), setBoundsCalls: [],
    isDestroyed: () => false,
    isVisible() { return this.visible },
    isFullScreen() { return this.fullScreen },
    setFullScreen(v) { this.fullScreen = v },
    getBounds() { return { ...this.bounds } },
    setBounds(b) { this.setBoundsCalls.push(b); this.bounds = { ...b } },
    on(event, fn) { this.listeners.set(event, fn) },
    once(event, fn) { this.listeners.set(event, fn) },
  }
  const screen = {
    listeners: new Map(),
    getDisplayMatching: () => ({ bounds: MONITOR }),
    on(event, fn) { this.listeners.set(event, fn) },
    removeListener(event) { this.listeners.delete(event) },
  }
  return { win, screen }
}

test('a window one pixel short of the monitor is put back on the whole monitor', () => {
  const { win, screen } = fakes()
  const guard = createFullscreenGuard({ win, screen, log: () => {} })
  assert.equal(guard.cover(), true)
  assert.deepEqual(win.bounds, MONITOR)
  assert.equal(guard.cover(), false, 'a window already in place is left alone')
})

test('a window out of fullscreen goes back into it', () => {
  const { win, screen } = fakes({ bounds: { ...MONITOR }, fullScreen: false })
  createFullscreenGuard({ win, screen, log: () => {} }).cover()
  assert.equal(win.fullScreen, true)
})

test('a hidden window is left alone: a game owns the screen', () => {
  const { win, screen } = fakes({ visible: false })
  assert.equal(createFullscreenGuard({ win, screen, log: () => {} }).cover(), false)
  assert.deepEqual(win.setBoundsCalls, [])
})

test('show, resize and display changes all re-assert the bounds', () => {
  const { win, screen } = fakes()
  createFullscreenGuard({ win, screen, log: () => {} }).watch()
  for (const event of ['show', 'resize', 'restore', 'leave-full-screen']) assert.ok(win.listeners.has(event), event)
  for (const event of ['display-metrics-changed', 'display-added', 'display-removed']) {
    assert.ok(screen.listeners.has(event), event)
  }
  screen.listeners.get('display-metrics-changed')()
  assert.deepEqual(win.bounds, MONITOR)
  win.listeners.get('closed')()
  assert.equal(screen.listeners.size, 0, 'a closed window keeps no display listener')
})

test('a monitor that refuses the size does not become a resize loop', () => {
  const { win, screen } = fakes()
  win.setBounds = function (b) { this.setBoundsCalls.push(b) }   // stays 1079 high
  let t = 0
  const guard = createFullscreenGuard({ win, screen, now: () => t, log: () => {} })
  for (let i = 0; i < MAX_CORRECTIONS * 3; i++) guard.cover()
  assert.equal(win.setBoundsCalls.length, MAX_CORRECTIONS)
  t += CORRECTION_WINDOW_MS
  guard.cover()
  assert.equal(win.setBoundsCalls.length, MAX_CORRECTIONS + 1)
})
