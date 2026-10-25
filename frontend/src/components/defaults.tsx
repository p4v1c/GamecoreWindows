/**
 * The default UI, re-exposed as prop-less surfaces.
 *
 * Theme surfaces take no props by contract — everything comes from the SDK —
 * but the components written before themes existed take handlers. These
 * wrappers close that gap, and they are what `sdk.defaults` hands to a theme
 * that wants to wrap the default look instead of replacing it.
 */
import { playSound } from '../lib/sounds'
import { useStore } from '../store'
import { launchAppSession, launchSession } from '../hooks/useWebSocket'
import { gateGameLaunch } from '../lib/launchGate'

import HomeScreen from './HomeScreen'
import LibraryScreen from './LibraryScreen'
import TopBar from './TopBar'
import Screensaver from './Screensaver'
import CrtStandby from './CrtStandby'
import PowerModal from './modals/PowerModal'
import GamepadModal from './modals/GamepadModal'
import DefaultGamepadView from './modals/gamepad/DefaultGamepadView'
import { VirtualKeyboard } from './ui/VirtualKeyboard'
import Toasts, { DefaultToastsView } from './ui/Toasts'
import SettingsModal from './modals/SettingsModal'
import SettingsScreen from './modals/SettingsScreen'
import DefaultShell from './DefaultShell'
import { Overlay, OverlayLabel, BackHeader } from './ui'
import { WifiPage } from './modals/settings/WifiPage'
import { AudioPage } from './modals/settings/AudioPage'
import { BluetoothPage } from './modals/settings/BluetoothPage'
import { StandbyPage } from './modals/settings/StandbyPage'
import { UpdatePage } from './modals/settings/UpdatePage'
import { DesktopPage } from './modals/settings/DesktopPage'
import { ThemesPage } from './modals/settings/ThemesPage'
import { CatalogPage } from './modals/settings/CatalogPage'
import { BiosPage } from './modals/settings/BiosPage'
import { StoragePage } from './modals/settings/StoragePage'

/** Launching lives here rather than in App so a prop-less surface can do it. */
export async function launchApp(system: { id: string; label?: string }): Promise<void> {
  // A second press, or one over a session, is not a second launch.
  if (useStore.getState().sessionGameKey) return
  playSound('launch')
  try {
    await launchAppSession(system.id, system.label || system.id)
  } catch (e) {
    console.error('Failed to launch app:', e)
  }
}

let gameLaunchPending = false

/** Launch a known ROM in place, without mounting or rescanning its library. */
export async function launchGame(
  game: { systemId: string; path: string; gameKey: string },
  ceremonyMs = 0,
): Promise<void> {
  if (gameLaunchPending || useStore.getState().sessionGameKey) return
  if (!game.path) throw new Error('This game has no ROM path. Reopen the Games tab.')
  gameLaunchPending = true
  try {
    const gated = gateGameLaunch(game.systemId, game.gameKey)
    const decision = typeof gated === 'string' ? gated : await gated
    if (decision !== 'launch') { gameLaunchPending = false; return }
  } catch (e) {
    gameLaunchPending = false
    throw e
  }
  useStore.getState().setTransition('launch')
  playSound('launch')
  try {
    // The emulator may take focus as soon as it spawns. Finish the theme's
    // handover first, including on the direct path that stays on Games.
    const hold = Number.isFinite(ceremonyMs) ? Math.max(0, Math.min(5000, ceremonyMs)) : 0
    if (hold > 0) await new Promise(resolve => setTimeout(resolve, hold))
    if (useStore.getState().transition !== 'launch') return
    await launchSession(game.systemId, game.path, game.gameKey)
  } finally {
    gameLaunchPending = false
    if (useStore.getState().transition === 'launch') useStore.getState().setTransition(null)
  }
}

export const DefaultHome = () => <HomeScreen onLaunchApp={launchApp} />
export const DefaultLibrary = () => <LibraryScreen />
export const DefaultScreensaver = () => <Screensaver />

/**
 * SDK 11. The CRT standby: a rendered room, the library's game clips on its
 * TV, the TV's light on the walls. A theme opts in by passing it as the
 * Shell's `screensaver`, with `skin` (a class for the caption's
 * `--crt-cap-*` variables) and `favourites` (`system_id:filename` keys).
 */
export { CrtStandby }

/**
 * SDK 12. Wakes the box on a mouse move or a key press while `stage` is not
 * 'off', for a theme drawing its own standby: the backend only hears pads.
 * Call it with `sdk.nav.use(s => s.standby)`.
 */
export { useLocalWake } from '../hooks/useLocalWake'

/** The modals close themselves through the store-independent callbacks App owns. */
export type CloseProps = { onClose: () => void }
export const DefaultPowerModal = ({ onClose }: CloseProps) => <PowerModal onClose={onClose} />
export const DefaultGamepadModal = ({ onClose }: CloseProps) => <GamepadModal onClose={onClose} />
/**
 * SDK 9. The controller screen's markup, for a theme to dress rather than
 * rewrite: pass it the `gamepadView` props plus `skin` (a class on the scrim).
 */
