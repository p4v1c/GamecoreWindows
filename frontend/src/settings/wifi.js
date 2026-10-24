/**
 * Settings → Wi-Fi: middle column and detail column, bare markup (the frame
 * carries the overlay). Behaviour is `sdk.api.wifi.*` (WinRT); `GET /networks`
 * rescans by itself.
 *
 * The radio switch is the row above the list (`status.radio`; no row when the
 * box has no Wi-Fi hardware or the backend predates it). Not drawn: dBm (the
 * backend gives 0–100 %), "Forget this network" (no route yet; needs a
 * two-step confirm). The password dialog uses the on-screen keyboard.
 */
const REFRESH_MS = 10000

/** `sel` value of the radio row, above network 0. */
const RADIO = -1

/** 0–100 → four bars, the way the capture draws them. */
const barsFor = (signal) => Math.max(1, Math.min(4, Math.ceil((signal || 0) / 25)))

import { asList, follow } from './list.js'
import { createDialogs } from './dialog.js'

/**
 * @param Dialog  the screen's dialog (dialog.js). The password prompt is one in
 *                every layout, so the rail and L1/R1 stand down while it is up;
 *                with `detail="dialog"` (Orbit) a network's details are one too,
 *                instead of the column beside the list.
 */
export const createWifiPage = (sdk, useSlow, OwnDialog) => {
  // A page built on its own, outside the screen, still gets a working dialog.
  const Dialog = OwnDialog || createDialogs(sdk).Dialog
  const { html, useState, useEffect, useRef, React } = sdk.ui
  const Fragment = React.Fragment
  const Keyboard = sdk.defaults.DefaultKeyboard

  return ({ active, onLeave, onLeft, seed, detail: detailMode }) => {
    const asDialog = detailMode === 'dialog'
    const [nets, setNets] = useState([])
    // Seeded from the rail, which already fetched the status to put the SSID at
    // the end of this row. It is what the detail column is built from, so the
    // page opens with it rather than asking again and drawing a blank panel.
    const [status, setStatus] = useState(() => seed || null)
    const [detail, setDetail] = useState({})
    const [sel, setSel] = useState(0)
    const [asking, setAsking] = useState(null)   // ssid awaiting a password
    // The network whose details dialog is up (dialog layout only). An SSID
    // rather than an index: the list rescans every ten seconds and reorders
    // by signal, and an index would quietly start describing its neighbour.
    const [viewing, setViewing] = useState(null)
    // Bumped every time the prompt opens and used as the keyboard's `key`, so
    // a prompt can only ever open on an empty field.
    //
    // This is a guard, not a repair: the dialog is unmounted on confirm, so the
    // keyboard already remounts with `useState('')` and the test below passes
    // without this line. It is here because the invariant is load-bearing and
    // invisible — a box sent `<old password><new password>` to NetworkManager
    // as one string, and nothing in this file would have looked wrong. A later
    // refactor that keeps the dialog mounted would reintroduce it silently.
    const [askSeq, setAskSeq] = useState(0)
    const [busy, setBusy] = useState(false)
    const [msg, setMsg] = useState('')
    const [loaded, setLoaded] = useState(false)
    // The radio row asks twice before going off when nothing else keeps the
    // box online: off is a lost connection, with no undo from a remote screen.
    const [armed, setArmed] = useState(false)
    // Last radio state `load` saw. Right after off → on NetworkManager has not
    // scanned yet, so an empty answer is "not yet", not "nothing in range",
    // until the next periodic refresh, so a place with no network at all does
    // not scan forever.
    const radioRef = useRef(seed ? seed.radio : undefined)
    const wakingRef = useRef(false)
    // `GET /networks` rescans before it answers, so a couple of seconds is
    // normal and silence is not.
    const slow = useSlow(!loaded, 2500)

    // The list scrolls with the pad's cursor — see `follow` in list.js.
    const mainRef = useRef(null)
    useEffect(() => {
      if (active) follow(mainRef.current?.querySelector('[data-on="1"]'), sel === (hasRadio ? RADIO : 0))
    }, [sel, active, nets.length])

    const selRef = useRef(sel)
    useEffect(() => { selRef.current = sel }, [sel])
    const netsRef = useRef(nets)
    useEffect(() => { netsRef.current = nets }, [nets])

    /** `tick`: called by the periodic refresh rather than by an action. */
    const load = (tick = false) => {
      sdk.api.wifi.status()
        .then((s) => {
          setStatus(s)
          const was = radioRef.current
          radioRef.current = s && s.radio
          if (was === false && radioRef.current === true) { wakingRef.current = true; setLoaded(false) }
          // Radio off: nothing to scan, and a scan would only answer empty.
          if (s && s.radio === false) { setNets([]); setDetail({}); setLoaded(true); return }
          scan(tick)
        })
        .catch(() => scan(tick))
    }

    const scan = (tick) => {
      sdk.api.wifi.networks()
        .then((raw) => {
          const list = asList(raw)
          if (wakingRef.current && list.length === 0 && !tick) return
          wakingRef.current = false
          // The cursor stays on the network it was on, wherever the rescan
          // moved it — a list that reorders under the pad sends the next ✕
          // to a network nobody chose.
          setSel((i) => {
            if (i === RADIO) return i
            const was = netsRef.current[i]
            const at = was ? list.findIndex((n) => n.ssid === was.ssid) : -1
            return at >= 0 ? at : Math.max(0, Math.min(i, list.length - 1))
          })
          setNets(list); setLoaded(true)
        })
        .catch(() => setLoaded(true))
      // Additive endpoint: a box whose backend predates it simply shows the
      // rows that do not depend on it, rather than an empty detail column.
      sdk.api.wifi.details()
        .then((rows) => setDetail(Object.fromEntries(asList(rows).map((r) => [r.ssid, r]))))
        .catch(() => {})
    }

    // Same race as Bluetooth's: the rail's status request and the player's
    // first press are not ordered, so the seed is taken whenever it lands.
    useEffect(() => { if (seed && !status) setStatus(seed) }, [seed, status])

    useEffect(() => {
      load()
      // Not while the keyboard is up: a refresh under it reorders the list and
      // the network being joined moves out from under the passphrase.
      const t = setInterval(() => { if (!asking && !busy) load(true) }, REFRESH_MS)
      return () => clearInterval(t)
    }, [asking, busy])

    // A details dialog over a network that has left range describes nothing.
    useEffect(() => {
      if (viewing && loaded && !nets.some((n) => n.ssid === viewing)) {
        setViewing(null)
        setMsg(`${viewing} is no longer in range.`)
      }
    }, [nets, viewing, loaded])

    const join = (n, password = '') => {
      setBusy(true); setMsg('')
      sdk.api.wifi.connect(n.ssid, password)
        .then((r) => {
          if (r.ok) { setMsg(`Connected to ${n.ssid}.`); load() }
          else setMsg(r.wrong_password ? 'Wrong password. Try again.'
                                       : (r.error || 'Could not join that network.'))
        })
        .catch(() => setMsg('Could not reach the backend.'))
        .finally(() => setBusy(false))
    }

    const radioOn = !!(status && status.radio)
    const hasRadio = !!status && typeof status.radio === 'boolean'
    const wired = !!(status && status.ethernet && status.ethernet.connected)

    const switchRadio = () => {
      if (busy) return
      if (radioOn && !wired && !armed) { setArmed(true); return }
      setArmed(false); setBusy(true); setMsg('')
      sdk.api.wifi.setRadio(!radioOn)
        .then(() => load())
        .catch((e) => setMsg((e && e.message) || 'Could not reach the backend.'))
        .finally(() => setBusy(false))
    }

    // Moving off the radio row disarms it. Compared to the row, not fired on
    // any move: a click sets the cursor and arms in the same tick.
    useEffect(() => { if (sel !== RADIO) setArmed(false) }, [sel])

    // No network to stand on (radio off, or none in range): the cursor goes
    // to the switch, the only thing left to press.
    useEffect(() => {
      if (hasRadio && loaded && nets.length === 0) setSel(RADIO)
    }, [hasRadio, loaded, nets.length])

    const activate = (n) => {
      if (!n || busy) return
      if (n.connected) {
        setBusy(true); setMsg('')
        sdk.api.wifi.disconnect()
          .then((r) => { setMsg(r.ok ? `Disconnected from ${n.ssid}.`
                                     : (r.error || 'Could not disconnect.')); load() })
          .catch(() => setMsg('Could not reach the backend.'))
          .finally(() => setBusy(false))
        return
      }
      if (n.secured) { setAskSeq((k) => k + 1); setAsking(n.ssid); return }
      join(n)
    }

    useEffect(() => {
      if (!active || asking || viewing) return   // a dialog brings its own bindings
      // Wraps over the radio row (when there is one) and the networks.
      const move = (dir) => {
        sdk.system.playSound('move')
        setSel((i) => {
          const lo = hasRadio ? RADIO : 0
          const hi = Math.max(lo, netsRef.current.length - 1)
          const n = i + dir
          return n < lo ? hi : n > hi ? lo : n
        })
      }
      const offs = [
        sdk.input.onGp('gp:dpad-up', () => move(-1)),
        sdk.input.onGp('gp:dpad-down', () => move(1)),
        sdk.input.onGp('gp:dpad-left', onLeft || onLeave),
        sdk.input.onGp('gp:confirm', () => {
          if (selRef.current === RADIO) { sdk.system.playSound('confirm'); switchRadio(); return }
          const n = netsRef.current[selRef.current]
          if (!n) return
          if (asDialog) { sdk.system.playSound('confirm'); setViewing(n.ssid) } else activate(n)
        }),
        sdk.input.onGp('gp:back', onLeave),
      ]
      return () => offs.forEach((off) => off())
    }, [active, asking, viewing, onLeave, busy, armed, status])

    const cur = (asDialog && viewing ? nets.find((n) => n.ssid === viewing) : null)
      || nets[sel] || nets[0] || null
    const curDetail = cur ? (detail[cur.ssid] || {}) : {}
    const isConn = !!(cur && cur.connected)

    // Only rows with a value. A blank "Gateway" reads as "this network has
    // none", which is a different and wrong statement.
    const rows = cur ? (isConn
      ? [
          ['Status', 'Connected'],
          ['IP address', status && status.ip],
          ['Gateway', status && status.gateway],
          ['DNS', status && (status.dns || []).join(', ')],
          ['Security', curDetail.security || (cur.secured ? 'Secured' : 'Open')],
          ['Signal', `${cur.signal}%`],
          ['Link rate', curDetail.rate],
          ['MAC address', status && status.mac],
        ]
      : [
          ['Status', 'Not connected'],
          ['Security', curDetail.security || (cur.secured ? 'Secured' : 'Open')],
          ['Band', curDetail.band],
          ['Channel', curDetail.channel ? String(curDetail.channel) : ''],
          ['Signal', `${cur.signal}%`],
          ['Link rate', curDetail.rate],
          ['Password', cur.secured ? 'Required' : 'Not required'],
        ]).filter(([, v]) => v) : []

    // A fragment, not three siblings: htm returns an array for multiple roots
    // and React then wants a key on each, which is a warning about a list this
    // is not — the three are the middle column, the detail column and a modal.
    return html`
      <${Fragment}>
      <section class="gcs-set-main" ref=${mainRef} data-zone=${active ? 'on' : 'off'}>
        <div class="gcs-set-h-row">
          <div class="gcs-set-h">Wi-Fi</div>
          <div class="gcs-wifi-state">${wired ? 'WIRED' : hasRadio && !radioOn ? 'OFF' : loaded ? 'ON' : ''}</div>
        </div>
        <p class="gcs-set-sub">
          ${hasRadio && !radioOn
            ? 'Wi-Fi is off. Turn it on to see the networks in range.'
            : wired
            ? 'This box is on a cable. Wi-Fi stays available, and joining a network here does not unplug it.'
            : !loaded
              ? (asDialog
                  ? 'Select a network to see its details or to connect; joining a secured network asks for its password.'
                  : 'Selecting a network shows its details on the right; joining a secured network asks for its password.')
              // Only once the scan has answered may this screen say how many
              // networks there are. It used to read the length of an
              // as-yet-unfetched list, so the first thing on screen was "No
              // networks are in range" — on a box that was connected to one.
              : `${nets.length || 'No'} network${nets.length === 1 ? '' : 's'} ${nets.length === 1 ? 'is' : 'are'} in range. ${asDialog
                  ? 'Select one to see its details or to connect'
                  : 'Selecting one shows its details on the right'}; joining a secured network asks for its password.`}
        </p>

        ${hasRadio ? html`
          <div class="gcs-row2" data-type="toggle" data-on=${active && sel === RADIO ? '1' : '0'}
               role="switch" aria-checked=${String(radioOn)} aria-label="Wi-Fi"
               onClick=${() => { setSel(RADIO); switchRadio() }}>
            <span class="gcs-row2-text">
              <b>${armed ? 'Press again to turn Wi-Fi off' : 'Wi-Fi'}</b>
              <i>${wired ? 'The cable keeps this box online either way.'
                : radioOn ? 'Turning it off takes this box offline.'
                : 'This box is offline until Wi-Fi is back on.'}</i>
            </span>
            <span class="gcs-tgl" data-v=${radioOn ? '1' : '0'}><i></i></span>
          </div>` : null}

        ${msg ? html`<div class="gcs-wifi-msg">${msg}</div>` : null}

        ${hasRadio && !radioOn ? null
          : !loaded
          ? html`<div class="gcs-load"><i></i>${slow
              ? 'Still scanning. The radio is slow to answer.' : 'Scanning for networks…'}</div>`
          : nets.length === 0
          ? html`<div class="gcs-wifi-empty">No network is in range.</div>`
          : nets.map((n, i) => {
            const d = detail[n.ssid] || {}
            const bars = barsFor(n.signal)
            const sub = [d.band, d.channel ? `channel ${d.channel}` : '', `${n.signal}%`]
              .filter(Boolean).join(', ')
            return html`
              <div key=${n.ssid} class="gcs-wifi-row"
                   data-on=${active && i === sel ? '1' : '0'}
                   data-sel=${i === sel ? '1' : '0'}
                   role="button"
                   aria-label=${`${n.ssid}, ${n.connected ? 'connected' : d.security || (n.secured ? 'secured' : 'open')}, signal ${n.signal}%`}
                   onClick=${() => { setSel(i); if (asDialog) setViewing(n.ssid); else activate(n) }}>
                <span class="gcs-wifi-bars">
                  ${[1, 2, 3, 4].map((k) => html`
                    <i key=${k} data-fill=${k <= bars ? '1' : '0'} style=${{ height: `${k * 25}%` }} />`)}
                </span>
                <span class="gcs-wifi-name">
                  <b>${n.ssid}</b>
                  <i>${sub}</i>
                </span>
                <span class="gcs-wifi-sec">${d.security || (n.secured ? 'Secured' : 'Open')}</span>
                ${n.connected ? html`<span class="gcs-wifi-conn">Connected</span>` : null}
              </div>`
          })}

        ${loaded && (radioOn || !hasRadio) ? html`
          <div class="gcs-wifi-scan"><i></i>${busy ? 'Working' : 'Scanning'}</div>` : null}
      </section>

      ${asDialog ? null : html`
      <aside class="gcs-set-aside">
        ${cur ? html`
          <div class="gcs-set-kicker">${isConn ? 'Active network' : 'Selected network'}</div>
          <div class="gcs-set-aside-title">${cur.ssid}</div>
          <dl class="gcs-set-facts">
            ${rows.map(([k, v]) => html`
              <div key=${k} class="gcs-set-fact"><dt>${k}</dt><dd>${v}</dd></div>`)}
          </dl>
          <button class="gcs-set-cta" disabled=${busy}
                  onClick=${() => activate(cur)}>
            ${isConn ? 'Disconnect' : 'Connect'}
          </button>` : null}
      </aside>`}

      ${asDialog && viewing && cur && !asking ? html`
        <${Dialog}
          kicker=${isConn ? 'Active network' : 'Selected network'}
          title=${cur.ssid}
          onCancel=${() => setViewing(null)}
          actions=${[
            {
              id: 'join', primary: true, disabled: busy,
              label: busy ? 'Working…' : isConn ? 'Disconnect' : 'Connect',
              // Closed first, so a secured network's password prompt is the
              // only dialog up and ○ from it lands back on the list.
              run: () => { setViewing(null); activate(cur) },
            },
            { id: 'back', label: 'Back', run: () => setViewing(null) },
          ]}>
          <dl class="gcs-set-facts">
            ${rows.map(([k, v]) => html`
              <div key=${k} class="gcs-set-fact"><dt>${k}</dt><dd>${v}</dd></div>`)}
          </dl>
        <//>` : null}

      ${asking ? html`
        <${Dialog} kicker="Secured network" title=${asking} wide=${true} ownsInput=${true}
                   body="Enter the password to join this network."
                   onCancel=${() => setAsking(null)}>
            <!-- The keyboard is the host's, and it draws itself in hardcoded
                 white on a black field — see VirtualKeyboard.tsx, where only the
                 accent is a variable. On a light dialog that is a keyboard you
                 cannot read, which is exactly what Shelf shipped. It gets its
                 own dark surface here rather than each theme discovering the
                 problem, and a theme whose dialog is already dark can make this
                 transparent in one rule. -->
            <div class="gcs-set-kb">
            <${Keyboard} key=${`${asking}-${askSeq}`}
              title="" password=${true} placeholder="Password"
              onConfirm=${(pw) => {
                const n = netsRef.current.find((x) => x.ssid === asking)
                setAsking(null)
                if (n) join(n, pw)
              }}
              onCancel=${() => setAsking(null)} />
            </div>
        <//>` : null}
      <//>`
  }
}
