/**
 * Who owns the screen: the session or the interface.
 *
 * A resumed game maps no new window: its window is already there, under the
 * interface. If nothing hides GameCore, the game comes back invisible.
 */
import { render, act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AWAIT_WINDOW_MAX_MS, useSessionScreen } from './useSessionScreen'
import { useStore, type BackgroundSession } from '../store'

const HELD: BackgroundSession = {
  gameKey: 'Zelda.iso', systemId: 'dolphin', session: 1, kind: 'game' }

const Host = () => { useSessionScreen(); return null }

let screenClaim: ReturnType<typeof vi.fn>

function mount() {
  screenClaim = vi.fn()
  ;(window as unknown as { gamecore: unknown }).gamecore = { sessionScreen: screenClaim }
  return render(<Host />)
}

const setState = (s: Parameters<typeof useStore.setState>[0]) =>
  act(() => { useStore.setState(s) })

afterEach(() => {
  cleanup()
  delete (window as unknown as { gamecore?: unknown }).gamecore
  useStore.setState({ sessionGameKey: null, sessionSystemId: null,
                      backgroundSessions: [] })
  vi.restoreAllMocks()
})

describe('who owns the screen', () => {
  it('tells the shell to get out of the way when a game is on screen', () => {
    mount()
    setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin' })
    expect(screenClaim).toHaveBeenCalledWith(true)
  })

  it('gives the screen back when the game is suspended', () => {
    mount()
    setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin' })
    screenClaim.mockClear()
    setState({ sessionGameKey: null, sessionSystemId: null,
               backgroundSessions: [HELD] })
    expect(screenClaim).toHaveBeenCalledWith(false)
  })

  it('claims it again on resume', () => {
    mount()
    setState({ sessionGameKey: null, sessionSystemId: null,
               backgroundSessions: [HELD] })
    screenClaim.mockClear()
    setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin',
               backgroundSessions: [] })
    expect(screenClaim).toHaveBeenCalledWith(true)
  })

  it('says it once per change, not once per store write', () => {
    mount()
    setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin' })
    screenClaim.mockClear()
    setState({ backgroundSessions: [HELD] })
    setState({ modalDepth: 1 })
    expect(screenClaim).not.toHaveBeenCalled()
  })

  it('gives the screen back when the shell unmounts it', () => {
    const r = mount()
    setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin' })
    screenClaim.mockClear()
    r.unmount()
    expect(screenClaim).toHaveBeenCalledWith(false)
  })

  it('works on a shell without the bridge', () => {
    delete (window as unknown as { gamecore?: unknown }).gamecore
    render(<Host />)
    setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin' })
  })
})

describe('a launch hands the screen over once its window is up', () => {
  afterEach(() => { useStore.setState({ awaitingWindow: null }); vi.useRealTimers() })

  it('keeps the interface shown while the new window is awaited', () => {
    mount()
    setState({ sessionGameKey: 'steam', sessionSystemId: 'steam', awaitingWindow: 'Steam' })
    expect(screenClaim).not.toHaveBeenCalledWith(true)
    setState({ awaitingWindow: null })            // game:presented
    expect(screenClaim).toHaveBeenLastCalledWith(true)
  })

  it('hands over anyway when the presented event never comes', () => {
    vi.useFakeTimers()
    mount()
    setState({ sessionGameKey: 'steam', sessionSystemId: 'steam', awaitingWindow: 'Steam' })
    act(() => { vi.advanceTimersByTime(AWAIT_WINDOW_MAX_MS) })
    expect(useStore.getState().awaitingWindow).toBeNull()
    expect(screenClaim).toHaveBeenLastCalledWith(true)
  })
})
