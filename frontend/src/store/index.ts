import { create } from 'zustand'
import type { RosterPad } from '../api'
import type { PadOwners } from '../lib/players'

type Screen = 'home' | 'library'

/** One frozen session, as the backend describes it. */
export interface BackgroundSession {
  gameKey: string
  systemId: string
  session: number
  /** A tile with no ROM launches with `game_key === system_id`. */
  kind: 'game' | 'app'
}

interface GamecoreStore {
  // Navigation
  screen: Screen
  selectedSystemId: string | null
  selectedGameIdx: number
  gridFocusIdx: number
  gridPage: number

  // Modal focus lock — prevents background screens from consuming gamepad events
  modalDepth: number

  // Power action in flight ('rest' | 'shutdown' | 'restart' | 'desktop'):
  // freezes the UI so nothing jumps back on screen while the OS acts
  powerPending: string | null

  /**
   * What the box is doing about power, as the backend last said.
   *
   * Here rather than inside the standby overlay because it is not the
   * overlay's business: the input bus has to know, and a theme may draw its
   * own screensaver — Summer does — or none at all. A guard that lived in the
   * picture would be lost with the picture.
   *
   *   'off'         awake
   *   'screensaver' the slideshow is up, the panel is still lit
   *   'sleep'       the backend has cut the panel through DPMS
   */
  standby: 'off' | 'screensaver' | 'sleep'
  setStandby: (stage: 'off' | 'screensaver' | 'sleep') => void

  /** The active profile's name, '' while the box has no profiles; kept current
   *  by `usePlayerNames` (lib/players.ts). Drawn by the top bar's picture. */
  profileName: string
  /** '' for the primary profile (and a box without profiles), else its id: what
   *  per-profile storage keys add, so the primary keeps its old keys. */
  profileKey: string
  /** The active profile's colour and picture, for the picture in a top bar. */
  profileLook: { color: string; avatar: string | null }
  setActiveProfile: (profileName: string, profileKey: string,
                     profileLook?: { color: string; avatar: string | null }) => void

  /** The connected pads as the backend knows them (`GET /controllers/pads`). */
  padRoster: RosterPad[]
  /** Player slot → the profile its pad is linked to: what labels a pad
   *  instead of "P<n>" (lib/players.ts). */
  padOwners: PadOwners
  setPads: (padRoster: RosterPad[], padOwners: PadOwners) => void

  /**
   * The session ON THE SCREEN — and it keeps that meaning exactly.
   *
   * Every reader of this asks the same question in different words: the pad
   * guard (`isPlaying`), the shell's decor, the library's bindings. All of
   * them mean "is a game in front of the player right now", and a suspended
   * one is not. So a session moving to the background writes `null` here, and
   * every one of those readers becomes correct without being touched — the
   * pad comes back on the dashboard while the game stays alive behind it.
   *
   * The alternative — keeping the key set and teaching each reader to also
   * check a state field — is the same fix written five times, with a sixth
   * reader added later that nobody remembers to teach.
   */
  sessionGameKey: string | null
  sessionSystemId: string | null

  /** Every suspended session, oldest first. Drawn by the session bar. */
  backgroundSessions: BackgroundSession[]

  /** The game which prevented a different game from being launched. */
  launchConflict: BackgroundSession | null
  setLaunchConflict: (session: BackgroundSession | null) => void

  /**
   * The handover a theme may draw a ceremony over, or null between them.
   *
   * The host decides WHEN — it is the only thing that knows a launch has been
   * held, a resume sent or a suspend confirmed — and the theme decides what it
   * looks like. That split is the point: a ceremony is the most theme-specific
   * thing on the box, and the timing is the least.
   *
   * `launch` and `resume` are held: the host waits `launch.ms` from the theme's
   * manifest before it sends anything, so the animation is over before the
   * emulator takes the screen. `suspend` is not held — the game is already
   * frozen by the time this is set — so it is a beat, not a gate.
   */
  transition: 'launch' | 'resume' | 'suspend' | null
  setTransition: (t: 'launch' | 'resume' | 'suspend' | null) => void
  /**
   * A new session whose first window is not up yet: the app's name ('' for a
   * game). The interface stays shown under the launch curtain, the pad already
   * locked, until the backend says the window is in front (`game:presented`).
   */
  awaitingWindow: string | null
  setAwaitingWindow: (label: string | null) => void
  setSessionState: (fg: { gameKey: string | null; systemId: string | null },
                    background: BackgroundSession[]) => void

  // Actions
  goHome: () => void
  goLibrary: (systemId: string) => void
  setGridFocus: (idx: number) => void
  setGridPage: (page: number) => void
  setSelectedGameIdx: (idx: number) => void
  setSession: (gameKey: string | null, systemId: string | null) => void
  openModal: () => void
  closeModal: () => void
  setPowerPending: (action: string | null) => void

