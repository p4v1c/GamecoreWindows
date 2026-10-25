import { useEffect, useRef } from 'react'
import { useStore, type BackgroundSession } from '../store'
import { api, type SessionState } from '../api'
import { fadeToStandby } from '../lib/standbyFade'
import { onResumed, onShellResumed, onSleeping, syncWake } from '../lib/wake'

/** How long the 'interface is back' beat lasts. Purely visual: nothing
 *  is waiting on it, and a theme that draws nothing never sees it. */
export const SUSPEND_BEAT_MS = 900

const WS_URL = `ws://${window.location.host}/ws`

interface WsEvent {
  event: string
  data: Record<string, unknown>
}

type Handler = (data: Record<string, unknown>) => void

const handlers: Map<string, Set<Handler>> = new Map()

let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

/**
 * Ask the box what it is doing about power, instead of assuming it is awake.
 *
 * The stage used to be learned from the three `standby:*` events and nothing
 * else, so the front end only knew what happened while it was listening. Two
 * ways that goes wrong, and the input guard makes both of them matter:
 *
 *   · the page reloads while the box is asleep — Chromium restarting is enough
 *     — and comes up believing it is awake, over a panel that is switched off;
 *   · the socket drops while the box is awake and the box then falls asleep.
 *     `standby:sleep` is sent to nobody, and the store stays 'off' for as long
 *     as the socket is down: no overlay, no guard, a live cursor over a dark
 *     television.
 *
 * Called on every open, which is the first connection and every reconnection
 * after it. A request that fails changes nothing: a failed request is not
 * evidence about the box, and guessing either way is worse than waiting for the
 * websocket to say. See standbySync.test.ts.
 */
export async function syncStandby(): Promise<void> {
  try {
    const { state } = await api.standby.get()
    const stage = state === 'sleep' ? 'sleep'
      : state === 'screensaver' ? 'screensaver'
        : state === 'active' ? 'off'
          : null      // a word this build does not know — say nothing
    if (stage) useStore.getState().setStandby(stage)
  } catch { /* the websocket will correct us */ }
}

/**
 * Which session answer is still worth writing.
 *
 * Bumped by every event that decides the session and read either side of the
 * request below. A `game:started` that arrives while `/api/games/session` is in
 * flight wins: the reply describes the box a moment ago, and clearing a game
 * that has just started would unblock the pad over a running emulator.
 */
let sessionEpoch = 0

/**
 * Which run the store's session belongs to, as the backend numbers them.
 *
 * The backend can have two of them alive at once for a moment — a watcher
 * suspended on a game that has exited, a new game already started — and the
 * late `game:finished` of the first one used to unlock the screen over the
 * second. `null` means an optimistic session written by the launch itself,
 * before any event has named a number: nothing to compare, so nothing is
 * refused.
 */
let currentSession: number | null = null

/** A launch reply must not resurrect a run that already finished over WS. */
export async function launchSession(systemId: string, path: string, gameKey: string): Promise<void> {
  const epoch = sessionEpoch
  await api.games.launch(systemId, path, gameKey)
  if (epoch === sessionEpoch) writeSession(gameKey, systemId)
}

const writeSession = (gameKey: string | null, systemId: string | null,
                      session: number | null = null) => {
  sessionEpoch += 1
  currentSession = gameKey ? session : null
  if (!gameKey) useStore.getState().setAwaitingWindow(null)
  useStore.getState().setSession(gameKey, systemId)
}

/**
 * An app launched from a tile: the pad locks and the curtain goes up at the
 * press, as on the Linux box where the launch answers at once. The backend
 * may take seconds before its answer (it waits for nothing an app reads, but
 * the spawn is still a round trip); a refusal gives the interface back.
 */
export async function launchAppSession(systemId: string, label: string): Promise<void> {
  writeSession(systemId, systemId)
  useStore.getState().setAwaitingWindow(label)
  const epoch = sessionEpoch
  try {
    await api.games.launch(systemId)
  } catch (e) {
    if (epoch === sessionEpoch) writeSession(null, null)
    throw e
  }
}

/**
 * The whole session state, both slots, from one payload.
 *
 * `/api/games/session` and the two suspend/resume events all carry this same
 * shape, and it is written in one go rather than as "set the foreground, then
 * set the background". A resume moves BOTH slots at once — the game coming
 * forward and the application going behind it — and applying that as two
 * writes renders a frame in which the box has neither.
 */
