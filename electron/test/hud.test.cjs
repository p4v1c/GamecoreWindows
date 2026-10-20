const { test } = require('node:test')
const assert = require('node:assert/strict')
const rig = require('./hud-rig.cjs')
const fs = require('node:fs')
const path = require('node:path')
const readCss = require('./css-bundle.cjs')
const contract = require('../hud-tokens.json')

test('missing and entirely invalid tokens preserve the original fallback', () => {
  const r = rig()
  r.context.showHudToast({ title: 'Connected', accent: '#4ade80' })
  const first = r.windows[0]
  for (const value of ['red;}</style><script>', 'url(javascript:alert(1))', '"Outfit"', '#12345', '33px', '9px;']) {
    r.context.showHudToast({ title: 'Connected', accent: '#4ade80', theme: Object.fromEntries(Object.keys(contract.tokens).map(k => [k, value])) })
    assert.equal(r.windows.at(-1).html, first.html)
    assert.equal(r.windows.at(-1).options.height, 100)
  }
  assert.match(first.html, /background:rgba\(18,18,26,0.94\)/)
  assert.match(first.html, /border-radius:14px/)
  assert.equal(first.options.width, 440)
})

test('IPC tokens are allowlisted and text is escaped, even for controller names and systems', () => {
  const r = rig()
  r.ipc.get('notify:controller')(null, { player: '<script>', connected: true,
    unconfigured: ['</div><script>alert(1)</script>'],
    theme: { panel: '#fffdf7', font: 'Arial, sans-serif', radius: '22px', blur: '18px', text: 'url(javascript:x)', script: '<script>' } })
  const w = r.windows[0]
  assert.match(w.html, /background:#fffdf7/)
  assert.match(w.html, /font-family:Arial, sans-serif/)
  assert.match(w.html, /color:#173039/)
  assert.match(w.html, /&lt;script&gt;/)
  assert.doesNotMatch(w.html, /<script>|javascript:/)
  assert.match(w.html, /default-src 'none'/)
  assert.equal(w.options.focusable, false)
  assert.equal(w.options.alwaysOnTop, true)
  assert.equal(w.clickThrough, true)
  assert.equal(w.options.y, 140)
})

test('each battery stage has distinct wording and its own overridable accent', () => {
  for (const stage of contract.battery) {
    const r = rig()
    r.ipc.get('notify:battery')(null, { level: stage.threshold, player: 1, theme: { [`battery-${stage.threshold}`]: '#abcdef' } })
    assert.match(r.windows[0].html, new RegExp(`battery at ${stage.threshold}%`))
    assert.ok(r.windows[0].html.includes(stage.message))
    assert.match(r.windows[0].html, /color:#abcdef/)
    assert.equal([...r.timers.values()][0].ms, 10000)
  }
  const r = rig()
  for (const level of [-1, 101, NaN, Infinity, '<script>']) r.context.showBatteryToast({ level })
  assert.equal(r.windows.length, 0)
  r.context.showBatteryToast({ level: 4 })
  r.context.showBatteryToast({ level: 9 })
  assert.equal(r.windows[0].destroyed, true)
  assert.equal(r.timers.size, 1)
  assert.match(r.windows[1].html, /Battery very low/)
})

function luminance(hex) {
  const rgb = hex.slice(1, 7).match(/../g).map(v => {
    const n = parseInt(v, 16) / 255
    return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4
  })
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
}

test('shipped text and severity colors meet AA on their panels', () => {
  for (const name of ['orbit', 'shelf', 'summer']) {
    const css = readCss(path.join(__dirname, `../../config/themes/${name}/theme.css`))
    const theme = Object.fromEntries([...css.matchAll(/--gc-hud-([\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2]]))
    const tokens = rig().context.hudTokens(theme)
    for (const key of ['text', 'connected', 'disconnected', 'warning', 'battery-25', 'battery-15', 'battery-10', 'battery-5']) {
      const a = luminance(tokens.panel), b = luminance(tokens[key])
      assert.ok((Math.max(a, b) + .05) / (Math.min(a, b) + .05) >= 4.5, `${name}: ${key}`)
    }
  }
})

test('the toast never takes the focus from the game', () => {
  const r = rig()
  r.context.showHudToast({ title: 'Connected', accent: '#4ade80' })
  const w = r.windows[0]
  assert.equal(w.options.show, false, 'a window shown at creation is activated')
  assert.equal(w.options.focusable, false)
  assert.equal(w.options.skipTaskbar, true)
  assert.equal(w.clickThrough, true)
  assert.deepEqual(w.topmost, { flag: true, level: 'screen-saver' })
  w.readyToShow()
  assert.deepEqual(w.calls, ['showInactive', 'moveTop'])
})

test('a toast replaced before it painted is not shown', () => {
  const r = rig()
  r.context.showHudToast({ title: 'First' })
  r.context.showHudToast({ title: 'Second' })
  r.windows[0].readyToShow()
  assert.deepEqual(r.windows[0].calls, [])
  r.windows[1].readyToShow()
  assert.deepEqual(r.windows[1].calls, ['showInactive', 'moveTop'])
})
