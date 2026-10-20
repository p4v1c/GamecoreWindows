/**
 * The per-game options panel, from the sofa: the settings that belong to this
 * title and no other.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, cleanup, waitFor } from '@testing-library/react'
import GameOptionsModal from './GameOptionsModal'
import { api } from '../../../api'

const handlers = new Map<string, () => void>()

vi.mock('../../../hooks/useGamepad', () => ({
  onGp: (event: string, handler: () => void) => {
    handlers.set(event, handler)
    return () => handlers.delete(event)
  },
}))

function press(event: string) {
  act(() => { handlers.get(event)?.() })
}

// A system with no per-game settings and no sentence to put in their place.
const NO_PER_GAME = {
  system_id: 'duckstation', supported: false, why: null,
  gameId: null, settings: {}, profile: { available: false },
  canOpenSettings: false,
}

beforeEach(() => {
  handlers.clear()
  vi.spyOn(api.perGame, 'state').mockResolvedValue({ ...NO_PER_GAME })
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function open(onClose = () => {}) {
  return render(
    <GameOptionsModal systemId="duckstation" rom="Crash Bandicoot (USA).cue"
                      title="Crash Bandicoot" onClose={onClose} />)
}

/**
 * The settings that belong to this title and no other.
 *
 * Nothing here edits a setting. The two jobs are reporting a shipped profile
 * so it can be taken off, and opening the emulator's own window — because a
 * unified settings screen means translating every option into thirteen
 * vocabularies and then chasing all thirteen through every emulator release.
 */
const PROFILED = {
  system_id: 'rpcs3', supported: true, why: null,
  gameId: 'BLES00932', settings: { Video: { 'Write Color Buffers': true } },
  source: 'profile' as const,
  profile: {
    available: true, label: 'Demon’s Souls',
    why: 'Black screen without Write Color Buffers',
    emulator: '>=0.0.30', emulatorVersion: '0.0.41', inRange: true,
    applied: true, dismissed: false,
  },
  canOpenSettings: true,
}

