/**
 * Two screens that kept showing what they were told once.
 *
 * Findings 10 and 14 of the 2026-09-04 complementary audit. Different
 * files, same shape: a value held from an earlier event, still on screen after
 * the thing it described had changed.
 *
 *   · Summer built the URLs of the previous game's captures out of a selection
 *     that had just become null, and took the theme into its error boundary.
 *   · The dashboard is mounted behind the settings, so installing a pack from
 *     the catalogue page changed nothing on it until a restart.
 */
import React from 'react'
import { render, act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HomeScreen from './HomeScreen'
import { api } from '../api'
import { useStore } from '../store'
import type { HomeViewProps } from './HomeScreen/types'
import { bootSteps, resetBootForTests } from '../lib/boot'
import { buildSdk } from '../lib/themeSdk'

/** The websocket bus, replaced by something this file can fire by hand. */
const listeners = vi.hoisted(() => new Map<string, Set<(data: unknown) => void>>())
vi.mock('../hooks/useWebSocket', () => ({
  onWsEvent: (name: string, fn: (d: unknown) => void) => {
    if (!listeners.has(name)) listeners.set(name, new Set())
    listeners.get(name)!.add(fn)
    return () => listeners.get(name)?.delete(fn)
  },
}))
const emit = (name: string, data: unknown) =>
  act(() => { listeners.get(name)?.forEach(fn => fn(data)) })

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); listeners.clear()
  delete (window as { gamecore?: unknown }).gamecore
})

describe('the dashboard after a pack is installed', () => {
  const alpha = { id: 'azahar', kind: 'emulator' as const, label: '3DS' }
  const beta = { id: 'melonds', kind: 'emulator' as const, label: 'DS' }
  const Probe = (p: HomeViewProps) => (
    <div data-testid="systems">{p.systems.map(s => s.id).join(',')}</div>
  )

  it('shows the system that has just been installed', async () => {
    const list = vi.spyOn(api.systems, 'list').mockResolvedValue([alpha])
    vi.spyOn(api.playtime, 'all').mockResolvedValue([])
    vi.spyOn(api.games, 'list').mockResolvedValue([])
    useStore.setState({ screen: 'home', gridFocusIdx: 0, gridPage: 0, modalDepth: 0, sessionGameKey: null })

    const r = render(<HomeScreen view={Probe} onLaunchApp={() => {}} />); await flush()
    expect(r.getByTestId('systems').textContent).toBe('azahar')

    act(() => useStore.setState({ modalDepth: 1 }))       // the settings are open
    list.mockResolvedValue([alpha, beta])
    await emit('catalog:done', { action: 'install', id: 'melonds', success: true })
    act(() => useStore.setState({ modalDepth: 0 }))
    await flush()
    expect(r.getByTestId('systems').textContent).toBe('azahar,melonds')
  })

  it('keeps the cursor on the console it was on, not on its old position', async () => {
    const gamma = { id: 'pcsx2', kind: 'emulator' as const, label: 'PS2' }
    vi.spyOn(api.systems, 'list').mockResolvedValue([alpha, beta, gamma])
    vi.spyOn(api.playtime, 'all').mockResolvedValue([])
    vi.spyOn(api.games, 'list').mockResolvedValue([])
    useStore.setState({ screen: 'home', gridFocusIdx: 0, gridPage: 0, modalDepth: 0, sessionGameKey: null })

    render(<HomeScreen view={Probe} onLaunchApp={() => {}} />); await flush()
    act(() => useStore.setState({ gridFocusIdx: 2 }))      // on PS2
    vi.mocked(api.systems.list).mockResolvedValue([beta, gamma])   // 3DS removed
    await emit('catalog:done', { action: 'remove', id: 'azahar', success: true })
    await flush()
    expect(useStore.getState().gridFocusIdx).toBe(1)       // still PS2
  })

  it('ignores an operation that failed', async () => {
    const list = vi.spyOn(api.systems, 'list').mockResolvedValue([alpha])
    vi.spyOn(api.playtime, 'all').mockResolvedValue([])
    vi.spyOn(api.games, 'list').mockResolvedValue([])
    useStore.setState({ screen: 'home', gridFocusIdx: 0, gridPage: 0, modalDepth: 0, sessionGameKey: null })

    render(<HomeScreen view={Probe} onLaunchApp={() => {}} />); await flush()
    list.mockClear()
    await emit('catalog:done', { action: 'install', id: 'melonds', success: false })
    await flush()
    expect(list).not.toHaveBeenCalled()
  })
})

describe('Summer, when the selection goes away', () => {
  it('draws its empty list instead of crashing on the previous game', async () => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true, value: vi.fn(),
    })
    const path = '../../../config/themes/summer/views/library.js'
    const { createLibraryView } = await import(/* @vite-ignore */ path)
    vi.spyOn(api.media, 'list').mockResolvedValue(
      { found: true, media: { 'screenshot-gameplay': true } } as never)
    vi.spyOn(console, 'error').mockImplementation(() => {})

    const View = createLibraryView(buildSdk('summer', { selectTheme: async () => {} }))
    const game = { filename: 'Alpha.rom', display_name: 'Alpha', path: '/roms/Alpha.rom', ext: '.rom', size: 1 }
    const props = {
      systemId: 'gc', system: { id: 'gc', label: 'GameCube' }, games: [game], totalCount: 1,
      playtime: {}, selectedIdx: 0, detailGame: game, sort: 'name', search: '', loading: false,
      loadError: false, launching: false, color: '#7755aa',
      onSelect: () => {}, onSearch: () => {}, onSort: () => {}, onLaunch: () => {},
      onBack: () => {}, onRetry: () => {}, Cover: () => null, Meta: () => null,
    }

    class Boundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
      state = { failed: false }
      static getDerivedStateFromError() { return { failed: true } }
      render() { return this.state.failed ? <div>THEME CRASHED</div> : this.props.children }
    }

    const r = render(<Boundary><View {...props} /></Boundary>); await flush()
    expect(r.container.querySelector('.sm-lib-shot')).not.toBeNull()

    // The selection is cleared while the captures of the previous game are
    // still in the hook's state — a change of console, or a search that
    // matches nothing. The reset runs in an effect, which is too late.
    r.rerender(<Boundary><View {...props} games={[]} detailGame={null} /></Boundary>)
    await flush()
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
    expect(r.container.textContent).not.toContain('THEME CRASHED')
    expect(r.container.querySelector('.sm-lib-shot')).toBeNull()
  })
})

it('keeps the boot gate closed on systems failure, then accepts an empty successful retry', async () => {
  resetBootForTests()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  const list = vi.spyOn(api.systems, 'list').mockRejectedValue(new Error('HTTP 503'))
  vi.spyOn(api.playtime, 'all').mockResolvedValue([])
  vi.spyOn(api.games, 'list').mockResolvedValue([])
  useStore.setState({ screen: 'home', gridFocusIdx: 0, gridPage: 0, modalDepth: 0, sessionGameKey: null })
  render(<HomeScreen view={() => <div />} onLaunchApp={() => {}} />)
  await flush()
  expect(bootSteps().systems).toBe(false)
  list.mockResolvedValue([])
  emit('playtime:rekeyed', {})
  await flush()
  expect(bootSteps().systems).toBe(true)
  resetBootForTests()
})
