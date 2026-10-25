/**
 * The interface's half of a wake from sleep.
 *
 * The backend counts wakes (`resumes`) and announces each one over the socket
 * (`power:resumed`). A socket can drop across a sleep, so every reconnect also
 * reads the count: a number that grew while nobody was listening is a wake
 * this page missed. A backend restart resets it, which is not a wake.
 *
 * The backend counts a wake only once the TV shows a picture again
 * (backend/services/display_ready.py), so the splash replayed here plays whole.
 */
import { api } from '../api'
import { useStore } from '../store'

let lastResumes: number | null = null

/** A `power:resumed` event. Replayed events (same count) are ignored. */
export function onResumed(resumes: unknown): void {
  const n = typeof resumes === 'number' ? resumes : null
  if (n !== null && lastResumes !== null && n <= lastResumes) return
  if (n !== null) lastResumes = n
  wakeUp()
}

/** Home with the splash, and the shell's window back in front: a Modern
 *  Standby wake sends the shell no resume of its own. */
function wakeUp(): void {
  useStore.getState().wake()
  window.gamecore?.wakeWindow?.()
}

/** `power:sleeping`: black over everything until the wake is announced. */
export function onSleeping(): void {
  useStore.getState().setAsleep(true)
}

/**
 * The shell saw the machine resume, before any socket is back: cover now, so
 * the first frame after the wake is black whatever was on screen before it,
 * then ask the backend, which lifts it again if this was no sleep of ours.
 */
export function onShellResumed(): void {
  useStore.getState().setAsleep(true)
  void syncWake()
}

/** On every socket open. A failed request changes nothing. */
export async function syncWake(): Promise<void> {
  try {
    const { resumes, sleeping, resting } = await api.power.state()
    if (lastResumes !== null && resumes > lastResumes) wakeUp()
    // `power:sleeping` can die with the socket it was sent on: the backend's
    // word puts the black back until the wake is announced.
    else if (sleeping || resting) useStore.getState().setAsleep(true)
    // A black layer the backend no longer stands behind (a page reloaded, an
    // announcement lost) would hide the box for good.
    else if (useStore.getState().asleep) useStore.getState().setAsleep(false)
    lastResumes = resumes
  } catch { /* the socket will tell us */ }
}

/** Tests only: forget what this page has seen. */
export function resetWakeForTests(): void {
  lastResumes = null
}