describe('the settings that belong to this game', () => {
  it('names the profile and why it is there before offering to remove it', async () => {
    vi.spyOn(api.perGame, 'state').mockResolvedValue({ ...PROFILED })
    open()
    // The reason is not decoration. It is what lets someone judge whether
    // removing it is safe, and the alternative is a yes/no about a setting
    // whose effect they cannot see.
    await screen.findByText('Black screen without Write Color Buffers')
    expect(screen.getByText('Remove the setting for Demon’s Souls')).toBeTruthy()
  })

  it('removes it, and then offers to put it back', async () => {
    const spy = vi.spyOn(api.perGame, 'profile').mockResolvedValue({
      ok: true, ...PROFILED,
      profile: { ...PROFILED.profile, applied: false, dismissed: true },
    })
    vi.spyOn(api.perGame, 'state').mockResolvedValue({ ...PROFILED })
    open()
    await screen.findByText('Remove the setting for Demon’s Souls')
    screen.getByText('Remove the setting for Demon’s Souls').closest('button')!.click()
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('duckstation', 'Crash Bandicoot (USA).cue',
                                       'remove'))
    // The inverse has to be reachable AND actually send the other action.
    // Checking only that the label flipped would pass just as happily with a
    // row that says "Restore" and sends "remove" — a button that reads as an
    // undo and repeats the thing it claims to undo.
    await screen.findByText('Restore the setting for Demon’s Souls')
    screen.getByText('Restore the setting for Demon’s Souls').closest('button')!.click()
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('duckstation', 'Crash Bandicoot (USA).cue',
                                       'restore'))
  })

  it('says which versions a profile was verified for when this box is not one',
     async () => {
    vi.spyOn(api.perGame, 'state').mockResolvedValue({
      ...PROFILED,
      profile: { ...PROFILED.profile, inRange: false, applied: false,
                 emulatorVersion: '0.0.12' },
    })
    open()
    // Otherwise "nothing happened" reads as the setting having silently
    // failed, which sends the owner looking in entirely the wrong place.
    await screen.findByText(/Verified for >=0\.0\.30; this box runs 0\.0\.12/)
  })

  it('opens the emulator’s own window and gets out of its way', async () => {
    const spy = vi.spyOn(api.perGame, 'openSettings').mockResolvedValue({ ok: true })
    const onClose = vi.fn()
    vi.spyOn(api.perGame, 'state').mockResolvedValue({ ...PROFILED })
    open(onClose)
    await screen.findByText('Open the emulator’s settings')
    screen.getByText('Open the emulator’s settings').closest('button')!.click()
    await waitFor(() => expect(spy).toHaveBeenCalled())
    // The emulator takes the screen. A modal left underneath a foreign window
    // is one the player dismisses blind when the emulator quits.
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('is reachable from the pad, not only from a mouse nobody has', async () => {
    const spy = vi.spyOn(api.perGame, 'profile').mockResolvedValue({
      ok: true, ...PROFILED,
    })
    vi.spyOn(api.perGame, 'state').mockResolvedValue({ ...PROFILED })
    open()
    await screen.findByText('Remove the setting for Demon’s Souls')
    // The profile row is the first one.
    press('gp:dpad-up')
    press('gp:confirm')
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      'duckstation', 'Crash Bandicoot (USA).cue', 'remove'))
  })

  it('offers no button where the emulator has no window to open', async () => {
    vi.spyOn(api.perGame, 'state').mockResolvedValue({
      ...PROFILED, canOpenSettings: false,
    })
    open()
    await screen.findByText('Remove the setting for Demon’s Souls')
    // Pressed, nothing happens, and from a sofa that is indistinguishable
    // from the box having frozen.
    expect(screen.queryByText('Open the emulator’s settings')).toBeNull()
  })

  it('gives the pack’s own reason when the system cannot do this at all', async () => {
    vi.spyOn(api.perGame, 'state').mockResolvedValue({
      ...NO_PER_GAME,
      why: 'PCSX2 names a per-game ini after the serial AND the disc CRC.',
    })
    open()
    // An empty section and an emulator that genuinely has no per-title config
    // look identical from four metres away.
    await screen.findByText(/serial AND the disc CRC/)
  })

  it('says when the copy carries no identifier rather than showing nothing', async () => {
    vi.spyOn(api.perGame, 'state').mockResolvedValue({
      ...PROFILED, gameId: null, profile: { available: false },
    })
    open()
    await screen.findByText(/carries no identifier GameCore can read/)
  })

  it('cannot be scrolled past the ends of the list', async () => {
    const spy = vi.spyOn(api.perGame, 'profile').mockResolvedValue({
      ok: true, ...PROFILED,
    })
    vi.spyOn(api.perGame, 'state').mockResolvedValue({ ...PROFILED })
    open()
    await screen.findByText('Open the emulator’s settings')
    for (let i = 0; i < 10; i++) press('gp:dpad-up')
    press('gp:confirm')
    // Still on the first row, not on undefined.
    await waitFor(() => expect(spy).toHaveBeenCalledWith(
      'duckstation', 'Crash Bandicoot (USA).cue', 'remove'))
  })
})

describe('leaving', () => {
  it('closes on ○', async () => {
    const onClose = vi.fn()
    open(onClose)
    await screen.findByText('Game options')
    press('gp:back')
    expect(onClose).toHaveBeenCalled()
  })

  it('closes on the button that opened it', async () => {
    // A modal a player cannot leave the way they entered is one they leave by
    // pulling the plug.
    const onClose = vi.fn()
    open(onClose)
    await screen.findByText('Game options')
    press('gp:menu')
    expect(onClose).toHaveBeenCalled()
  })
})

describe('when the backend does not answer', () => {
  it('says the game still launches, rather than looking broken', async () => {
    vi.spyOn(api.perGame, 'state').mockRejectedValue(new Error('503'))
    open()
    await screen.findByText(/The game options could not be read/)
  })
})
