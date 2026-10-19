#!/usr/bin/env node
// Measures what the player actually sees: renders a screen in headless
// Chromium, then checks every visible text and SVG icon for contrast against
// the pixels behind it and for TV size.
//
//   node legibility-audit.mjs [--url http://127.0.0.1:8766/] [--theme shelf|default]
//                             [--press confirm,dpad-down] [--init stub.js] [--eval "js;;js"]
//                             [--all] [--json] [--shot out.png [--crops] [--clip x,y,w,h]]
//
// --theme switches the active theme through the API first (dev server only,
// see devserve.py). --press sends pad events before the scan to reach a
// screen, by their gp:* name: confirm (✕), back (○), x (□), menu (Start),
// power (Select), l1, r1, dpad-up/down/left/right. --shot saves what was audited;
// --crops adds its four quarters at 2x (out-q1..q4.png) and --clip one region at
// 2x (out-clip.png), for a close visual review.
// Exit 1 when something fails.
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const arg = (name, def) => {
  const i = process.argv.indexOf(`--${name}`)
  return i > 0 ? process.argv[i + 1] : def
}
const URL_ = arg('url', 'http://127.0.0.1:8766/')
const THEME = arg('theme')
const KEYS = (arg('press', '') || '').split(',').filter(Boolean)
const SHOW_ALL = process.argv.includes('--all')
const AS_JSON = process.argv.includes('--json')
const SHOT = arg('shot')
const CROPS = process.argv.includes('--crops')
const CLIP = arg('clip')?.split(',').map(Number)
// --init: a script run before the page's own (e.g. a fake navigator.getGamepads);
// --eval: expressions run after --press, `;;`-separated, 900 ms apart.
const INIT = arg('init')
const EVALS = (arg('eval', '') || '').split(';;').filter(Boolean)
const PORT = 9333
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

if (THEME) {
  const origin = new URL(URL_).origin
  const res = await fetch(`${origin}/api/themes/active`, {
    method: 'POST', headers: { 'content-type': 'application/json', origin },
    body: JSON.stringify({ id: THEME === 'default' ? null : THEME }),
  })
  if (!res.ok) throw new Error(`theme switch failed: ${res.status}`)
}

// Windows has no `chromium` on PATH: Chrome when installed, else the Edge
// every Windows 11 ships. GAMECORE_CHROME names another one.
function browserPath() {
  if (process.env.GAMECORE_CHROME) return process.env.GAMECORE_CHROME
  if (process.platform !== 'win32') return 'chromium'
  const roots = [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA]
  const rel = [join('Google', 'Chrome', 'Application', 'chrome.exe'),
    join('Microsoft', 'Edge', 'Application', 'msedge.exe')]
  for (const r of rel) for (const root of roots) {
    if (root && existsSync(join(root, r))) return join(root, r)
  }
  throw new Error('no Chrome or Edge found; set GAMECORE_CHROME')
}

const profile = mkdtempSync(join(tmpdir(), 'legibility-'))
// Headless Chromium still plays through the box's speakers and reads its real
// pads: muted, and blind to pads unless --init fakes some (fake-pads.js).
const chrome = spawn(browserPath(), ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, '--window-size=1920,1080', '--hide-scrollbars', '--mute-audio', 'about:blank'],
{ stdio: 'ignore' })

