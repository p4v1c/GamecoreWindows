/**
 * Settings → Bluetooth: one card, Paired on the left, Nearby on the right.
 * Bare markup; the router pairs, trusts, then connects, in that safe order.
 *
 * Not drawn, because nothing reports them: an ON/OFF switch (no route turns
 * the adapter off), per-device battery (sysinfo pads carry no MAC — matching
 * by name would be a guess), RSSI, device class.
 */
// Mirrors SCAN_SECS in the router, so the screen can show how long it takes.
const SCAN_SECS = 10
const SCAN_PATIENCE_MS = (SCAN_SECS + 4) * 1000

import { asList, follow } from './list.js'

export const createBluetoothPage = (sdk, useSlow) => {
  const { html, useState, useEffect, useRef, React } = sdk.ui
  const Fragment = React.Fragment

  // `onLeft` is what ← out of the Paired column does; the screen passes a
  // no-op in Orbit's index layout, where ○ is the only way back up.
  return ({ active, onLeave, onLeft, seed }) => {
    // Seeded from the rail. The settings screen already fetched the paired list
    // to put "2 connected" at the end of this row, so the page opens with it
    // rather than fetching the same thing again and showing an empty card while
    // it waits.
    const [paired, setPaired] = useState(() => seed || [])
    const [gotPaired, setGotPaired] = useState(() => !!seed)
    const [nearby, setNearby] = useState([])
    const [scanning, setScanning] = useState(false)
    const [busy, setBusy] = useState('')      // mac being worked on
    const [msg, setMsg] = useState('')
    const [col, setCol] = useState('paired')
    const [idx, setIdx] = useState(0)
    // On a paired row, which of its two targets the cursor is on: 0 the row
    // itself (✕ connects or disconnects), 1 its Forget button. And the MAC
    // whose Forget has had its first press: forgetting takes two, like every
    // destructive row in these settings, and moving the cursor disarms it.
    const [side, setSide] = useState(0)
    const [armed, setArmed] = useState('')
    // Nothing is wrong yet — it is just taking a while. The scan is ten seconds
    // by design, so its patience is longer than the paired list's; a "still
    // working" that fires during a normal scan would be crying wolf.
    const slowPaired = useSlow(!gotPaired, 2500)
    const slowScan = useSlow(scanning, SCAN_PATIENCE_MS)

    // Each column scrolls on its own and the heading above them never moves:
    // a long Nearby list used to push the whole page, and a pad's cursor could
    // walk off the bottom of the screen with nothing following it.
    const mainRef = useRef(null)
    useEffect(() => {
      if (!active) return
      follow(mainRef.current?.querySelector(`.gcs-bt-col[data-col="${col}"] .gcs-bt-row[data-here="1"]`), idx === 0)
    }, [col, idx, active, paired.length, nearby.length])

    const stateRef = useRef({ col, idx, paired, nearby, side, armed })
    useEffect(() => { stateRef.current = { col, idx, paired, nearby, side, armed } },
      [col, idx, paired, nearby, side, armed])
    // Disarmed when the cursor is no longer on the Forget button it armed.
    // Compared against the target rather than fired on every move: a click
    // moves the cursor AND arms in one go, and clearing on any change would
    // disarm it in the same tick — the same trap rows.js documents.
    useEffect(() => {
      const d = col === 'paired' && side === 1 ? paired[idx] : null
      if (armed && (!d || d.mac !== armed)) setArmed('')
    }, [col, idx, side, armed, paired])

    const loadPaired = () => sdk.api.bluetooth.devices()
      .then((r) => setPaired(asList(r)))
      .catch(() => {})
      // Loaded means "the question has been answered", including answered
      // badly. An adapter that is off has no paired devices and no error to
      // show; leaving this false would spin for ever on a box with no radio.
      .finally(() => setGotPaired(true))

    // The seed can arrive AFTER this page has mounted — the rail's requests and
    // a player pressing straight down to Bluetooth are in a race, and the seed
    // loses it whenever the box is slow, which is exactly when it matters.
    // Adopted whenever it turns up, until the page's own answer supersedes it.
    useEffect(() => {
      if (seed && !gotPaired) { setPaired(seed); setGotPaired(true) }
    }, [seed, gotPaired])

    useEffect(() => { loadPaired() }, [])

    // One scan on arrival. It blocks for SCAN_SECS on the other end, so it is
    // not on a timer: re-running it every few seconds would keep the adapter
    // permanently in discovery and make connecting to anything slower.
    useEffect(() => {
      let alive = true
      setScanning(true)
      sdk.api.bluetooth.scan()
        .then((r) => { if (alive) setNearby(r.found || []) })
        .catch(() => {})
        .finally(() => { if (alive) setScanning(false) })
      return () => { alive = false }
    }, [])

    const rescan = () => {
      if (scanning) return
      setScanning(true); setMsg('')
      sdk.api.bluetooth.scan()
        .then((r) => setNearby(asList(r && r.found)))
        .catch(() => setMsg('Could not scan.'))
        .finally(() => setScanning(false))
    }

    const act = (d, kind) => {
      if (busy) return
      setBusy(d.mac); setMsg('')
      const done = (m) => { setMsg(m); loadPaired(); setBusy('') }
      if (kind === 'pair') {
        sdk.api.bluetooth.pair(d.mac)
          .then((r) => {
            // Paired-but-not-connected is a real, useful state and the router
            // says so rather than calling it a failure. Echo its words.
            if (r.ok) setNearby((n) => n.filter((x) => x.mac !== d.mac))
            done(r.message || (r.ok ? 'Paired.' : 'Pairing failed.'))
          })
          .catch(() => done('Could not reach the backend.'))
        return
      }
      const call = d.connected ? sdk.api.bluetooth.disconnect(d.mac)
                               : sdk.api.bluetooth.connect(d.mac)
      call.then((r) => done(r.message || (r.ok ? 'Done.' : 'Failed.')))
          .catch(() => done('Could not reach the backend.'))
    }

    // Unpair and forget: the device has to be paired again before it can
    // reconnect. The router only says yes once Windows' paired list no longer
    // holds it, and the list shown afterwards is the one read back from the
    // adapter, never this page's guess.
    const forget = (d) => {
      if (busy) return
      if (stateRef.current.armed !== d.mac) { setArmed(d.mac); return }
      setArmed(''); setBusy(d.mac); setMsg('')
      sdk.api.bluetooth.remove(d.mac)
        .then((r) => setMsg(r && r.ok ? `${d.name} forgotten. Pair it again to reconnect.`
                                       : ((r && r.message) || `${d.name} is still paired.`)))
        .catch(() => setMsg('Could not reach the backend.'))
        .finally(() => { setBusy(''); setSide(0); loadPaired() })
    }

    useEffect(() => {
      if (!active) return
      const len = (c) => Math.max(1, (c === 'paired' ? stateRef.current.paired
                                                     : stateRef.current.nearby).length)
      const offs = [
        sdk.input.onGp('gp:dpad-up', () => {
          sdk.system.playSound('move'); setSide(0)
          setIdx((i) => (i - 1 + len(stateRef.current.col)) % len(stateRef.current.col))
        }),
        sdk.input.onGp('gp:dpad-down', () => {
          sdk.system.playSound('move'); setSide(0)
          setIdx((i) => (i + 1) % len(stateRef.current.col))
        }),
        // Left walks back one step at a time: off a Forget button onto its row,
        // out of Nearby into Paired, and out of Paired to the rail. Right is
        // the same walk the other way — a paired row's Forget button sits
        // between it and the Nearby column, exactly where it is drawn.
        sdk.input.onGp('gp:dpad-left', () => {
          const s = stateRef.current
          if (s.col === 'paired' && s.side === 1) { sdk.system.playSound('move'); setSide(0) }
          else if (s.col === 'nearby') { sdk.system.playSound('move'); setCol('paired'); setIdx(0) }
          else (onLeft || onLeave)()
        }),
        sdk.input.onGp('gp:dpad-right', () => {
          const s = stateRef.current
          if (s.col === 'paired' && s.side === 0 && s.paired[s.idx]) {
            sdk.system.playSound('move'); setSide(1)
          } else if (s.col === 'paired' && s.nearby.length) {
            sdk.system.playSound('move'); setSide(0); setCol('nearby'); setIdx(0)
          }
        }),
        sdk.input.onGp('gp:confirm', () => {
          const s = stateRef.current
          const d = (s.col === 'paired' ? s.paired : s.nearby)[s.idx]
          if (!d) return
          if (s.col === 'paired' && s.side === 1) forget(d)
          else act(d, s.col === 'paired' ? 'toggle' : 'pair')
        }),
        sdk.input.onGp('gp:back', onLeave),
      ]
      return () => offs.forEach((off) => off())
    }, [active, onLeave, onLeft, busy, scanning])

    const row = (d, i, kind) => {
      const here = active && col === kind && idx === i
      const on = here && (kind !== 'paired' || side === 0)
      const onForget = here && kind === 'paired' && side === 1
      const working = busy === d.mac
      return html`
        <div key=${d.mac} class="gcs-bt-row" data-on=${on ? '1' : '0'} data-here=${here ? '1' : '0'}
             onClick=${() => { setCol(kind); setIdx(i); setSide(0); act(d, kind === 'paired' ? 'toggle' : 'pair') }}>
          <span class="gcs-bt-dot" data-live=${d.connected ? '1' : '0'}></span>
          <span class="gcs-bt-name">
            <b>${d.name}</b>
            <i>${d.mac}</i>
          </span>
          ${kind === 'paired'
            ? html`<span class="gcs-bt-state" data-live=${d.connected ? '1' : '0'}>
                     ${working ? 'Working' : d.connected ? 'Connected' : 'Offline'}
                   </span>
                   <span class="gcs-bt-forget" role="button" data-on=${onForget ? '1' : '0'}
                         data-armed=${armed === d.mac ? '1' : '0'}
                         aria-label=${`Forget ${d.name}`}
                         onClick=${(e) => { e.stopPropagation(); setCol('paired'); setIdx(i); setSide(1); forget(d) }}>
                     ${armed === d.mac ? 'Press again to forget' : 'Forget'}
                   </span>`
            : html`<span class="gcs-bt-btn">${working ? 'Pairing…' : 'Connect'}</span>`}
        </div>`
    }

    return html`
      <${Fragment}>
      <section class="gcs-set-main gcs-bt-page" ref=${mainRef} data-zone=${active ? 'on' : 'off'}>
        <div class="gcs-set-h-row">
          <div class="gcs-set-h">Bluetooth</div>
          <div class="gcs-wifi-state">${paired.some((d) => d.connected) ? 'ON' : ''}</div>
        </div>
        <p class="gcs-set-sub">
          Pairing keeps what you have already introduced to the box. Controllers
          reconnect on their own when the console wakes.
        </p>

        ${msg ? html`<div class="gcs-wifi-msg">${msg}</div>` : null}

        <div class="gcs-bt-cols">
          <div class="gcs-bt-col" data-col="paired">
            <div class="gcs-set-kicker">Paired</div>
            <div class="gcs-bt-list">
              ${!gotPaired
                ? html`<div class="gcs-load"><i></i>${slowPaired
                    ? 'Still asking the adapter…' : 'Reading the paired list…'}</div>`
                : paired.length === 0
                  ? html`<div class="gcs-wifi-empty">Nothing is paired yet.</div>`
                  : paired.map((d, i) => row(d, i, 'paired'))}
            </div>
          </div>

          <div class="gcs-bt-col" data-col="nearby">
            <div class="gcs-bt-head">
              <span class="gcs-set-kicker">Nearby</span>
              <span class="gcs-wifi-scan" onClick=${rescan}>
                <i data-idle=${scanning ? '0' : '1'}></i>${scanning ? 'Scanning' : 'Scan again'}
              </span>
            </div>
            <div class="gcs-bt-list">
              ${nearby.length === 0
                ? (scanning
                    ? html`<div class="gcs-load"><i></i>${slowScan
                        ? 'Still looking. Some devices only advertise every few seconds.'
                        : `Looking around for ${SCAN_SECS} seconds…`}</div>`
                    : html`<div class="gcs-wifi-empty">Nothing new in range.</div>`)
                : nearby.map((d, i) => row(d, i, 'nearby'))}
            </div>
          </div>
        </div>
      </section>
      <//>`
  }
}
