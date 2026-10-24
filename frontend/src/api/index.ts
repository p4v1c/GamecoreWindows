import type { RosterPad, UsbDevice, AutoconfigPack, AutoconfigState, MappingStep, MappingSession, MappingCommit, SavedMapping } from './controllers'
export type { RosterPad, UsbDevice, AutoconfigPack, AutoconfigState, MappingStep, MappingSession, MappingCommit, SavedMapping } from './controllers'
import type { StorageVolume } from './storage'
export type { StorageVolume } from './storage'
import { logs } from './logs'
export type { LogsUsage } from './logs'
import { profiles } from './profiles'
import { power } from './power'
export type { PowerState } from './power'
import { standbyVideos } from './standby'
export type { StandbyItem, StandbyPlaylist } from './standby'
export type { Profile, ProfileController, ProfilesState, ProfileFields } from './profiles'

import { BASE, get, put, post, postDetailed } from './http'

export interface SystemEntry {
  id: string
  kind: 'emulator' | 'app'
  type?: string
  platform?: string
  label?: string
  color?: string
  iconPath?: string
  /**
   * Pictures the pack ships for themes, name → URL: `console` is the hardware
   * photo. From catalog/<id>/art/, or the operator's assets/art/<id>/. A theme
   * picks the name it wants and falls back when it is absent.
   */
  art?: Record<string, string>
  romsPath?: string
  extensions?: string[]
  path?: string
  args?: string
}

/**
 * What the box is running, as `/api/games/session` and the two lifecycle
 * events describe it.
 *
 * The flat fields are the session ON THE SCREEN, which is the shape this
 * endpoint has always had — so a client from before suspending existed reads a
 * box whose only session is frozen as "nothing in front of me". That is true,
 * and it is the answer that leaves its pad unblocked instead of freezing the
 * interface over a game nobody can see.
 */
export interface SessionEntry {
  game_key: string
  system_id: string
  rom_path?: string
  session: number
  state: 'foreground' | 'background'
  /** A tile with no ROM launches with `game_key === system_id`. */
  kind: 'game' | 'app'
}

export interface SessionState {
  game_key?: string
  system_id?: string
  rom_path?: string
  session?: number
  state?: 'foreground'
  kind?: 'game' | 'app'
  /** Every suspended session, oldest first. Absent when there are none. */
  background?: SessionEntry[]
}

export interface GameEntry {
  /** Present on the SDK 6 aggregate library; identifies the source console. */
  system_id?: string
  filename: string
  display_name: string
  path: string
  size: number
  /** The file's extension, upper case; "" for a system whose pack sets
   *  `roms.showExtension: false` (launcher entries such as Lutris stubs). */
  ext: string
}

export interface GameMeta {
  found: boolean
  title: string
  description: string
  year: string
  genres: string[]
  /** Highest player count the sources know: "1-3" arrives here as 3. */
  players: number
  /** Age rating (ESRB, PEGI as a fallback) — a label, not a score. */
  rating: string

  // ── Filled in by the gamemedia tier (ScreenScraper / LaunchBox) ──────────
  // All optional: a box with no ScreenScraper account and no LaunchBox index
  // answers from TheGamesDB, which has none of them. Read them with `?.`.
  source?: 'screenscraper' | 'launchbox' | ''
  developer?: string
  publisher?: string
  /** Full ISO date when known ("2011-05-10"); `year` is extracted from it. */
  released?: string
  /** The raw player string, "1-3" — `players` is the number. */
  players_label?: string
  /** Community score normalised to 0–1, ready to multiply by 5 stars. */
  score?: number | null
  /** Number of votes behind `score` (LaunchBox only). 5.0 from 2 votes is not 4.7 from 987. */
  score_count?: number | null
  /** { PEGI: "16", ESRB: "T", USK: "16", … } */
  classifications?: Record<string, string>
  platform?: string
  /** A PC game's minimum spec, for a printed box reverse. Empty for consoles. */
  requirements?: { os?: string; cpu?: string; ram?: string; gpu?: string; disk?: string }
}

/** What a shipped profile is doing for this game, if anything.
 *
 *  `available` false covers both "no profile for this title" and "this system
 *  has none at all" — the panel says nothing in either case, which is right:
 *  most games need no profile and hearing about it every time would be noise.
 */
export interface PerGameProfile {
  available: boolean
  label?: string
  /** What breaks without it. Shown before asking anyone to remove it. */
  why?: string
  /** The emulator versions it was verified against, e.g. ">=0.0.30". */
  emulator?: string
  emulatorVersion?: string | null
  /** False = it exists, but this box runs an emulator it does not claim. */
  inRange?: boolean
  applied?: boolean
  dismissed?: boolean
}