let ws
try {
  let target
  for (let i = 0; i < 50 && !target; i++) {
    await sleep(200)
    target = await fetch(`http://127.0.0.1:${PORT}/json`).then((r) => r.json())
      .then((l) => l.find((t) => t.type === 'page')).catch(() => null)
  }
  ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j })
  let seq = 0
  const pending = new Map()
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data)
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id) }
  }
  const send = (method, params = {}) => new Promise((r) => {
    const id = ++seq
    pending.set(id, r)
    ws.send(JSON.stringify({ id, method, params }))
  })
  // Wait until the screen stops changing (splash, theme load, entry motion).
  const settle = async () => {
    let last = ''
    for (let i = 0; i < 20; i++) {
      await sleep(i ? 1000 : 3000)
      const now = (await send('Runtime.evaluate', { expression: 'document.body ? document.body.innerText : ""',
        returnByValue: true })).result.result.value
      if (now && now === last) return
      last = now
    }
  }

  await send('Page.enable')
  // The box renders at 1920x1080; the headless window alone is a bit shorter.
  await send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false })
  await send('Page.addScriptToEvaluateOnNewDocument', { source: 'navigator.getGamepads = () => []' })
  if (INIT) await send('Page.addScriptToEvaluateOnNewDocument', { source: readFileSync(INIT, 'utf8') })
  await send('Page.navigate', { url: URL_ })
  await settle()
  // The same window events useGamepad emits and every theme listens to.
  for (const key of KEYS) {
    await send('Runtime.evaluate', { expression: `window.dispatchEvent(new CustomEvent('gp:${key}'))` })
    await sleep(900)
  }
  for (const expression of EVALS) {
    await send('Runtime.evaluate', { expression })
    await sleep(900)
  }
  await settle()
  const run = async (fn, ...args) => (await send('Runtime.evaluate', {
    expression: `(${fn})(...${JSON.stringify(args)})`, returnByValue: true, awaitPromise: true,
  })).result.result.value
  if (SHOT) {
    const seen = await send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(SHOT, Buffer.from(seen.result.data, 'base64'))
    if (CROPS) {
      const quarters = [[0, 0], [960, 0], [0, 540], [960, 540]]
      for (const [i, [x, y]] of quarters.entries()) {
        const q = await send('Page.captureScreenshot', { format: 'png',
          clip: { x, y, width: 960, height: 540, scale: 2 } })
        writeFileSync(SHOT.replace(/\.png$/, '') + `-q${i + 1}.png`, Buffer.from(q.result.data, 'base64'))
      }
    }
    if (CLIP?.length === 4) {
      const [x, y, width, height] = CLIP
      const c = await send('Page.captureScreenshot', { format: 'png', clip: { x, y, width, height, scale: 2 } })
      writeFileSync(SHOT.replace(/\.png$/, '') + '-clip.png', Buffer.from(c.result.data, 'base64'))
    }
  }
  await run(collect)
  await sleep(200)                 // hidden text and icons repaint
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  const rows = await run(measure, `data:image/png;base64,${shot.result.data}`)
  report(rows)
  process.exitCode = rows.some((r) => r.fail) ? 1 : 0
} finally {
  ws?.close()
  chrome.kill()
  await sleep(300)
  rmSync(profile, { recursive: true, force: true })
}

function report(rows) {
  const shown = SHOW_ALL ? rows : rows.filter((r) => r.fail)
  const warns = rows.filter((r) => r.warn).length
  if (AS_JSON) { console.log(JSON.stringify(shown, null, 2)); return }
  const fails = rows.filter((r) => r.fail).length
  console.log(`${THEME ?? 'current theme'} ${KEYS.join(' ')}: ${rows.length} checked, ${fails} failing,`
    + ` ${warns} small (--all lists them)`)
  for (const r of shown.sort((a, b) => a.ratio - b.ratio)) {
    console.log(`${r.fail ? 'FAIL' : r.warn ? 'warn' : 'ok  '} ${r.kind.padEnd(4)} ${String(r.ratio).padStart(5)}:1 need ${r.need}`
      + ` ${String(r.size).padStart(4)}px ${r.fg} on ${r.bg}${r.busy ? ' (busy backdrop)' : ''}`
      + `  ${r.where}  "${r.text}"${r.why ? `  [${r.why}]` : ''}`)
  }
}

