'use strict'
/**
 * Keep the kiosk window over the whole monitor it is on.
 *
 * Windows hides the taskbar only for a window that covers the entire monitor.
 * Electron's frameless fullscreen comes back one pixel short after a
 * fullscreen round trip (1920x1079, measured on the installed PC), and the
 * taskbar then stayed drawn over the interface. So the monitor's bounds are
 * re-asserted whenever they or the window can have changed.
 *
 * Dependencies are handed in, so the VM bench can load this without Electron.
 */

/** Corrections allowed per window of time: a monitor that refuses must not become a resize loop. */
const MAX_CORRECTIONS = 5
const CORRECTION_WINDOW_MS = 10000

function sameRect(a, b) {
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height
}

/**
 * `cover()` puts `win` back on its monitor's full bounds when it is not there.
 * `watch()` calls it on show, resize and every display change. Returns both.
 */
function createFullscreenGuard({ win, screen, now = Date.now, log = console.warn }) {
  let corrections = []

  function cover() {
    if (!win || win.isDestroyed() || !win.isVisible()) return false
    const target = screen.getDisplayMatching(win.getBounds()).bounds
    if (sameRect(win.getBounds(), target) && win.isFullScreen()) return false
    const t = now()
    corrections = corrections.filter((at) => t - at < CORRECTION_WINDOW_MS)
    if (corrections.length >= MAX_CORRECTIONS) return false
    corrections.push(t)
    log('[fullscreen] window', JSON.stringify(win.getBounds()), 'put back on', JSON.stringify(target))
    if (!win.isFullScreen()) win.setFullScreen(true)
    win.setBounds(target)
    return true
  }

  function watch() {
    for (const event of ['show', 'resize', 'restore', 'leave-full-screen']) win.on(event, cover)
    const onDisplay = () => cover()
    for (const event of ['display-metrics-changed', 'display-added', 'display-removed']) {
      screen.on(event, onDisplay)
    }
    win.once('closed', () => {
      for (const event of ['display-metrics-changed', 'display-added', 'display-removed']) {
        screen.removeListener(event, onDisplay)
      }
    })
  }

  return { cover, watch }
}

module.exports = { createFullscreenGuard, MAX_CORRECTIONS, CORRECTION_WINDOW_MS }