export const applySessionState = (data: SessionState | Record<string, unknown>) => {
  sessionEpoch += 1
  const d = data as Record<string, unknown>
  const key = typeof d.game_key === 'string' ? d.game_key : null
  currentSession = key && typeof d.session === 'number' ? d.session : null
  const raw = Array.isArray(d.background) ? d.background : []
  const background: BackgroundSession[] = raw
    .filter((b): b is Record<string, unknown> => !!b && typeof b === 'object')
    .map(b => ({
      gameKey: String(b.game_key ?? ''),
      systemId: String(b.system_id ?? ''),
      session: typeof b.session === 'number' ? b.session : -1,
      kind: b.kind === 'app' ? 'app' as const : 'game' as const,
    }))
    .filter(b => b.gameKey)
  // A snapshot never describes a window still opening: a resume shows one
  // that exists, and a reconnect cannot tell.
  useStore.getState().setAwaitingWindow(null)
  useStore.getState().setSessionState(
    { gameKey: key, systemId: key ? String(d.system_id ?? '') : null },
    background)
}

/**
 * Ask the box what it is running, instead of assuming nothing has changed.
 *
 * The socket carries `game:started` and `game:finished`, so a front end only
 * knows what happened while it was listening. Lose the socket with a game up,
 * have the emulator quit during the outage, come back: the finish was sent to
 * nobody. The reconnect used to resynchronise standby and not the session, so
 * the store kept a game key that no longer existed anywhere, and the session
 * guard blocked the pad on every screen.
 *
 * The socket now announces the session on connect — the empty one too — and
 * this is the second answer to the same question, for the case where that
 * announcement is itself lost. A failed request changes nothing.
 */
export async function syncSession(): Promise<void> {
  const epoch = sessionEpoch
  try {
    const s = await api.games.session()
    if (epoch !== sessionEpoch) return   // an event has since said better
    // Both slots, for the same reason this function exists at all. A socket
    // lost while a game was suspended and regained after it was closed would
    // otherwise leave a session bar on screen offering to resume a game that
    // is gone — the same class of fault as the stale `game_key` that used to
    // block the pad, arriving through the other slot.
    applySessionState(s)
  } catch { /* the websocket will correct us */ }
}

/**
 * Apply a request's own answer, unless an event has said better since.
 *
 * Same discipline as `syncSession` above and for the same reason: a reply
 * describes the box as it was when the request was sent. Suspending answers
 * with the whole state so the interface can move without waiting for the
 * socket — but if the socket has spoken in the meantime, the socket is newer.
 */
export async function applyIfStillCurrent(
  request: Promise<SessionState>): Promise<void> {
  const epoch = sessionEpoch
  try {
    const s = await request
    if (epoch !== sessionEpoch) return
    applySessionState(s)
  } catch { /* the websocket will correct us */ }
}

function connect() {
  if (socket && socket.readyState < 2) return

  socket = new WebSocket(WS_URL)

  socket.onopen = () => { syncStandby(); syncSession(); syncWake() }

  socket.onmessage = (e) => {
    try {
      const msg: WsEvent = JSON.parse(e.data)
      handlers.get(msg.event)?.forEach(h => h(msg.data))
    } catch {}
  }

  socket.onclose = () => {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connect, 3000)
  }

  socket.onerror = () => {
    socket?.close()
  }
}

export function onWsEvent(event: string, handler: Handler): () => void {
  if (!handlers.has(event)) handlers.set(event, new Set())
  handlers.get(event)!.add(handler)
  return () => handlers.get(event)?.delete(handler)
}

