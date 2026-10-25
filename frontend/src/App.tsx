import { useState, useEffect, useRef, useSyncExternalStore } from 'react'
import { AnimatePresence } from 'framer-motion'
import { useWebSocket, applyIfStillCurrent } from './hooks/useWebSocket'
import { useGamepad, onGp } from './hooks/useGamepad'
import { useSessionScreen } from './hooks/useSessionScreen'
import { useWakeSplash } from './hooks/useWakeSplash'
import { useStore } from './store'
import { api } from './api'

import Splash from './components/Splash'
import LaunchCurtain from './components/LaunchCurtain'
import BootRecovery from './components/BootRecovery'
import { bootBackground, bootSteps, isBootReady, onBootChange } from './lib/boot'
import ErrorBoundary from './components/ErrorBoundary'
import DefaultShell from './components/DefaultShell'
import SessionBar from './components/SessionBar'
import WhoIsPlaying from './components/WhoIsPlaying'
import { usePlayerNames } from './lib/players'
import { useTheme } from './hooks/useTheme'
import { ThemeProvider, Shell } from './components/ThemeSurface'

/**
 * The kernel.
 *
 * Picking a theme swaps the frontend: this mounts one shell — the theme's, or
 * the default one — and nothing else of the UI. What stays here is what a theme
 * must not be able to take away:
 *
 *   · the input bus and the WebSocket
 *   · the fact that there *is* a splash — a theme may redraw it, not remove it
 *   · gp:guide, the double press that suspends a running game
 *   · the session bar — the only way back to a suspended one
 *   · the layer in which a theme draws session handovers
 *   · the emulator overlay handshake with Electron
 *   · the error boundaries and the L1+R1 rescue (see useTheme)
 */
/**
 * Longest a boot ANIMATION may hold the screen before it is treated as over.
 *
 * This bounds a theme that forgets to call `onDone` — a broken animation, not
 * a broken box — and it no longer decides whether the dashboard appears. That
 * is `isBootReady()`, and nothing here can promote it.
 */
const SPLASH_WATCHDOG_MS = 20000

/**
 * Longest the boot may go on before the screen says something.
 *
 * Also not a promotion: at the end of it the player gets the recovery view,
 * naming the step still outstanding. Showing the home instead would mean a
 * dashboard with no consoles on it, which from a sofa is indistinguishable
 * from a box that has lost its games. Longer than the shell's own patience
 * (20 s in electron/main.js) so that a backend problem is reported there
 * first, where the journal is.
 */
const BOOT_WATCHDOG_MS = 25000

