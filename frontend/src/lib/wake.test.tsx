/**
 * Waking from Rest mode: the count from the backend, the store, and the shell.
 * The backend is a stubbed fetch; nothing sleeps.
 */
import { render, act, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import { onResumed, onShellResumed, onSleeping, syncWake, resetWakeForTests } from './wake'
import DefaultShell from '../components/DefaultShell'
import type { PowerViewProps } from '../components/modals/power/types'

const answer = (resumes: number, sleeping = false, resting = false) =>
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, statusText: 'OK', json: async () => ({ resting, resumes, sleeping }),
  })))

beforeEach(() => {
  resetWakeForTests()
  useStore.setState({ wakeCount: 0, asleep: false, powerPending: null, screen: 'home', modalDepth: 0,
                      sessionGameKey: null, backgroundSessions: [] })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('the wake count', () => {
  it('an announced wake lands on Home and unfreezes the power menu', () => {
    useStore.setState({ screen: 'library', selectedSystemId: 'nes', powerPending: 'rest' })
    onResumed(1)
    const s = useStore.getState()
    expect(s.wakeCount).toBe(1)
    expect(s.screen).toBe('home')
    expect(s.selectedSystemId).toBeNull()
    expect(s.powerPending).toBeNull()
  })

  it('the same wake announced twice replays once', () => {
    onResumed(3)
    onResumed(3)
    expect(useStore.getState().wakeCount).toBe(1)
  })

  it('a wake missed while the socket was down is caught on reconnect', async () => {
    answer(2)
    await syncWake()                    // first connection: nothing to compare
    expect(useStore.getState().wakeCount).toBe(0)
    answer(3)
    await syncWake()                    // reconnect after a sleep
    expect(useStore.getState().wakeCount).toBe(1)
  })

  it('a wake asks the shell to put its window back in front, once per wake', async () => {
    const wakeWindow = vi.fn()
    window.gamecore = { wakeWindow } as unknown as typeof window.gamecore
    onResumed(1)
    onResumed(1)
    answer(1)
    await syncWake()
    answer(2)
    await syncWake()                    // a wake missed with the socket down
    expect(wakeWindow).toHaveBeenCalledTimes(2)
    window.gamecore = undefined
  })

  it('a restarted backend counting from zero is not a wake', async () => {
    answer(5)
    await syncWake()
    answer(0)
    await syncWake()
    expect(useStore.getState().wakeCount).toBe(0)
  })
})

describe('the black layer', () => {
  it('goes up when the sleep is announced and down with the wake, splash and all', () => {
    onSleeping()
    expect(useStore.getState().asleep).toBe(true)
    onResumed(1)
    expect(useStore.getState().asleep).toBe(false)
    expect(useStore.getState().wakeCount).toBe(1)
  })

  it('stays up while the backend still waits for the TV', async () => {
    answer(0)
    await syncWake()
    onSleeping()
    answer(0, true)
    await syncWake()                    // a reconnect mid-wake
    expect(useStore.getState().asleep).toBe(true)
  })

  it('comes back on a reconnect when the sleep announcement was lost', async () => {
    answer(0)
    await syncWake()
    answer(0, true)
    await syncWake()                    // power:sleeping died with its socket
    expect(useStore.getState().asleep).toBe(true)
  })

  it('goes up as soon as the shell sees the resume, and down if no sleep was ours', async () => {
    answer(0)
    await syncWake()
    answer(0, true)
    onShellResumed()
    expect(useStore.getState().asleep).toBe(true)
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(useStore.getState().asleep).toBe(true)
    answer(0, false, false)
    onShellResumed()
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(useStore.getState().asleep).toBe(false)
  })

  it('is dropped if the backend no longer stands behind it', async () => {
    answer(0)
    await syncWake()
    onSleeping()
    answer(0, false, false)
    await syncWake()
    expect(useStore.getState().asleep).toBe(false)
  })
})

describe('the shell after a wake', () => {
  it('closes the power menu that sent the box to sleep', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => [] })))
    let shown = 0
    const PowerView = (_p: PowerViewProps) => { shown++; return <div data-testid="power" /> }
    const Nothing = () => null
    const { queryByTestId } = render(
      <DefaultShell background={Nothing} topbar={Nothing} homeView={Nothing as never}
                    libraryView={Nothing as never} powerView={PowerView} />)
    await act(async () => { window.dispatchEvent(new CustomEvent('gp:power')) })
    expect(queryByTestId('power')).not.toBeNull()
    expect(shown).toBeGreaterThan(0)
    await act(async () => { onResumed(1) })
    await act(async () => { await new Promise(r => setTimeout(r, 400)) })
    expect(queryByTestId('power')).toBeNull()
  })
})