export interface PerGameState {
  system_id: string
  supported: boolean
  /** The pack's own sentence when `supported` is false. Never invented here. */
  why: string | null
  /** null = the dump carries no identity this box can read. Not an error. */
  gameId: string | null
  settings: Record<string, Record<string, string | number | boolean>>
  source?: 'player' | 'profile'
  profile: PerGameProfile
  canOpenSettings: boolean
}

/** One artwork a game has. The file itself is at `api.media.url(...)`. */
export interface MediaEntry {
  /** box, cart, logo, screenshot, mix, marquee, artwork, icon, bezel, video, document, theme, pinball */
  category: string
  kind: 'image' | 'video' | 'document' | 'archive'
  region: string
  /** Already on disk. False means the first request pays one download. */
  cached: boolean
}

export interface GameMediaIndex {
  found: boolean
  /** False = no media source configured on this box — not "game unknown". */
  available: boolean
  source?: string
  matched_by?: string
  meta: Partial<GameMeta>
  /** Keyed by media type: "box-3d", "clear-logo", "screenshot-gameplay", … */
  media: Record<string, MediaEntry>
  /** True = we could not ask (quota, network). Retrying later is worth something. */
  unreachable?: boolean
  notes?: string[]
}

/** One pack in the catalogue — an emulator or an application. */
export interface BtDevice {
  mac: string
  name: string
  connected: boolean
  /** Already known to the adapter. False means it was just discovered by a
   *  scan and has to be paired before it can be connected. */
  paired: boolean
}

export interface CatalogEntry {
  id: string
  kind: 'emulator' | 'app'
  label: string
  platform: string
  /** Who made the hardware — 'Nintendo', 'Sony'... Empty when the pack does not
   *  say, which groups it under "Other" rather than guessing from the id. */
  family: string
  color: string
  /** The product name when it differs from the platform label: the N64 slot is
   *  labelled "Nintendo 64" and runs "Rosalie's Mupen GUI". */
  emulatorName: string
  description: string
  /** 'shipped' with the release, or 'local' from config/catalog.d/. */
  origin: 'shipped' | 'local'
  /** Its tile is on the grid. Not "the application is present" — see api.catalog. */
  installed: boolean
  /** Blocks ignored because a local pack is data only (generator.py, services…). */
  restricted: string[]
}

export interface PlaytimeEntry {
  game_key: string
  system_id: string
  total_secs: number
  session_count: number
  last_played: string | null
}

/**
 * One system file the owner has to supply, and what this box makes of it.
 *
 * Three states, deliberately not two: `absent` and `mismatch` are different
 * problems with different fixes. Nothing here, and nothing on the page that
 * renders it, ever says where to obtain a file.
 */
export interface BiosFile {
  /** Name relative to the system's BIOS directory. Empty when `any_file`. */
  file: string
  /** The exact absolute path to copy to — the whole reason this screen exists. */
  path: string
  required: boolean
  note: string
  status: 'ok' | 'absent' | 'mismatch'
  /** An md5 was declared AND compared. `ok` without it means "present". */
  verified: boolean
  expected_md5: string
  /** Only filled on a mismatch: the hash the owner's file actually has. */
  actual_md5?: string
  /** The emulator scans its directory and names no file (DuckStation). */
  any_file?: boolean
}

export interface BiosSystem {
  id: string
  label: string
  platform: string
  color: string
  /** Where the files go on this box, already resolved. */
  dir: string
  /** The worst state among the REQUIRED files. Optional ones never reach it. */
  status: 'ok' | 'absent' | 'mismatch'
  /** Its tile is on the grid. A system not added yet is dimmed, never hidden. */
  installed: boolean
  files: BiosFile[]
}

export interface SysInfo {
  ip: string
  storage_used_gb: number
  storage_total_gb: number
  storage_free_gb: number
  version: string
  controllers: { level: number; name?: string; label?: string; player?: number | null; charging?: boolean }[]
  /**
   * The BIOS check, small enough to ride along on a support report. `ok` is
   * null when the check itself could not run — not the same as "fine".
   * `api.bios.list()` is where the per-file detail lives.
   */
  bios: { ok: boolean | null; systems: Record<string, 'ok' | 'absent' | 'mismatch'> }
}

