import { useEffect } from 'react'
import { useStore } from '../store'

/**
 * Tells the shell whether a session owns the screen.
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

    const sync = () => claim(!!useStore.getState().sessionGameKey)
    sync()
    const off = useStore.subscribe(sync)

    return () => {
      off()
      // The shell outlives this hook on a theme swap; leaving the claim set
      // would hide the interface for good.
      claim(false)
    }
  }, [])
}
