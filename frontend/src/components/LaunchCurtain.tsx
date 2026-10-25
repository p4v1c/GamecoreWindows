/**
 * The launch curtain: black over the interface from the press until the new
 * session's window is up.
 *
 * The pad is already locked (a session owns the screen) and the interface
 * stays shown under this, so nothing flashes while the app starts; the shell
 * hides it only once the backend has the app's window in front
 * (`game:presented`, hooks/useSessionScreen.ts). Drawn by the host, over every
 * theme: a theme's own launch ceremony plays before the launch is sent.
 */
import { useStore } from '../store'

export default function LaunchCurtain() {
  const awaiting = useStore(s => s.awaitingWindow)
  const playing = useStore(s => s.sessionGameKey !== null)
  if (awaiting === null || !playing) return null
  return (
    <div
      data-testid="launch-curtain"
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed', inset: 0, zIndex: 950, background: '#000', cursor: 'none',
        display: 'grid', placeItems: 'center',
        animation: 'gc-launch-curtain-in 180ms ease-out both',
      }}
    >
      <style>{`@keyframes gc-launch-curtain-in { from { opacity: 0 } to { opacity: 1 } }
        @media (prefers-reduced-motion: reduce) { [data-testid="launch-curtain"] { animation: none !important } }`}</style>
      {awaiting && (
        <div style={{ fontFamily: 'var(--gc-font-ui)', fontSize: 32, fontWeight: 600, color: '#fff' }}>
          Starting {awaiting}
        </div>
      )}
    </div>
  )
}
