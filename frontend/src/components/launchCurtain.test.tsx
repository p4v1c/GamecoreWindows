/**
 * Launching an app: the pad locks and the curtain goes up at the press, and a
 * refusal gives the interface back. The backend is a stubbed fetch.
 */
import { render, screen, cleanup, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore, isPlaying } from '../store'
import { launchApp } from './defaults'
import LaunchCurtain from './LaunchCurtain'

let answer: (r: { ok: boolean; status: number }) => void
let calls = 0

beforeEach(() => {
  calls = 0
  vi.stubGlobal('fetch', vi.fn(() => {
    calls += 1
    return new Promise(resolve => {
      answer = ({ ok, status }) => resolve({
        ok, status, statusText: ok ? 'OK' : 'Conflict',
        json: async () => (ok ? { ok: true } : { detail: 'A game is already running' }),
        text: async () => '',
      })
    })
  }))
  useStore.setState({ sessionGameKey: null, sessionSystemId: null, awaitingWindow: null })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('an app launch', () => {
  it('locks the pad and shows the curtain before the backend answers', async () => {
    render(<LaunchCurtain />)
    let done: Promise<void> = Promise.resolve()
    act(() => { done = launchApp({ id: 'steam', label: 'Steam' }) })
    expect(isPlaying()).toBe(true)
    expect(screen.getByText('Starting Steam')).toBeTruthy()
    await act(async () => { answer({ ok: true, status: 200 }); await done })
    expect(isPlaying()).toBe(true)
    expect(screen.getByTestId('launch-curtain')).toBeTruthy()
  })

  it('a second press while launching sends nothing', async () => {
    const first = launchApp({ id: 'steam', label: 'Steam' })
    await launchApp({ id: 'steam', label: 'Steam' })
    expect(calls).toBe(1)
    answer({ ok: true, status: 200 })
    await first
  })

  it('a refused launch gives the interface back', async () => {
    render(<LaunchCurtain />)
    let done: Promise<void> = Promise.resolve()
    act(() => { done = launchApp({ id: 'steam', label: 'Steam' }) })
    await act(async () => { answer({ ok: false, status: 409 }); await done })
    expect(isPlaying()).toBe(false)
    expect(screen.queryByTestId('launch-curtain')).toBeNull()
  })

  it('a game launched elsewhere gets a plain black curtain', () => {
    useStore.setState({ sessionGameKey: 'Zelda.iso', sessionSystemId: 'dolphin', awaitingWindow: '' })
    render(<LaunchCurtain />)
    expect(screen.getByTestId('launch-curtain')).toBeTruthy()
    expect(screen.queryByText(/Starting/)).toBeNull()
  })
})