  /**
   * Bumped when something asks for the mapping wizard — today, the toast shown
   * when a pad no SDL can name is plugged in.
   *
   * A counter and not a boolean: the shell reacts to the CHANGE, so asking
   * twice in a row works, and there is no flag left set for a later mount to
   * trip over. The toast cannot open the wizard itself — the shell owns which
   * modal is up, and the wizard has to displace whatever else is on screen.
   */
  remapRequest: number
  requestRemap: () => void

  /**
   * Opens the per-game options for the game the library has settled on, or
   * null when there is none. Set by LibraryScreen, called by the shell's ≡
   * binding: one handler decides between game options and Settings, so a
   * single press can never open both.
   */
  gameOptions: (() => void) | null
  setGameOptions: (open: (() => void) | null) => void

  /**
   * Bumped to open the suspended-session menu once nothing else is on screen.
   * A counter for the same reason as `remapRequest`; the power menu asks for
   * it as it closes, before its own modal depth has been released.
   */
  sessionMenuRequest: number
  requestSessionMenu: () => void

  /**
   * Bumped once per wake from Rest mode (or any sleep). The kernel replays the
   * theme's boot animation on a change and the shell closes its modals, so the
   * player lands on Home whatever was open when the box went to sleep.
   */
  wakeCount: number
  wake: () => void

  /**
   * The box is going to sleep, or waking and waiting for the TV: the kernel
   * covers everything in black so the TV never shows the frame from before the
   * sleep. Set by Rest mode's second press and by `power:sleeping`; cleared by
   * `wake()`, in the same write that replays the splash.
   */
  asleep: boolean
  setAsleep: (asleep: boolean) => void
}

export const useStore = create<GamecoreStore>((set) => ({
  screen: 'home',
  selectedSystemId: null,
  selectedGameIdx: 0,
  gridFocusIdx: 0,
  gridPage: 0,
  modalDepth: 0,
  powerPending: null,
  standby: 'off',
  profileName: '',
  profileKey: '',
  profileLook: { color: '', avatar: null },
  padRoster: [],
  padOwners: {},
  sessionGameKey: null,
  sessionSystemId: null,
  backgroundSessions: [],
  launchConflict: null,
  transition: null,
  awaitingWindow: null,
  remapRequest: 0,
  gameOptions: null,
  sessionMenuRequest: 0,
  wakeCount: 0,
  asleep: false,

  goHome: () => set({ screen: 'home', selectedSystemId: null, gridPage: 0, gridFocusIdx: 0 }),
  goLibrary: (id) => set({ screen: 'library', selectedSystemId: id, selectedGameIdx: 0 }),
  setGridFocus: (idx) => set({ gridFocusIdx: idx }),
  setGridPage: (page) => set({ gridPage: page }),
  setSelectedGameIdx: (idx) => set({ selectedGameIdx: idx }),
  setSession: (gameKey, systemId) => set({ sessionGameKey: gameKey, sessionSystemId: systemId }),
  setTransition: (transition) => set({ transition }),
  setAwaitingWindow: (awaitingWindow) => set({ awaitingWindow }),
  // Both halves in one write. Two `set` calls would render once with the game
  // gone from the screen and still absent from the session bar, which is one
  // frame of a box that has lost the player's game.
  setSessionState: (fg, background) => set({
    sessionGameKey: fg.gameKey, sessionSystemId: fg.systemId,
    backgroundSessions: background,
  }),
  setLaunchConflict: (launchConflict) => set({ launchConflict }),
  openModal: () => set(s => ({ modalDepth: s.modalDepth + 1 })),
  closeModal: () => set(s => ({ modalDepth: Math.max(0, s.modalDepth - 1) })),
  setPowerPending: (action) => set({ powerPending: action }),
  setStandby: (stage) => set({ standby: stage }),
  setActiveProfile: (profileName, profileKey, profileLook = { color: '', avatar: null }) =>
    set({ profileName, profileKey, profileLook }),
  setPads: (padRoster, padOwners) => set({ padRoster, padOwners }),
  requestRemap: () => set(s => ({ remapRequest: s.remapRequest + 1 })),
  setGameOptions: (gameOptions) => set({ gameOptions }),
  requestSessionMenu: () => set(s => ({ sessionMenuRequest: s.sessionMenuRequest + 1 })),
  setAsleep: (asleep) => set({ asleep }),
  wake: () => set(s => ({
    wakeCount: s.wakeCount + 1, powerPending: null, launchConflict: null, asleep: false,
    screen: 'home', selectedSystemId: null, gridPage: 0, gridFocusIdx: 0,
  })),
}))

/**
 * True while a game or app holds the screen. The one definition of the
 * session invariant: the input bus, the UI sounds, the theme rescue and
 * `sdk.input.rumble` all read it, and frame subscribers that act must too.
 */
export function isPlaying(): boolean {
  return useStore.getState().sessionGameKey !== null
}