export const api = {
  systems: {
    list: () => get<SystemEntry[]>('/systems'),
    get: (id: string) => get<SystemEntry>(`/systems/${encodeURIComponent(id)}`),
  },
  games: {
    list: (systemId: string) => get<GameEntry[]>(`/systems/${encodeURIComponent(systemId)}/games`),
    launch: (systemId: string, romPath = '', gameKey = '') =>
      post('/games/launch', { system_id: systemId, rom_path: romPath, game_key: gameKey }),
    /** Ends a run — the one on screen unless another is named. */
    kill: (session?: number) =>
      post('/games/kill', session === undefined ? {} : { session }),
    /** Freezes the session on screen and gives the interface back. */
    background: () => post<SessionState>('/games/background'),
    /**
     * Wakes a suspended session, putting whatever holds the screen behind it.
     * With no number, the most recent suspended one.
     */
    foreground: (session?: number) =>
      post<SessionState>('/games/foreground',
                         session === undefined ? {} : { session }),
    // `session` numbers the run, so a finish belonging to a previous one can be
    // told apart from the finish of the game on screen. See useWebSocket.
    session: () => get<SessionState>('/games/session'),
  },
  metadata: {
    get: (systemId: string, filename: string) =>
      get<GameMeta>(`/metadata/${encodeURIComponent(systemId)}/${encodeURIComponent(filename)}`),
  },
  /**
   * One game's own settings, and the shipped profile that may be behind them.
   *
   * `state()` answers everything the panel shows in a single round trip. Three
   * separate calls would give three chances to paint a half-answered screen,
   * and "supported, but we have not identified the game yet" is a state that
   * should never be visible.
   *
   * There is no call to set an individual setting, deliberately. GameCore does
   * not know what a setting MEANS — `openSettings()` opens the emulator's own
   * window, and what the player does there is what GameCore then keeps, per
   * game. A unified settings screen would mean translating every option into
   * thirteen vocabularies and maintaining that map for ever.
   */
  perGame: {
    state: (systemId: string, rom: string) =>
      get<PerGameState>(`/pergame/${encodeURIComponent(systemId)}`
                        + `?rom=${encodeURIComponent(rom)}`),
    profile: (systemId: string, rom: string, action: 'remove' | 'restore') =>
      post<{ ok: boolean } & PerGameState>(
        `/pergame/${encodeURIComponent(systemId)}/profile`, { rom, action }),
    openSettings: (systemId: string, rom: string) =>
      post<{ ok: boolean }>(`/pergame/${encodeURIComponent(systemId)}/open`,
                            { rom }),
  },
  /**
   * Every artwork a game has, not just its jacket.
   *
   * `list()` says what exists — that is the call to make first, because what a
   * game has depends on the game: a PS3 dump carries 28 media, an obscure
   * cartridge three. `url()` builds the src for one of them; it is a plain
   * string, so it goes straight into an <img> or a <video>.
   *
   * Nothing is downloaded before it is asked for, so the first display of a
   * type costs one round trip and every one after it is served from disk.
   */
  media: {
    list: (systemId: string, filename: string) =>
      get<GameMediaIndex>(`/media/${encodeURIComponent(systemId)}/${encodeURIComponent(filename)}`),
    url: (systemId: string, filename: string, type: string) =>
      `${BASE}/media/${encodeURIComponent(systemId)}/${encodeURIComponent(filename)}/media/${encodeURIComponent(type)}`,
  },
  playtime: {
    all: () => get<PlaytimeEntry[]>('/playtime'),
    forSystem: (id: string) => get<PlaytimeEntry[]>(`/playtime/system/${encodeURIComponent(id)}`),
    // Without a console this is the total across all of them: the same
    // filename can exist under two, and the table is keyed by the pair.
    forGame: (key: string, systemId?: string) => get<PlaytimeEntry>(
      `/playtime/game/${encodeURIComponent(key)}`
      + (systemId ? `?system_id=${encodeURIComponent(systemId)}` : '')),
  },
  /**
   * The pack catalogue: everything this box COULD run, and adding it without
   * re-running the installer.
   *
   * `installed` is about the grid — the tile the player sees. Whether the
   * Flatpak itself is present is a different question, and answering it needs
   * the network (`gamecore-emu verify`).
   *
   * install/remove/reconfigure return as soon as the CLI has started; progress
   * arrives on the WebSocket as `catalog:log`, and `catalog:done` closes it.
   */
  catalog: {
    list: () => get<CatalogEntry[]>('/catalog'),
    busy: () => get<{ busy: boolean }>('/catalog/busy'),
    install: (id: string) => post(`/catalog/${encodeURIComponent(id)}/install`),
    remove: (id: string) => post(`/catalog/${encodeURIComponent(id)}/remove`),
    reconfigure: (id: string) => post(`/catalog/${encodeURIComponent(id)}/reconfigure`),
  },
  standby: {
    get: () => get<{ state: string; enabled: boolean; screensaver_mins: number; sleep_mins: number; wake_settle_secs?: number }>('/standby'),
    setConfig: (cfg: { enabled?: boolean; screensaver_mins?: number; sleep_mins?: number; wake_settle_secs?: number }) =>
      post<{ ok: boolean; enabled: boolean; screensaver_mins: number; sleep_mins: number; wake_settle_secs?: number }>('/standby/config', cfg),
    exit: () => post('/standby/exit'),
    /** The UI has faded to black: the backend may switch the screen off. */
    dark: () => post('/standby/dark'),
    /** What the CRT standby plays: clips and screenshots already on disk. */
    videos: standbyVideos,
  },
  bios: {
    list: () => get<BiosSystem[]>('/bios'),
  },
  sysinfo: () => get<SysInfo>('/sysinfo'),
  update: {
    check: () => get<{ update_available: boolean; current: string; latest: string; download_url?: string }>('/update/check'),
    apply: () => post('/update/apply'),
    // The backend is the source of truth for "is an update running": component
    // state does not survive leaving the page, and an update outlives that.
    status: () => get<{ running: boolean }>('/update/status'),
  },
  wifi: {
    networks: () => get<{ ssid: string; signal: number; secured: boolean; connected: boolean }[]>('/settings/wifi/networks'),
    // `radio` is null on a box with no Wi-Fi hardware, absent on an older backend.
    status: () => get<{ connected: boolean; ssid: string; ip: string; iface: string; gateway: string; dns: string[]; mac: string; radio?: boolean | null; ethernet: { connected: boolean; iface: string; ip: string } }>('/settings/wifi/status'),
    /**
     * The radio detail behind each SSID — security, channel, band, link rate.
     *
     * Separate from `networks()` so that endpoint keeps the shape the default
     * UI and the shipped themes already parse. Merge on `ssid`; a screen that
     * does not want the detail simply never asks.
     */
    details: () => get<{ ssid: string; security: string; channel: number; band: string; rate: string }[]>('/settings/wifi/details'),
    connect: (ssid: string, password = '') => post<{ ok: boolean; wrong_password: boolean; error?: string }>('/settings/wifi/connect', { ssid, password }),
    disconnect: () => fetch(BASE + '/settings/wifi/connect', { method: 'DELETE' }).then(r => r.json()) as Promise<{ ok: boolean; error?: string }>,
    /** Rejects with the backend's sentence (Windows refused, hardware block). */
    setRadio: (enabled: boolean) => postDetailed<{ enabled: boolean | null }>('/settings/wifi/radio', { enabled }),
  },
  audio: {
    get: () => get<{ volume: number; muted: boolean }>('/settings/audio'),
    sinks: () => get<{ id: string; name: string; default: boolean }[]>('/settings/audio/sinks'),
    setVolume: (volume: number) => post('/settings/audio/volume', { volume }),
    setSink: (sink: string) => post('/settings/audio/sink', { sink }),
  },
  /**
   * The display mode, and the way back from a bad one.
   *
   * `setMode` arms a revert in the BACKEND: the previous mode returns unless
   * `confirm()` is called. That is deliberate — a mode the television refuses
   * is a black screen, and the screen that would have held the timer is the
   * one that disappears.
   */
  display: {
    get: () => get<{
      output: string
      modes: { width: number; height: number; rate: number }[]
      current: { width: number; height: number; rate: number } | null
      pending: boolean
      revert_secs: number
    }>('/settings/display'),
    setMode: (width: number, height: number, rate: number) =>
      postDetailed<{ ok: boolean; changed: boolean; revert_secs: number }>(
        '/settings/display/mode', { width, height, rate }),
    confirm: () => post<{ ok: boolean; confirmed: boolean }>('/settings/display/confirm'),
    revert: () => post<{ ok: boolean; reverted: boolean }>('/settings/display/revert'),
    /** Interface size on this screen. Stored by the backend, applied by the
     *  shell as a page zoom (lib/uiScale.ts). */
    scale: () => get<{ scale: number; choices: number[] }>('/settings/display/scale'),
    setScale: (scale: number) => post<{ ok: boolean; scale: number }>('/settings/display/scale', { scale }),
  },
  bluetooth: {
    devices: () => get<BtDevice[]>('/settings/bluetooth/devices'),
    /** Looks around for `seconds`, then answers with what is in range and NOT
     *  already paired. It used to return the moment it was called and throw the
     *  discovery away, which is why nothing new could ever appear. */
    scan: () => post<{ ok: boolean; found: BtDevice[]; seconds: number }>('/settings/bluetooth/scan'),
    pair: (mac: string) => post<{ ok: boolean; message: string }>('/settings/bluetooth/pair', { mac }),
    connect: (mac: string) => post<{ ok: boolean; message: string }>('/settings/bluetooth/connect', { mac }),
    disconnect: (mac: string) => post<{ ok: boolean }>('/settings/bluetooth/disconnect', { mac }),
    /**
     * Unpair and forget: disconnects, drops the pairing, and answers `ok` only
     * once the paired list no longer holds the device — the device has to be
     * paired again before it can reconnect.
     */
    remove: async (mac: string) => {
      const r = await fetch(`${BASE}/settings/bluetooth/devices/${encodeURIComponent(mac)}`, { method: 'DELETE' })
      const payload = await r.json().catch(() => null)
      if (!r.ok) throw new Error(payload?.detail || `${r.status} ${r.statusText}`)
      return payload as { ok: boolean; message: string }
    },
  },
  /**
   * External disks.
   *
   * `unmount` is the one that matters: pulling a disk with unwritten data is
   * how a save is lost, and nobody can tell by looking whether a write has
   * finished. It is Windows' "Safely remove", and it reports Windows' reason
   * when it cannot: "busy" means a game is still reading the disk.
   */
  storage: {
    list: () => get<{ ok: boolean; volumes: StorageVolume[] }>('/storage/volumes'),
    mount: (device: string) =>
      postDetailed<{ ok: boolean; detail: string }>('/storage/mount', { device }),
    unmount: (device: string) =>
      postDetailed<{ ok: boolean; detail: string }>('/storage/unmount', { device }),
  },
  logs,
  profiles,
  power,
  controllers: {
    /**
     * The peripherals that are NOT SDL pads, present or absent.
     *
     * The player slots answer "who is player 2". They cannot answer "is the
     * GameCube adapter plugged in": Dolphin drives it over raw libusb, so it
     * has no evdev node and never enters the roster. Without this list, an
     * adapter that is unplugged and one the box cannot see look identical.
     */
    devices: () => get<{ ok: boolean; devices: UsbDevice[] }>('/controllers/devices'),
    /** The connected pads, for the controller screen. */
    pads: () => get<{ pads: RosterPad[] }>('/controllers/pads'),
    /**
     * Whether GameCore still writes emulator controller configs.
     *
     * `setAutoconfig` is BOTH destructive directions and the screen has to warn
     * before either: off empties the slots GameCore filled, on overwrites
     * whatever the owner set up by hand while it was off. The answer carries
     * `released` — what was actually emptied, by name — so the screen reports
     * what happened instead of assuming it.
     *
     * `pack` addresses one emulator's exception. Same call on purpose: the two
     * settings compose (`effective` is the composed answer), and a separate
     * route would let one change without the other being recomputed.
     */
    autoconfig: () => get<AutoconfigState>('/controllers/autoconfig'),
    setAutoconfig: (enabled: boolean, pack?: string) =>
      post<AutoconfigState & { released?: string[]; reprofiling?: boolean; error?: string }>(
        '/controllers/autoconfig', pack ? { enabled, pack } : { enabled }),
    /**
     * The mapping wizard: a pad SDL does not know, where no emulator will offer
     * to bind it in the first place. It writes an SDL mapping line every
     * SDL-based emulator reads at startup.
     */
    mapping: {
      start: () => post<MappingSession>('/controllers/mapping/start'),
      commit: (bindings: Record<string, string>, name = '') =>
        post<MappingCommit>('/controllers/mapping/commit', { bindings, name }),
      cancel: () => post<{ ok: boolean }>('/controllers/mapping/cancel'),
      saved: () => get<{ ok: boolean; saved: SavedMapping[]; file: string }>(
        '/controllers/mapping/saved'),
      forget: (guid: string) =>
        post<{ ok: boolean; forgotten: boolean }>('/controllers/mapping/forget', { guid }),
      /**
       * The event stream, on its own socket rather than the app-wide one.
       *
       * Deliberate: this carries every press on the pad, it is only meaningful
       * while the wizard is on screen, and closing it is how the backend learns
       * the player walked away. Sharing the main socket would leave a capture
       * session open for the life of the app.
       */
      socket: () => new WebSocket(`ws://${window.location.host}/api/ws/controllers/mapping`),
    },
  },
}
