/** What the host had to settle before the shell may show the interface.
 *  Reported for the journal, never used to decide anything on this side. */
interface BootReadyReport {
  steps?: Record<string, boolean>
  ms?: number
}

interface GamecoreAPI {
  /** The ground the shell is already painting — the active theme's declared
   *  `boot.background`, or the default. `null` outside Electron. Used by the
   *  covers the host draws before the theme's splash exists, so the whole boot
   *  is one colour. */
  bootBackground: string | null

  /** Sent once, when the interface is worth looking at. Absent outside
   *  Electron — the browser dev server has nothing to tell. */
  bootReady: (report?: BootReadyReport) => void

  /** Interface size on this screen (Settings → Display → Scale), as a page
   *  zoom factor. Optional: an older shell has no such bridge. */
  setUiScale?: (factor: number) => void

  reboot:   () => void
  shutdown: () => void
  quit:     () => void

  /**
   * Whether a session owns the screen, so the shell can get out of its way.
   *
   * Optional: a box running an Electron shell older than this feature has no
   * such handler, and the call is made with `?.` for that reason. Resuming
   * there leaves the game behind the interface, which is the behaviour that
   * shell already had.
   */
  sessionScreen?: (owned: boolean) => void

  batteryToast:    (data: { theme?: Record<string, string>; level: number; player?: number | null; who?: string }) => void
  /**
   * `unconfigured` is systems the pipeline gave up on — a fault. `autoconfigOff`
   * is systems it was told not to touch — a setting. The HUD draws different
   * sentences for them and only ever receives one, because a pad that got
   * nothing because of the switch has no give-ups to report.
   */
  controllerToast: (data: { theme?: Record<string, string>; player?: number | null; who?: string; label?: string; connected: boolean; unconfigured?: string[]; autoconfigOff?: string[] }) => void

  /** Electron's powerMonitor `resume`, relayed by electron/power.js. Older shells lack it. */
  onResume?: (cb: () => void) => void
  /** After `power:resumed`: the shell puts its window back in front (electron/power.js). Older shells lack it. */
  wakeWindow?: () => void
}

declare global {
  interface Window {
    gamecore?: GamecoreAPI
  }
}

export {}
