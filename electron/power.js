'use strict'
/**
 * Power, from the shell's side: the two commands the renderer asks for, and
 * the window's half of a wake from sleep.
 *
 * Rest mode itself is the backend's (services/rest_mode.py): it closes every
 * game before suspending, which the shell cannot see. After a wake, the
 * backend puts the screen, pads and display mode back and tells the renderer;
 * what is left here is this window. A suspend can leave a kiosk window
 * unfocused or out of fullscreen, where the pad then drives nothing, or
 * showing the frame from before the sleep with parts of it gone black.
 *
 * Dependencies are handed in, so the VM bench can load this without Electron.
 */

/** Windows' own shutdown.exe; the console user holds the shutdown right. */
const REBOOT_COMMAND   = 'shutdown /r /t 0'
const SHUTDOWN_COMMAND = 'shutdown /s /t 0'

function registerPowerIpc({ ipcMain, exec }) {
  ipcMain.on('system:reboot',   () => exec(REBOOT_COMMAND))
  ipcMain.on('system:shutdown', () => exec(SHUTDOWN_COMMAND))
}

/**
 * Re-assert the main window after every resume. `getWindow` and
 * `sessionOwnsScreen` are read at the time of the wake, not at registration.
 */
function watchResume({ powerMonitor, getWindow, sessionOwnsScreen, log = console.log }) {
  if (!powerMonitor || typeof powerMonitor.on !== 'function') return false
  powerMonitor.on('resume', () => {
    const win = getWindow()
    if (!win || sessionOwnsScreen()) return
    log('[power] resumed: putting the interface back in front')
    try {
      if (!win.isFullScreen()) win.setFullScreen(true)
      win.show()
      win.focus()
      // The renderer covers itself in black; a full repaint replaces the
      // frame from before the sleep, which amdgpu brings back half lost.
      win.webContents.send('power:resume')
      win.webContents.invalidate()
    } catch { /* the window is going away */ }
  })
  return true
}

/** Both, from main.js: the resume hook waits for app ready, before which
 *  powerMonitor cannot be subscribed to. */
function register({ app, ipcMain, exec, powerMonitor, getWindow, sessionOwnsScreen }) {
  registerPowerIpc({ ipcMain, exec })
  app.whenReady().then(() => watchResume({ powerMonitor, getWindow, sessionOwnsScreen }))
}

module.exports = { register, registerPowerIpc, watchResume }
