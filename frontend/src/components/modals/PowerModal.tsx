/**
 * PowerModal — the shutdown flow.
 *
 * The markup lives in a view component, default or themed. What stays here is
 * what must not be reimplemented: the two-press confirmation, the pending lock
 * that makes every close path inert, and the failsafe that unfreezes the UI
 * when the OS never powers off.
 */
import { useState, useEffect, useRef } from 'react'
import { useStore } from '../../store'
import { onGp } from '../../hooks/useGamepad'
import { api } from '../../api'
import DefaultPowerView from './power/DefaultPowerView'
import { useSharedPowerView } from './power/useSharedPowerView'
import type { PowerOption, PowerViewProps } from './power/types'

interface Props {
  onClose: () => void
  view?: React.ComponentType<PowerViewProps>
}

/**
 * The four ways a session ends, and nothing else.
 *
 * There is no longer any way for a theme to take a row out. `omit` existed so
 * themes could move "Scan mapping" and "Forget mapping" to their Controllers
 * screen; both actions were removed, and what is left — shutdown, restart,
 * desktop, and now rest — is exactly the set no theme was ever allowed to drop.
 */
const OPTIONS: PowerOption[] = [
  // Rest mode leads and the cursor opens on it, as on a PS5: it is what the
  // power button is for most evenings. The backend closes every game and app
  // before the box sleeps; the second press is the warning that it will.
  { id: 'rest', label: 'Rest mode', busy: 'Going to sleep…', icon: '☾', color: 'var(--set-acc, #e8a33d)', desc: 'Closes games and apps, then sleeps' },
  // Then the order of how often each is wanted.
  { id: 'shutdown', label: 'Turn off', busy: 'Turning off…', icon: '⏻', color: 'var(--set-danger, #ef4444)', desc: 'Power off' },
  { id: 'restart',  label: 'Restart',  busy: 'Restarting…',    icon: '↺', color: 'var(--set-warn, #f59e0b)', desc: 'Reboot the system' },
  // Leaving for the desktop is the third way a session ends, and it belonged
  // in the menu the other two are in. It was reachable only from
  // Settings → Desktop, four rows into a menu nobody opens to quit — while the
  // button that means "I am done with this box" opened a screen that could
  // restart it and shut it down but not step out of it.
  //
  // Last on purpose: leaving the front end is the least common of the three.
  { id: 'desktop',  label: 'Return to desktop', busy: 'Leaving…', icon: '⌘', color: 'var(--set-info, #38bdf8)', desc: 'Leave for the desktop, come back from there' },
]

/**
 * The way to a suspended session for pads whose guide button never reaches the
 * box (PS ×2 is the other way). Not a power action, so no second press.
 */
const SESSIONS: PowerOption = {
  id: 'sessions', label: 'In the background', busy: 'Opening…', icon: '▶',
  color: 'var(--set-ok, #22c55e)', desc: 'Resume or close a suspended game or app',
}

// If the OS is still alive after this delay the power command failed
// (no sudo rights, systemctl error…) — unfreeze the UI instead of soft-locking.
const POWER_FAILSAFE_MS = 10000
// Rest mode first waits for every game to close (up to ~25 s in
// backend/services/rest_mode.py) before it asks for sleep.
const REST_FAILSAFE_MS = 35000
const REST_REFUSED = 'Couldn’t sleep. Try again, or turn off.'