// Page side, pass 1: list what the player reads (text runs, SVG shapes)
// with their colour, then hide them so the screenshot shows only what is
// behind. Plain functions: they are serialised into the page.
function collect() {
  const parse = (c) => {
    let m = c.match(/rgba?\(([^)]+)\)/)
    if (m) { const p = m[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat); return [p[0], p[1], p[2], p[3] ?? 1] }
    m = c.match(/color\(srgb ([^)]+)\)/)
    if (m) { const p = m[1].split(/[\s/]+/).filter(Boolean).map(parseFloat); return [p[0] * 255, p[1] * 255, p[2] * 255, p[3] ?? 1] }
    return null
  }
  const opacityOf = (el) => {
    let o = 1
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity)
    return o
  }
  const where = (el) => {
    const parts = []
    for (let e = el; e && parts.length < 3 && e !== document.body; e = e.parentElement) {
      const cls = typeof e.className === 'string' ? e.className.trim().split(/\s+/)[0] : ''
      parts.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : ''))
    }
    return parts.join(' > ')
  }
  const onScreen = (r) => r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0
    && r.top < innerHeight && r.left < innerWidth
  const box = (r) => [r.left, r.top, r.width, r.height].map(Math.round)
  const items = []
  // What is on top at the item's centre: decoration there is a failure, any
  // other layer (a modal over the home screen) means the item is not seen.
  const force = document.createElement('style')
  force.textContent = '* { pointer-events: auto !important }'
  document.head.append(force)
  const paints = (e) => {
    if (['IMG', 'VIDEO', 'CANVAS', 'svg', 'path', 'circle', 'rect', 'ellipse', 'polygon'].includes(e.tagName)) return true
    const cs = getComputedStyle(e)
    if (cs.visibility !== 'visible' || parseFloat(cs.opacity) === 0) return false
    const bg = parse(cs.backgroundColor)
    return (bg && bg[3] > 0.05) || cs.backgroundImage !== 'none'
  }
  // Decoration in the way = a small shape from a screen-sized aria-hidden
  // layer (shells on the beach) with nothing else painted between it and the
  // item. Anything else painted on top means another screen covers the item.
  const area = (x) => { const d = x.getBoundingClientRect(); return d.width * d.height }
  const isDecor = (e) => {
    const layer = e.closest('[aria-hidden="true"]')
    const screen = innerWidth * innerHeight
    return Boolean(layer) && area(e) < 0.25 * screen && area(layer) > 0.5 * screen
  }
  const cover = (el, r) => {
    let decor = false
    for (const e of document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2)) {
      if (el.contains(e) || e.contains(el)) return decor ? 'decor' : null
      if (!paints(e)) continue
      if (!isDecor(e)) return 'hidden'
      decor = true
    }
    return decor ? 'decor' : null
  }

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.data.trim()
    const el = n.parentElement
    if (!text || !el || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue
    if (el.closest('[aria-hidden="true"]')) continue     // decoration carries no information
    if (el.closest(':disabled, [aria-disabled="true"]')) continue   // inactive: exempt, as in WCAG
    const cs = getComputedStyle(el)
    if (cs.visibility !== 'visible') continue
    const range = document.createRange()
    range.selectNodeContents(n)
    const r = range.getBoundingClientRect()
    const fg = parse(cs.color)
    if (!onScreen(r) || !fg) continue
    fg[3] *= opacityOf(el)
    if (fg[3] < 0.05) continue
    const size = parseFloat(cs.fontSize)
    const large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700)
    const covered = cover(el, r)
    if (covered === 'hidden') continue
    items.push({ kind: 'text', fg, box: box(r), size, need: large ? 3 : 4.5, where: where(el), text: text.slice(0, 40),
      covered: covered === 'decor' })
  }

  // Shapes under 0.5 opacity are deliberate de-emphasis (an unlit d-pad arm).
  // Pure decoration sits in an aria-hidden container and is skipped; an icon
  // that is aria-hidden itself (next to its label) is still checked.
  for (const svg of document.querySelectorAll('svg')) {
    if (svg.parentElement?.closest('[aria-hidden="true"]')) continue
    const r = svg.getBoundingClientRect()
    if (!onScreen(r) || getComputedStyle(svg).visibility !== 'visible') continue
    const covered = cover(svg, r)
    if (covered === 'hidden') continue
    const alpha = opacityOf(svg)
    for (const shape of svg.querySelectorAll('path, circle, rect, line, polyline, polygon, ellipse')) {
      const cs = getComputedStyle(shape)
      // An unset fill computes to black; only count fills the markup asks for.
      const filled = shape.closest('[fill]') || shape.style.fill
      for (const [paint, op] of [[cs.stroke, cs.strokeOpacity], [filled ? cs.fill : 'none', cs.fillOpacity]]) {
        const fg = parse(paint)
        if (!fg || fg[3] === 0) continue
        fg[3] *= parseFloat(op) * alpha
        if (fg[3] < 0.5) continue
        items.push({ kind: 'icon', fg, box: box(r), size: Math.round(r.height), need: 3, where: where(svg),
          text: svg.getAttribute('aria-label') || '', covered: covered === 'decor' })
      }
    }
  }

  force.remove()
  window.__legibility = items
  const hide = document.createElement('style')
  hide.id = '__legibility'
  hide.textContent = `*, *::before, *::after { color: transparent !important; -webkit-text-fill-color: transparent !important;
    text-shadow: none !important; transition: none !important; animation-play-state: paused !important; caret-color: transparent !important }
    svg { visibility: hidden !important }`
  document.head.append(hide)
}