export default function App() {
  // The ground the shell is already painting — see lib/boot.ts. Read per
  // render rather than at module scope: the value belongs to the shell, and
  // reading it where it is used keeps that visible.
  const bootBg = bootBackground()
  // A wake from Rest mode plays the boot again, sound included: the theme's
  // splash plays its `boot` sound on mount, so a fresh key is all it takes.
  const wakeCount = useStore(s => s.wakeCount)
  /** The animation is finished. NOT "the box is ready" — see below. */
  const [splashDone, finishSplash] = useWakeSplash(wakeCount)
  const [stuck, setStuck] = useState(false)
  /**
   * The three facts, from the host's own gate (lib/boot.ts). Subscribed rather
   * than polled: the last of them is marked from a `requestAnimationFrame`,
   * which is not a React event and would otherwise not re-render anything.
   */
  const ready = useSyncExternalStore(onBootChange, isBootReady)
  const showSplash = !splashDone || !ready
  // One subscription per value: this is the root, so a re-render here is a
  // re-render of the entire shell. Bare, it took one on every field in the
  // store — the library cursor included. See components/shellRerender.test.tsx.
  const goHome = useStore(s => s.goHome)
  const sessionGameKey = useStore(s => s.sessionGameKey)
  const asleep = useStore(s => s.asleep)

  const sessionRef = useRef(sessionGameKey)
  useEffect(() => { sessionRef.current = sessionGameKey }, [sessionGameKey])

  useWebSocket()
  useGamepad()
  const theme = useTheme()
  usePlayerNames()

  /**
   * The boot animation is decided once, and only once the theme has resolved.
   *
   * Reading `theme.splash ?? Splash` on every render mounts the default splash
   * immediately — the theme is still loading, so its splash is undefined — and
   * then swaps in the theme's a moment later, mid-animation. You see both boot
   * animations running over each other.
   *
   * Until the theme answers, the screen is a plain opaque cover: a fraction of
   * a second, and the alternative is the dashboard flashing before the splash.
   */
  const chosenSplash = useRef<React.ComponentType<{ onDone: () => void; bootReady?: boolean }> | null>(null)
  if (!chosenSplash.current && !theme.loading) chosenSplash.current = theme.splash ?? Splash
  const SplashC = chosenSplash.current
  const Ceremony = theme.ceremony

  // A themed splash decides its own length, but not whether booting ever ends:
  // one that forgets to call onDone would leave the box on its title card for
  // good. The default runs ~4s, plus a cold-boot hold capped at 10s.
  useEffect(() => {
    if (splashDone) return
    const t = setTimeout(() => {
      console.warn('[gamecore] the boot animation never finished — treating it as over')
      finishSplash()
    }, SPLASH_WATCHDOG_MS)
    return () => clearTimeout(t)
  }, [splashDone])

  // And the other half, which does not move the boot along: it explains it.
  useEffect(() => {
    if (ready) { setStuck(false); return }
    const t = setTimeout(() => {
      console.warn('[gamecore] still not ready:', JSON.stringify(bootSteps()))
      setStuck(true)
    }, BOOT_WATCHDOG_MS)
    return () => clearTimeout(t)
  }, [ready])

  // The shell hides itself while a session owns the screen.
  useSessionScreen()

  /**
   * The one binding no theme may own: leaving a running game.
   *
   * It killed. It suspends now — the game is frozen and intact, and closing it
   * is a deliberate second action on the session bar. Two accidental presses
   * used to cost an unsaved save; they cost nothing at all now.
   *
   * The backend's own evdev monitor does the same thing when IT sees the
   * double press, and both paths can fire for one press: Chromium exposes the
   * guide button on some pads and not others, so neither path can be the only
   * one. The second call finds nothing on the screen and is refused, which is
   * why the failure is swallowed rather than shown.
   *
   * `background()` answers with the whole session state, and that answer is
   * what moves the store — not an optimistic write here. A suspend that the
   * backend refused must not leave the interface believing the game is safely
   * frozen when it is still running behind the picture.
   */
  useEffect(() => onGp('gp:guide', async () => {
    if (!sessionRef.current) {
      // The backend can suspend first for this same press. Its acknowledgement
      // must not then turn the frontend's duplicate into a navigation command.
      if (!useStore.getState().backgroundSessions.length) goHome()
      return
    }
    // The answer moves the interface without waiting for the socket — but only
    // if the socket has not said better while it was in flight.
    await applyIfStillCurrent(api.games.background())
  }), [goHome])

  return (
    <ThemeProvider value={theme}>
      <Shell fallback={DefaultShell} />

      {/* The host owns the layer and the theme owns the picture. Keeping the
          z-index here preserves the theme contract and guarantees that the
          handover sits above every shell layer and below the boot splash. */}
      {Ceremony && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 900, pointerEvents: 'none' }}>
          <ErrorBoundary fallback={null}>
            <Ceremony />
          </ErrorBoundary>
        </div>
      )}

      {/* Above the shell and outside it, for the same reason the splash is: a
          theme may redraw this, and may not remove it. Suspending is reached
          through a gesture the core owns, so the way back has to be owned by
          the core too — a theme that simply forgot to draw a session bar would
          otherwise leave a frozen emulator holding gigabytes of RAM with
          nothing on screen able to resume or close it. */}
      <SessionBar view={theme.sessionBar} menuView={theme.sessionMenu} />

      {/* From the press to the new session's window: black, the pad locked.
          Over the shell and the theme's ceremony, under the boot splash. */}
      <LaunchCurtain />

      {/* Over every shell, under the splash: asked by the host so no theme can
          drop it. Drawn during the boot, answered once the splash is gone. */}
      <WhoIsPlaying interactive={ready && splashDone} />

      {/* Above the shell, and outside it. A theme draws its own boot animation
          but cannot remove it, and cannot decide when booting ends: onDone is
          ours, and a theme that never calls it hits the watchdog above.

          "Above" has to be built here, because a theme is forbidden to write a
          z-index (docs/themes/README.md §6 — the shell owns stacking) and being
          later in the DOM is not enough: the shell paints its own layers at
          z-index 1, 400 and 500, and a positive z-index beats a `auto` one
          whatever the source order. A themed splash therefore came up UNDER the
          dashboard — the boot animation visible only in the gaps between the
          system tiles, the wordmark hidden behind them entirely. The default
          Splash never showed it because it sets zIndex 9000 on its own root,
          which is a thing only the host is allowed to do.

          So the host supplies the layer and the theme keeps drawing inside it,
          with no rule bent on either side. Same 9000 as the default splash: the
          two are mutually exclusive, and matching keeps one number to change. */}
      {/* Under the splash and over the shell, for as long as the box is not
          ready. An SDK 4 theme holds its own last frame and never needs this;
          one written before the contract fades out when its animation ends,
          and without a curtain that fade would reveal a dashboard still
          filling itself in. One mechanism, no theme sniffing, and the older
          theme keeps working. */}
      {!ready && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 8999, background: bootBg }} />
      )}

      <AnimatePresence>
        {showSplash && (SplashC ? (
          <div key={`splash-${wakeCount}`} style={{ position: 'fixed', inset: 0, zIndex: 9000 }}>
            <ErrorBoundary resetKey={theme.resetKey} fallback={<Splash onDone={finishSplash} />}>
              {/* `bootReady` is the SDK 4 half of the contract: the animation
                  may end on a held frame and leave when the host allows it.
                  A theme that ignores the prop behaves exactly as before. */}
              <SplashC onDone={finishSplash} bootReady={ready} />
            </ErrorBoundary>
          </div>
        ) : (
          <div key="pre-splash" style={{ position: 'fixed', inset: 0, zIndex: 900, background: bootBg }} />
        ))}
      </AnimatePresence>

      {/* Asleep, or awake and waiting for the TV: plain black over everything,
          splash included, so the frame from before the sleep never shows.
          Dropped in the same store write that replays the splash. */}
      {asleep && (
        <div data-testid="sleep-cover" style={{ position: 'fixed', inset: 0, zIndex: 9600, background: '#000', cursor: 'none' }} />
      )}

      {stuck && <BootRecovery steps={bootSteps()} onRetry={() => window.location.reload()} />}
    </ThemeProvider>
  )
}
