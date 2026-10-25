import { useEffect } from 'react'
import { useStore } from '../store'

/** The backend gives up on a window after 45 s (window_focus.PRESENT_TIMEOUT_SECS). */
export const AWAIT_WINDOW_MAX_MS = 50_000

/**
 * Tells the shell whether a session owns the screen.
 *
 * Not before the new session's window is up (`awaitingWindow`): until then
 * the interface stays shown under the launch curtain, so the player sees no
 * empty desktop while the app starts. Bounded by `AWAIT_WINDOW_MAX_MS`, past
 * the backend's own timeout, should `game:presented` be lost.
 *
 * Follows the reconciled foreground (`sessionGameKey`, null on suspend) rather
 * than raw start/finish events, so it covers start, suspend, resume, swap and
 * end, and cannot drift from the input guard, which reads the same value.
 * A resumed game maps no new window: unless the shell hides itself, the game
 * comes back underneath the interface.
 */
export function useSessionScreen(): void {
  useEffect(() => {
    let owned: boolean | null = null
    const claim = (next: boolean) => {
      if (owned === next) return
      owned = next
      window.gamecore?.sessionScreen?.(next)
    }

    let giveUp: ReturnType<typeof setTimeout> | null = null
    const sync = () => {
      const { sessionGameKey, awaitingWindow } = useStore.getState()
      const waiting = !!sessionGameKey && awaitingWindow !== null
      if (waiting && !giveUp) {
        giveUp = setTimeout(() => { giveUp = null; useStore.getState().setAwaitingWindow(null) },
                            AWAIT_WINDOW_MAX_MS)
      } else if (!waiting && giveUp) {
        clearTimeout(giveUp)
        giveUp = null
      }
      claim(!!sessionGameKey && !waiting)
    }
    sync()
    const off = useStore.subscribe(sync)

    return () => {
      off()
      if (giveUp) clearTimeout(giveUp)
      // The shell outlives this hook on a theme swap; leaving the claim set
      // would hide the interface for good.
      claim(false)
    }
  }, [])
}