export default function PowerModal({ onClose, view }: Props) {
  /**
   * The shared power menu — the same one both themes draw — in the built-in
   * UI's colours, unless a theme passed its own view.
   *
   * `DefaultPowerView` is still the fallback under it, and not as dead code:
   * the shared view is built from an sdk, and if building one ever throws, a
   * box must still be able to turn itself off. That is the one screen where
   * "render nothing" is not an acceptable outcome.
   */
  const Shared = useSharedPowerView()
  const View = view ?? Shared ?? DefaultPowerView
  const hasSessions = useStore(s => s.backgroundSessions.length > 0)
  // Why the last Rest mode was refused, shown in that row's subtitle: every
  // theme draws `desc`, so no view needs a new prop for it.
  const [restError, setRestError] = useState<string | null>(null)
  const base = hasSessions ? [SESSIONS, ...OPTIONS] : OPTIONS
  const options = restError
    ? base.map(o => (o.id === 'rest' ? { ...o, desc: restError } : o))
    : base
  const optionsRef = useRef(options)
  optionsRef.current = options

  const [confirm, setConfirm] = useState<string | null>(null)
  const [focusIdx, setFocusIdx] = useState(() => Math.max(0, options.findIndex(o => o.id === 'rest')))
  const { powerPending, setPowerPending } = useStore()

  // Stable refs to avoid stale closure in confirm handler
  const focusIdxRef = useRef(focusIdx)
  const confirmRef  = useRef(confirm)
  useEffect(() => { focusIdxRef.current = focusIdx }, [focusIdx])
  useEffect(() => { confirmRef.current  = confirm  }, [confirm])

  // Failsafe: re-enable the UI if the system never actually powered off
  useEffect(() => {
    if (!powerPending) return
    const t = setTimeout(() => setPowerPending(null),
                         powerPending === 'rest' ? REST_FAILSAFE_MS : POWER_FAILSAFE_MS)
    return () => clearTimeout(t)
  }, [powerPending, setPowerPending])

  const handleAction = (id: string) => {
    if (useStore.getState().powerPending) return
    if (id === SESSIONS.id) { onClose(); useStore.getState().requestSessionMenu(); return }
    if (confirmRef.current !== id) { setConfirm(id); return }
    setPowerPending(id)
    if (id === 'desktop') {
      // The same two presses and the same pending lock as the other two: this
      // also ends the session, and if the window refuses to go the failsafe is
      // what gives the screen back instead of stranding the player on a menu
      // that has stopped accepting input. Identical to Settings → Desktop,
      // which stays for the sentence of explanation this row has no room for.
      window.gamecore?.quit()
      window.close()
      return
    }
    if (id === 'rest') {
      // The backend owns the whole sequence: close, verify, suspend. A refusal
      // (Windows refused to sleep) gives the menu back instead of waiting it out.
      setRestError(null)
      // Black now, not when Windows announces the sleep: the games close behind
      // it, and the TV never sees the menu again before the wake.
      useStore.getState().setAsleep(true)
      api.power.rest().catch((e: Error) => {
        // No answer at all is the sleep itself: Chromium drops every socket
        // as the network goes down, and the answer only comes after the wake.
        // Only a refusal gives the menu back; the wake lifts the black.
        if (e instanceof TypeError) return
        useStore.getState().setAsleep(false)
        // A bare "503 Service Unavailable" means nothing on a TV.
        setRestError(e.message && !/^\d{3} /.test(e.message) ? e.message : REST_REFUSED)
        setPowerPending(null)
        setConfirm(null)
      })
      return
    }
    if (window.gamecore) {
      if (id === 'restart')  window.gamecore.reboot()
      if (id === 'shutdown') window.gamecore.shutdown()
    }
    // No onClose() here on purpose: the modal stays up until the OS kills the
    // display, so the UI doesn't flash back to the screen behind it.
  }

  // All close paths are inert while the power command is in flight
  const safeClose = () => { if (!useStore.getState().powerPending) onClose() }

  const prev = () => { if (!useStore.getState().powerPending) setFocusIdx(i => Math.max(0, i - 1)) }
  const next = () => { if (!useStore.getState().powerPending) setFocusIdx(i => Math.min(optionsRef.current.length - 1, i + 1)) }

  useEffect(() => {
    const offs = [
      // ← → as well as ↑ ↓: a theme may lay the rows side by side, and the
      // other axis moving nothing is a menu that feels frozen.
      ...(['gp:dpad-up', 'gp:dpad-left'] as const).map(e => onGp(e, prev)),
      ...(['gp:dpad-down', 'gp:dpad-right'] as const).map(e => onGp(e, next)),
      onGp('gp:confirm',   () => {
        const o = optionsRef.current[focusIdxRef.current]
        if (o) handleAction(o.id)
      }),
      onGp('gp:back',  safeClose),
      onGp('gp:power', safeClose),
    ]
    return () => offs.forEach(o => o())
  }, [onClose]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <View
      options={options}
      focusIdx={focusIdx}
      confirmId={confirm}
      pendingId={powerPending}
      onFocus={setFocusIdx}
      onActivate={handleAction}
      onCancel={safeClose}
    />
  )
}
