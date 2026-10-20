'use strict'
/**
 * The alert HUD: battery and controller toasts drawn over a running game.
 *
 * In game the kiosk window is hidden behind the emulator, so its React toast
 * is invisible. This small transparent, always-on-top, click-through window
 * shows the alert over anything that is not exclusive fullscreen, and never
 * takes the focus from the game.
 *
 * Dependencies are handed in, so the VM bench can load this without Electron.
 */
const hudContract = require('./hud-tokens.json')

const HUD_TOAST_MS = 10000

function createHud({ BrowserWindow, screen, timers = { setTimeout, clearTimeout } }) {
  let hudToastWindow = null
  let hudToastTimer  = null

  // title/body/label reach us from the renderer over IPC, and the renderer gets
  // them from WebSocket broadcasts — including /api/addons/notify, an open LAN
  // endpoint, and Bluetooth device names. Never interpolate them into HTML raw.
  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ))
  }

  // accent lands inside style="" — only ever use it as a plain color token
  function safeColor(c) {
    return /^#[0-9a-fA-F]{3,8}$/.test(String(c)) ? c : '#fbbf24'
  }

  // Both renderers interpret this small declarative contract. No theme code or
  // stylesheet crosses the IPC boundary, even if a LAN sender controls the data.
  function hudTokens(input) {
    const result = {}
    for (const [key, kind] of Object.entries(hudContract.tokens)) {
      const value = input?.[key]
      if (typeof value === 'string' && value.length <= 160 &&
          new RegExp(hudContract.patterns[kind]).test(value)) result[key] = value
    }
    // A light panel without severity overrides must still have readable text.
    const rgb = result.panel?.slice(1, 7).match(/../g)?.map(v => {
      const n = parseInt(v, 16) / 255
      return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4
    })
    if (rgb && 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] > 0.45) {
      return { ...hudContract.lightDefaults, ...result }
    }
    return result
  }

  // A known icon name draws the contract's SVG path; any other value (a theme's own
  // text icon) is escaped text, as before.
  function hudIcon(icon) {
    const d = hudContract.icons && Object.hasOwn(hudContract.icons, icon) ? hudContract.icons[icon] : null
    return d
      ? `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`
      : escHtml(icon)
  }

  function showHudToast({ icon = 'gamepad', title = '', body = '', accent = '#fbbf24', theme, tone } = {}) {
    icon = hudIcon(icon); title = escHtml(title); body = escHtml(body)
    const t = hudTokens(theme)
    const themed = Object.keys(t).length > 0
    accent = t[tone] || safeColor(accent)
    const html = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"></head><body style="margin:0;background:transparent;overflow:hidden;font-family:${t.font || 'sans-serif'}">
      <div style="display:flex;align-items:center;gap:14px;margin:8px;padding:14px 18px;border-radius:${t.radius || '14px'};
                  background:${t.panel || 'rgba(18,18,26,0.94)'};backdrop-filter:blur(${t.blur || '0px'});border:1px solid ${t.border || accent};box-shadow:0 8px 32px rgba(0,0,0,0.6)">
        <div style="width:40px;height:40px;flex-shrink:${themed ? 0 : 1};border-radius:10px;background:${accent.length === 7 ? accent + '33' : accent};display:flex;align-items:center;justify-content:center;font-size:20px;color:${accent}">${icon}</div>
        <div style="${themed ? 'min-width:0;overflow-wrap:anywhere' : ''}">
          <div style="font-size:${themed ? 20 : 14}px;font-weight:700;color:${accent}">${title}</div>
          <div style="font-size:${themed ? 18 : 13}px;color:${t.text || 'rgba(255,255,255,0.7)'};margin-top:3px">${body}</div>
        </div>
      </div></body></html>`

    if (hudToastTimer) { timers.clearTimeout(hudToastTimer); hudToastTimer = null }
    // destroy(), not close(): close() is a request that can be ignored — a
    // lingering HUD on screen is worse than a skipped fade-out.
    if (hudToastWindow) { hudToastWindow.destroy(); hudToastWindow = null }

    const { width } = screen.getPrimaryDisplay().workAreaSize
    const W = themed ? 560 : 440, H = themed ? 240 : 100
    hudToastWindow = new BrowserWindow({
      // The shipped themed headers are taller than the default 54px bar.
      // Keep their notifications below the header as well as over emulators.
      x: width - W - 24, y: themed ? 140 : 66, width: W, height: H,
      transparent: true, backgroundColor: '#00000000', frame: false,
      alwaysOnTop: true, skipTaskbar: true, focusable: false,
      resizable: false, hasShadow: false, show: false,
      webPreferences: { nodeIntegration: false, contextIsolation: true },
    })
    // Above a borderless fullscreen game. Nothing is drawn over an exclusive
    // fullscreen one: packs must ask their emulator for borderless.
    hudToastWindow.setAlwaysOnTop(true, 'screen-saver')
    hudToastWindow.setIgnoreMouseEvents(true)
    hudToastWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
    // showInactive(), never show(): taking the foreground pulls the game out
    // of fullscreen and moves the pad's input away from it.
    const win = hudToastWindow
    win.once('ready-to-show', () => {
      if (hudToastWindow !== win) return
      win.showInactive()
      win.moveTop()
    })
    hudToastWindow.on('closed', () => { hudToastWindow = null })

    hudToastTimer = timers.setTimeout(() => {
      hudToastTimer = null
      if (hudToastWindow) { hudToastWindow.destroy(); hudToastWindow = null }
    }, HUD_TOAST_MS)
  }

  function showBatteryToast({ level = 0, player = null, who = '', theme } = {}) {
    if (typeof level !== 'number' || !Number.isFinite(level) || level < 0 || level > 100) return
    const stage = hudContract.battery.find(s => level <= s.threshold) || hudContract.battery.at(-1)
    const title = String(who || (player ? `Controller ${player}` : 'Controller'))  // who: "Max’s controller"
    showHudToast({
      icon: 'gamepad', title: `${title} battery at ${Math.round(level)}%`,
      body: stage.message, accent: stage.color, theme, tone: `battery-${stage.threshold}`,
    })
  }

  function notifyController(data) {
    const d = data || {}
    const who = String(d.who || (d.player ? `Controller ${d.player}` : 'Controller'))

    // Systems this pad was NOT configured for. A pad can be recognised and still
    // be left out of one emulator, and until this branch existed that arrived as
    // the green "connected" toast — so the one console that ignored the pad was
    // the one thing the player was never told about.
    //
    // Same provenance rule as everything else reaching showHudToast: these are
    // catalogue labels relayed by the renderer, so they are text to escape and
    // never markup. Capped because the HUD is a fixed 440x100 window.
    const missing = Array.isArray(d.unconfigured)
      ? d.unconfigured.filter(s => typeof s === 'string').slice(0, 3)
      : []

    // Autoconfig is off, so this pad got nothing anywhere. First, and it names no
    // systems: listing nine of them truncated to three would read as a fault in
    // three consoles instead of one switch somebody flicked. This is also the one
    // moment the player can be told at all — they are in a game, the app window
    // is buried under the emulator, and the pad in their hands does not work.
    if (d.connected && Array.isArray(d.autoconfigOff) && d.autoconfigOff.length > 0) {
      showHudToast({
        icon: 'gamepad',
        title: `${who} was not configured`,
        body: 'Automatic controller setup is off (Settings → Controllers).',
        accent: '#fbbf24', theme: d.theme, tone: 'warning',
      })
      return
    }

    if (d.connected && missing.length > 0) {
      showHudToast({
        icon: 'warning',
        title: `${who} is not set up for ${missing.join(', ')}`,
        body: `It works elsewhere — ${missing.length === 1 ? 'that system' : 'those systems'} will not respond to it.`,
        accent: '#fbbf24', theme: d.theme, tone: 'warning',
      })
      return
    }

    showHudToast({
      icon: 'gamepad',
      title: d.connected ? `${who} connected` : `${who} disconnected`,
      body: d.label ? String(d.label) : '',
      accent: d.connected ? '#4ade80' : '#94a3b8', theme: d.theme,
      tone: d.connected ? 'connected' : 'disconnected',
    })
  }

  function register(ipcMain) {
    ipcMain.on('notify:battery', (_, data) => showBatteryToast(data || {}))
    ipcMain.on('notify:controller', (_, data) => notifyController(data))
  }

  return { showHudToast, showBatteryToast, hudTokens, register }
}

module.exports = { createHud, HUD_TOAST_MS }