export const GamepadView = DefaultGamepadView

export type TopBarProps = { onSettings: () => void; onPower: () => void }
export const DefaultTopBar = ({ onSettings, onPower }: TopBarProps) => (
  <TopBar onSettings={onSettings} onPower={onPower} />
)

export const DefaultKeyboard = VirtualKeyboard

/**
 * The settings screen and the power menu, as factories.
 *
 * Not components: both are `(sdk) => Component`, because they are written in
 * the theme SDK's own idiom — `sdk.ui.html`, `sdk.api`, `sdk.input.onGp` — and
 * a theme passes its own sdk so the screen talks to the box through it.
 *
 * They used to live in `config/themes/_shared/` and be imported by relative
 * path from each theme. That made them a delivery problem (`update/linux.sh`
 * only ships a theme directory whose `version` moved, and a fix here twice
 * failed to reach the box because nobody bumped it) and a safety problem (the
 * built-in UI is safe mode's fallback, and could not depend on a directory
 * shipped over the air). In the bundle they are simply always there.
 *
 * They carry no colour. `settings.css` gives the built-in UI its palette;
 * a theme's own stylesheet gives it theirs.
 */
export { createSettings } from '../settings/screen'
export { createPowerView } from '../settings/power'
export { createWhoIsPlaying } from '../settings/whoIsPlaying'

/** The whole default frontend. Render it with overrides to change one screen. */
export const Shell = DefaultShell

/**
 * The container to write a NEW settings page in, so it gets the same width,
 * padding and scroll as the built-in ones.
 *
 * Not for wrapping the pages in `DefaultSettingsPages`: every one of them
 * already renders its own `<Overlay>` as its root. This used to say they were
 * "fragments, not modals" and had to be wrapped — true before they each gained
 * one, and the opposite of true after. A theme author following it nested an
 * Overlay inside an Overlay and got exactly the broken width, margins and
 * scroll the old text warned about, which is how the Wi-Fi page came out
 * broken. Render those pages bare.
 */
export const SettingsOverlay = Overlay
export const Label = OverlayLabel
export const BackBar = BackHeader

/**
 * The settings screen the built-in UI now shows: the rail, the same one both
 * shipped themes draw, in the default's own dark and violet.
 */
export const DefaultSettings = ({ onClose }: CloseProps) => <SettingsScreen onClose={onClose} />

/**
 * The screen it replaced — a centred list of ten rows, each opening a
 * full-screen overlay.
 *
 * Kept, and not as a courtesy: every one of those overlays is still what
 * `DefaultSettingsPages` resolves to, so this is the menu that matches them. A
 * theme that wants a short list rather than a rail can render it, and it is
 * also the smaller thing to fall back on if the rail ever proves too much for
 * a weak box.
 */
export const DefaultSettingsList = ({ onClose }: CloseProps) => <SettingsModal onClose={onClose} />

/**
 * The settings sub-pages, so a theme can restyle the menu around them without
 * reimplementing Wi-Fi scanning or the update stream. Each takes
 * { onClose, onBack } and brings its own gamepad bindings.
 */
export const DefaultSettingsPages = {
  wifi: WifiPage,
  audio: AudioPage,
  bluetooth: BluetoothPage,
  standby: StandbyPage,
  themes: ThemesPage,
  update: UpdatePage,
  desktop: DesktopPage,
  // Adding and removing systems was reachable from the built-in settings modal
  // and from nowhere else. A theme builds its own menu and resolves each entry
  // through this map, so leaving `catalog` out of it meant the two shipped
  // themes had no way to install an emulator at all — the page existed, the
  // route existed, and nothing could open them.
  catalog: CatalogPage,
  // Same reason as `catalog`, and it bites harder here: the BIOS screen is
  // what someone opens BECAUSE their box is not working. A theme that could
  // not resolve it would leave them with the black screen this page exists to
  // explain, and no route to the explanation.
  bios: BiosPage,
  // And again, found by the check below rather than by a player: `storage` was
  // in the built-in settings menu and in no theme's, because it was never even
  // added to this map — so no theme *could* have offered it. Safe-eject for an
  // external disk is not somewhere to lose: the failure mode of not having it
  // is a pulled drive and a corrupted library.
  storage: StoragePage,
}

/**
 * Every settings page a theme is expected to be able to reach.
 *
 * The list is derived, never typed out: this map has gained four entries since
 * it was written and each one was a page some theme could not open. Comparing a
 * theme's menu against this is what turns "we forgot" into a line on screen.
 */
export const SETTINGS_PAGE_IDS = Object.keys(DefaultSettingsPages)

/** Nothing behind and nothing on top, unless a theme says otherwise. */
export const DefaultBackground = () => null
export const DefaultDecor = () => null

/**
 * The notification stack.
 *
 * `DefaultToasts` is the whole thing, queue included — for a theme that writes
 * its own tree rather than rendering `Shell`, and would otherwise have no
 * notifications at all. `DefaultToastsView` is only the markup, for a theme
 * that wants the default look somewhere else on screen.
 */
export const DefaultToasts = Toasts
export const DefaultToastsMarkup = DefaultToastsView
