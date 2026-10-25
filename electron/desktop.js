'use strict'
/**
 * Settings → Desktop mode: hand the screen back to the Windows desktop.
 *
 * The shell only tells the backend and quits with code 0. gamecore-session
 * reads that exit as Desktop mode and has the desktop opened: by gamecore-shell
 * (the console user's shell, which starts Explorer as the desktop, unelevated),
 * or by starting Explorer itself where Explorer is still the shell. Started
 * from here, Explorer would be elevated and, under the GameCore shell, a folder
 * window instead of the desktop. Coming back is the GameCore shortcut.
 *
 * Dependencies are handed in, so the VM bench can load this without Electron.
 */

/** How long telling the backend may hold the exit. The player asked to leave. */
const LEAVE_DEADLINE_MS = 5000

/** Tell the backend (`announce`), then call `quit` exactly once, within the deadline. */
function leaveToDesktop({ quit, announce = () => {}, timers = { setTimeout }, log = console.warn }) {
  let left = false
  const leave = () => { if (!left) { left = true; quit() } }
  timers.setTimeout(leave, LEAVE_DEADLINE_MS)
  return Promise.resolve()
    .then(announce)
    .catch((err) => log('[desktop] could not tell the backend:', err.message))
    .finally(leave)
}

module.exports = { leaveToDesktop, LEAVE_DEADLINE_MS }
