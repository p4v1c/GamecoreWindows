'use strict'
/**
 * Settings → Desktop mode: hand the screen back to the Windows desktop.
 *
 * Explorer may not be running in the console session (it is the user's shell,
 * but it can have been closed), so leaving starts it when it is not; GameCore
 * then quits. Coming back is the logon task at the next sign-in, or running
 * install\bin\gamecore-session again.
 *
 * Dependencies are handed in, so the VM bench can load this without Electron.
 */

/** How long the Explorer check may hold the exit. The player asked to leave. */
const LEAVE_DEADLINE_MS = 5000

/** Whether an explorer.exe runs in this session (tasklist only lists its own). */
function explorerRunning(execFile) {
  return new Promise((resolve) => {
    execFile('tasklist', ['/FI', 'IMAGENAME eq explorer.exe', '/FO', 'CSV', '/NH'],
      { windowsHide: true },
      (err, stdout) => resolve(!err && /"explorer\.exe"/i.test(String(stdout))))
  })
}

/**
 * Tell the backend (`announce`), start Explorer if needed, then call `quit`
 * exactly once. The deadline covers all of it.
 */
function leaveToDesktop({ execFile, spawn, quit, announce = () => {},
  timers = { setTimeout }, log = console.warn }) {
  let left = false
  const leave = () => { if (!left) { left = true; quit() } }
  timers.setTimeout(leave, LEAVE_DEADLINE_MS)
  return Promise.resolve()
    .then(announce)
    .catch((err) => log('[desktop] could not tell the backend:', err.message))
    .then(() => explorerRunning(execFile))
    .then((running) => {
      if (running) return
      spawn('explorer.exe', [], { detached: true, stdio: 'ignore' }).unref()
    })
    .catch((err) => log('[desktop] could not start Explorer:', err.message))
    .finally(leave)
}

module.exports = { leaveToDesktop, explorerRunning, LEAVE_DEADLINE_MS }