export function useWebSocket() {
  const initialized = useRef(false)

  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    connect()

    const runNumber = (d: Record<string, unknown>) =>
      typeof d.session === 'number' ? d.session : null
    const off1 = onWsEvent('game:started', (d) => {
      const label = useStore.getState().awaitingWindow
      writeSession(d.game_key as string, d.system_id as string, runNumber(d))
      // A new session's window is not up yet; an app's label set at the press stays.
      useStore.getState().setAwaitingWindow(label ?? '')
    })
    // Its window is in front (or never came): the shell may hide the interface.
    const off1c = onWsEvent('game:presented', (d) => {
      const run = runNumber(d)
      if (run !== null && currentSession !== null && run !== currentSession) return
      useStore.getState().setAwaitingWindow(null)
    })
    // Sent on every connection: the session as the backend has it, which is
    // `{}` when there is none. An empty one is an answer — see backend/ws.py.
    const off1b = onWsEvent('game:running', (d) => {
      applySessionState(d)
    })
    const off2 = onWsEvent('game:finished', (d) => {
      const ended = runNumber(d)
      // A finish belonging to a SUSPENDED run: the player closed it from the
      // session bar, or it was killed from under us. It never had the screen,
      // so nothing about the foreground changes — only the bar loses a row.
      const held = useStore.getState().backgroundSessions
      if (ended !== null && held.some(b => b.session === ended)) {
        useStore.getState().setSessionState(
          { gameKey: useStore.getState().sessionGameKey,
            systemId: useStore.getState().sessionSystemId },
          held.filter(b => b.session !== ended))
        return
      }
      // A finish from a run that is already over: the player has started
      // something else since, and this would unlock the screen underneath it.
      if (ended !== null && currentSession !== null && ended !== currentSession) return
      writeSession(null, null)
    })
    /**
     * The core's own gesture, and ONLY the gesture.
     *
     * It used to mean "the backend killed the game", so clearing the session
     * here was the state change. It now means "the player pressed Home twice",
     * and what happened as a result is carried by `game:backgrounded` — which
     * the backend sends first, on the same socket, with the whole state.
     *
     * The two must not both write. The backend has a real outcome where it
     * suspends nothing (the signal did not land, so the game is still running
     * fullscreen) and acknowledges the gesture anyway; treating that
     * acknowledgement as a finish unblocks the pad over a live emulator, which
     * is the exact fault the session guard exists to prevent.
     *
     * Keep the originating screen and selection when returning to the UI.
     */
    // Suspend and resume. Both carry the whole state after the transition,
    // because a resume moves two slots at once and "run 3 came forward" alone
    // says nothing about what happened to run 2.
    const snapshot = (d: Record<string, unknown>) => {
      const snap = d.state_snapshot
      if (snap && typeof snap === 'object') applySessionState(snap as Record<string, unknown>)
    }
    /**
     * The interface coming back, for a theme that wants to draw it arriving.
     *
     * Not a gate, unlike launch and resume: by the time this event exists the
     * game is already frozen and the screen is already ours, so there is
     * nothing left to hold. It is a beat — set here, cleared on a timer — and a
     * theme that draws nothing for it simply never notices.
     *
     * Cleared on the way out too: a socket that drops mid-beat would otherwise
     * leave the flag set, and the next screen to read it would draw a ceremony
     * for a handover that finished minutes ago.
     */
    const suspendBeat = { timer: 0 as ReturnType<typeof setTimeout> | 0 }
    const off7 = onWsEvent('game:backgrounded', (d) => {
      snapshot(d)
      const store = useStore.getState()
      store.setTransition('suspend')
      if (suspendBeat.timer) clearTimeout(suspendBeat.timer)
      suspendBeat.timer = setTimeout(() => {
        if (useStore.getState().transition === 'suspend') store.setTransition(null)
      }, SUSPEND_BEAT_MS)
    })
    const off8 = onWsEvent('game:foregrounded', snapshot)

    // Standby, into the store rather than into whatever is drawing the
    // screensaver. The input bus reads it to decide whether a press is a
    // command or a wake, and that has to hold for a theme that draws its own
    // standby screen (Summer) or none at all.
    const off4 = onWsEvent('standby:screensaver', () => fadeToStandby('screensaver'))
    const off5 = onWsEvent('standby:sleep', () => fadeToStandby('sleep'))
    const off6 = onWsEvent('standby:exit', () => fadeToStandby('off'))
    // Rest mode's wake: Home, with the boot animation (App.tsx, DefaultShell).
    const off9 = onWsEvent('power:resumed', (d) => onResumed(d.resumes))
    const off10 = onWsEvent('power:sleeping', onSleeping)
    // Subscribed once (the guard above): the shell has no way to unsubscribe.
    window.gamecore?.onResume?.(onShellResumed)

    return () => {
      if (suspendBeat.timer) clearTimeout(suspendBeat.timer)
      if (useStore.getState().transition === 'suspend') useStore.getState().setTransition(null)
      off1(); off1b(); off1c(); off2(); off4(); off5(); off6(); off7(); off8(); off9(); off10()
    }
  }, [])
}