// Pass 2: sample the screenshot under each item. The ratio kept is the 10th
// percentile over the box, so a busy or patterned backdrop counts at its
// worst common pixel, not its average.
async function measure(png) {
  const MIN_PX = 14            // below this nobody reads it from the couch at 1080p
  const WARN_PX = 16           // fine up close, small on a TV: warn
  document.getElementById('__legibility')?.remove()
  const img = new Image()
  img.src = png
  await img.decode()
  const cv = document.createElement('canvas')
  cv.width = img.width
  cv.height = img.height
  const ctx = cv.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(img, 0, 0)
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
  const over = (top, under) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3]))
  const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')

  const rows = []
  const seen = new Set()
  for (const it of window.__legibility) {
    const [x, y, w, h] = it.box
    const cx = Math.max(0, x), cy = Math.max(0, y)
    const cw = Math.min(cv.width - cx, w + Math.min(0, x)), ch = Math.min(cv.height - cy, h + Math.min(0, y))
    if (cw < 1 || ch < 1) continue
    const data = ctx.getImageData(cx, cy, cw, ch).data
    const step = Math.max(1, Math.floor(Math.sqrt((cw * ch) / 400))) * 4   // whole RGBA pixels
    const samples = []
    for (let i = 0; i < data.length; i += step) {
      const bg = [data[i], data[i + 1], data[i + 2]]
      samples.push([ratio(over(it.fg, bg), bg), bg])
    }
    samples.sort((a, b) => a[0] - b[0])
    const [worst, bg] = samples[Math.floor(samples.length * 0.1)]
    const busy = samples[samples.length - 1][0] / samples[0][0] > 1.5
    const c = Math.round(worst * 100) / 100
    const small = it.kind === 'text' && it.size < MIN_PX
    const why = [c < it.need && 'contrast', small && 'size', it.covered && 'under decoration'].filter(Boolean).join(', ')
    const warn = !why && it.kind === 'text' && it.size < WARN_PX
    const fg = hex(it.fg) + (it.fg[3] < 1 ? `@${it.fg[3].toFixed(2)}` : '')
    const row = { kind: it.kind, ratio: c, need: it.need, size: Math.round(it.size * 10) / 10, fg, bg: hex(bg), busy,
      where: it.where, text: it.text, fail: Boolean(why), warn, why: why || (warn ? 'small for TV' : '') }
    const key = [row.kind, row.where, row.fg, row.bg, row.size, row.text].join('|')
    if (!seen.has(key)) { seen.add(key); rows.push(row) }
  }
  return rows
}
