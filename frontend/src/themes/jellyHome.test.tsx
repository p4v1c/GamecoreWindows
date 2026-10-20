/**
 * Jelly's dashboard and library, against the real host and the real SDK.
 *
 * Mounted through HomeScreen and LibraryScreen with the same omissions
 * index.js declares, so the host's own bindings are live underneath and a
 * press answered twice shows up here. What it looks like on a TV is checked
 * in a browser, not claimed below.
 */
import { render, act, cleanup } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React, { createElement } from 'react'
import { buildSdk } from '../lib/themeSdk'
import { useStore } from '../store'
import HomeScreen from '../components/HomeScreen'
import LibraryScreen from '../components/LibraryScreen'
import DefaultShell from '../components/DefaultShell'
import { api } from '../api'

const THEME = '../../../config/themes/jelly'

const SYSTEMS = [
  { id: 'azahar', label: 'Azahar', kind: 'emulator' as const,
    art: { console: '/api/systems/azahar/art/console?v=1' } },
  { id: 'duckstation', label: 'DuckStation', kind: 'emulator' as const, art: {} },
  { id: 'youtube', label: 'YouTube', kind: 'app' as const, iconPath: 'assets/logos/youtube.png' },
]
const GAMES: Record<string, unknown[]> = {
  azahar: [
    { filename: 'Mario Kart 7 (Europe).3ds', display_name: 'Mario Kart 7', path: '/r/mk7.3ds' },
    { filename: 'Pokemon X (Europe).3ds', display_name: 'Pokémon X', path: '/r/px.3ds' },
  ],
  duckstation: [
    { filename: 'Crash Bandicoot (Europe).chd', display_name: 'Crash Bandicoot', path: '/r/crash.chd' },
  ],
}
const PLAYTIME = [
  { game_key: 'Crash Bandicoot (Europe).chd', system_id: 'duckstation', total_secs: 1680,
    session_count: 2, last_played: '2026-09-28T19:00:00Z' },
]

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
    const games = url.match(/\/systems\/([^/]+)\/games/)
    const one = url.match(/\/systems\/([^/]+)$/)
    // Mario Kart 7 has a 3D box on this box; nothing else does.
    const media = url.match(/\/media\/[^/]+\/([^/]+)$/)
    const body: unknown = media
      ? { available: true, media: decodeURIComponent(media[1]).startsWith('Mario Kart')
        ? { 'box-3d': { category: 'box', kind: 'image', cached: true } } : {} }
      :
      url.includes('/playtime') ? PLAYTIME
        : games ? (GAMES[decodeURIComponent(games[1])] ?? [])
          : one ? SYSTEMS.find(x => x.id === decodeURIComponent(one[1]))
          : url.endsWith('/systems') ? SYSTEMS
            : url.includes('/metadata') ? { found: false }
              : url.includes('/sysinfo') ? { ip: '10.0.0.2', controllers: [] }
                : []
    return { ok: true, status: 200, statusText: 'OK', json: async () => body }
  }))
  useStore.setState({
    screen: 'home', selectedSystemId: null, selectedGameIdx: 0, gridFocusIdx: 0, gridPage: 0,
    modalDepth: 0, sessionGameKey: null, sessionSystemId: null, transition: null,
    backgroundSessions: [], powerPending: null, standby: 'off',
  })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

const settle = () => act(async () => { for (let i = 0; i < 4; i++) await new Promise(r => setTimeout(r, 0)) })

const press = async (event: string, n = 1) => {
  await act(async () => {
    for (let i = 0; i < n; i++) window.dispatchEvent(new CustomEvent(event))
    await new Promise(r => setTimeout(r, 0))
  })
}

const focused = () => (document.activeElement as HTMLElement | null)?.dataset?.nav

async function jelly() {
  const sdk = buildSdk('jelly', { selectTheme: vi.fn(async () => {}) })
  const launchGame = vi.fn(async () => {})
  ;(sdk.defaults as Record<string, unknown>).launchGame = launchGame
  const mod = await import(/* @vite-ignore */ `${THEME}/index.js`)
  const theme = mod.default(sdk)
  // The same parts the shell renders, from the same factory.
  const { ctx, Home, Library } = mod.createParts(sdk)
  const tabs = ctx.tabs
  return { sdk, theme, tabs, Home, Library, launchGame }
}

async function mountHome() {
  const j = await jelly()
  const r = render(createElement('div', null,
    createElement(HomeScreen as unknown as React.ComponentType<Record<string, unknown>>,
      { view: j.Home, omit: ['nav', 'pages', 'confirm'], onLaunchApp: vi.fn() }),
    createElement(LibraryScreen as unknown as React.ComponentType<Record<string, unknown>>,
      { view: j.Library, omit: ['nav', 'confirm', 'sort'] })))
  await settle()
  return { ...r, ...j }
}

describe('the module', () => {
  it('provides both surfaces and the session parts', async () => {
    const { theme } = await jelly()
    expect(typeof theme.splash).toBe('function')
    expect(typeof theme.shell).toBe('function')
    expect(typeof theme.sessionBar).toBe('function')
    expect(typeof theme.sessionMenu).toBe('function')
    expect(typeof theme.ceremony).toBe('function')
  })
})

describe('Jouer', () => {
  it('builds its hero from what was actually played, not from a bundled list', async () => {
    const { container } = await mountHome()
    expect(container.querySelector('.jl-hero-game')?.textContent).toContain('Crash Bandicoot')
    const titles = [...container.querySelectorAll('.jl-card-caption strong')].map(e => e.textContent)
    expect(titles).toEqual(['Crash Bandicoot'])
    expect(container.textContent).not.toContain('Chrono Trigger')
  })

  it('starts the pad on the hero button and launches the real ROM', async () => {
    const { launchGame } = await mountHome()
    expect(focused()).toBe('hero-play')
    await press('gp:confirm')
    expect(launchGame).toHaveBeenCalledWith(
      { systemId: 'duckstation', path: '/r/crash.chd', gameKey: 'Crash Bandicoot (Europe).chd' })
  })

  it('resumes a suspended game instead of starting it twice', async () => {
    const { launchGame, sdk } = await mountHome()
    const resume = vi.spyOn(sdk.session as { resume: (n?: number) => Promise<unknown> }, 'resume')
      .mockResolvedValue({})
    await act(async () => {
      useStore.setState({ backgroundSessions: [{ gameKey: 'Crash Bandicoot (Europe).chd',
        systemId: 'duckstation', session: 7, kind: 'game' }] } as never)
    })
    await settle()
    expect(document.querySelector('.jl-pill')?.textContent).toContain('Paused')
    ;(document.querySelector('[data-nav="hero-play"]') as HTMLElement).focus()
    await press('gp:confirm')
    expect(resume).toHaveBeenCalledWith(7)
    expect(launchGame).not.toHaveBeenCalled()
  })
})

describe('the tabs', () => {
  it('opens Settings from Play even when a game is focused', async () => {
    const sdk = buildSdk('jelly', { selectTheme: vi.fn(async () => {}) })
    const mod = await import(/* @vite-ignore */ `${THEME}/index.js`)
    const { ctx, Home, Library } = mod.createParts(sdk)
    const choices = vi.spyOn(api.perGame, 'state').mockRejectedValue(new Error('unavailable'))
    const Inert = () => null
    const r = render(createElement(DefaultShell as React.ComponentType<Record<string, unknown>>, {
      topbar: Inert, homeView: Home, libraryView: Library,
      homeGameOptions: () => ctx.actions.gameOptions?.() || null,
      settings: () => createElement('div', null, 'Settings screen'),
    }))
    await settle()
    expect(focused()).toBe('hero-play')
    await press('gp:menu')
    expect(r.container.textContent).toContain('Settings screen')
    expect(r.container.textContent).not.toContain('Game options')
    expect(choices).not.toHaveBeenCalled()
  })

  it('opens the host game options for the focused Collection card', async () => {
    const sdk = buildSdk('jelly', { selectTheme: vi.fn(async () => {}) })
    const mod = await import(/* @vite-ignore */ `${THEME}/index.js`)
    const { ctx, Home, Library } = mod.createParts(sdk)
    vi.spyOn(api.perGame, 'state').mockRejectedValue(new Error('unavailable'))
    const Inert = () => null
    const r = render(createElement(DefaultShell as React.ComponentType<Record<string, unknown>>, {
      topbar: Inert, homeView: Home, libraryView: Library,
      homeGameOptions: () => ctx.actions.gameOptions?.() || null,
      settings: () => createElement('div', null, 'Settings screen'),
    }))
    await settle()
    await press('gp:r1')
    const card = r.container.querySelector('[data-nav="g-duckstation:Crash Bandicoot (Europe).chd"]') as HTMLElement
    expect(card).toBeTruthy()
    act(() => card.focus())
    await press('gp:menu')
    expect(r.container.textContent).toContain('Game options')
    expect(r.container.textContent).toContain('Crash Bandicoot')
    expect(r.container.textContent).not.toContain('Settings screen')
    expect(api.perGame.state).toHaveBeenCalledWith('duckstation', 'Crash Bandicoot (Europe).chd')
  })

  it('walk with L1 and R1, and ○ comes back to Play', async () => {
    const { container } = await mountHome()
    await press('gp:r1')
    expect(container.querySelector('.jl-collection')).toBeTruthy()
    await press('gp:r1')
    expect(container.querySelector('.jl-consoles-page')).toBeTruthy()
    await press('gp:r1')
    expect(container.querySelector('.jl-playpage')).toBeTruthy()
    await press('gp:l1')
    await press('gp:back')
    expect(container.querySelector('.jl-playpage')).toBeTruthy()
  })

  it('Collection lists every game and filters favourites', async () => {
    const { container } = await mountHome()
    await press('gp:r1')
    expect(container.querySelectorAll('.jl-grid .jl-card')).toHaveLength(3)
    const { toggleFavourite } = await import(/* @vite-ignore */ `${THEME}/lib/favourites.js`)
    await act(async () => { toggleFavourite('azahar', 'Pokemon X (Europe).3ds') })
    ;(container.querySelector('[data-nav="f-fav"]') as HTMLElement).click()
    await settle()
    const titles = [...container.querySelectorAll('.jl-grid .jl-card strong')].map(e => e.textContent)
    expect(titles).toEqual(['Pokémon X'])
    expect(JSON.parse(localStorage.getItem('jelly-favourites') || '[]'))
      .toEqual(['azahar:Pokemon X (Europe).3ds'])
  })

  it('Consoles shows a photo per console and the applications after them', async () => {
    const { container } = await mountHome()
    await press('gp:l1')
    // The photo is the pack's (system.art.console); a pack without one shows
    // its mark, never a picture the theme carries itself.
    const photos = [...container.querySelectorAll('img.jl-console-photo')].map(e => e.getAttribute('src'))
    expect(photos).toEqual(['/api/systems/azahar/art/console?v=1'])
    expect(container.querySelector('.jl-picture-mark.jl-console-photo')?.textContent).toBe('PS')
    expect(container.querySelectorAll('.jl-app')).toHaveLength(1)
  })
})

describe('the jackets', () => {
  const srcOf = (container: HTMLElement, title: string) => {
    const card = [...container.querySelectorAll('.jl-grid .jl-card')]
      .find(c => c.querySelector('strong')?.textContent === title)
    return card?.querySelector('.jl-jacket img')?.getAttribute('src') ?? ''
  }

  it('stand on the 3D box when the game has one, and the flat jacket otherwise', async () => {
    const { container } = await mountHome()
    await press('gp:r1')
    await settle()
    expect(srcOf(container, 'Mario Kart 7')).toContain('/media/box-3d')
    expect(container.querySelector('.jl-jacket[data-kind="3d"]')).toBeTruthy()
    expect(srcOf(container, 'Crash Bandicoot')).toContain('/api/covers/duckstation/')
    // No box-3d: the flat jacket is drawn as a box rather than shown flat.
    const crash = [...container.querySelectorAll('.jl-grid .jl-card')]
      .find(c => c.querySelector('strong')?.textContent === 'Crash Bandicoot')
    expect(crash?.querySelector('.jl-jacket')?.getAttribute('data-kind')).toBe('drawn')
  })

  it('go flat for every game when the player says so, and remember it', async () => {
    const { container } = await mountHome()
    await press('gp:r1')
    await settle()
    ;(container.querySelector('[data-nav="f-style"]') as HTMLElement).click()
    await settle()
    expect(srcOf(container, 'Mario Kart 7')).toContain('/api/covers/azahar/')
    expect(container.querySelector('.jl-jacket[data-kind="drawn"]')).toBeNull()
    expect(localStorage.getItem('jelly-jacket')).toBe('box-front')
    ;(container.querySelector('[data-nav="f-style"]') as HTMLElement).click()
    await settle()
  })
})

describe('a console\'s library', () => {
  it('opens from Consoles, and ○ goes back to Consoles rather than to Play', async () => {
    // One ○ reaches the host (library → home) and then Jelly's Consoles tab,
    // which would read the new screen and leave for Jouer on the same press.
    const { container } = await mountHome()
    await press('gp:l1')
    ;(container.querySelector('[data-nav="c-azahar"]') as HTMLElement).focus()
    await press('gp:confirm')
    await settle()
    expect(useStore.getState().screen).toBe('library')
    expect(container.querySelectorAll('.jl-library .jl-card')).toHaveLength(2)
    await press('gp:back')
    await settle()
    expect(useStore.getState().screen).toBe('home')
    expect(container.querySelector('.jl-consoles-page')).toBeTruthy()
    expect(focused()).toBe('c-azahar')
  })

  it('keeps the host\'s selection on the focused card, so Options and Play act on it', async () => {
    const { container } = await mountHome()
    await act(async () => { useStore.getState().goLibrary('azahar') })
    await settle()
    const cards = container.querySelectorAll('.jl-library .jl-card')
    ;(cards[1] as HTMLElement).focus()
    expect(useStore.getState().selectedGameIdx).toBe(1)
  })
})

describe('the pad', () => {
  it('a step goes straight along the row before it goes diagonally', async () => {
    const { nextInDirection } = await import(/* @vite-ignore */ `${THEME}/lib/spatial.js`)
    const at = (left: number, top: number) => {
      const el = document.createElement('button')
      el.getBoundingClientRect = () => ({ left, top, right: left + 100, bottom: top + 60,
        width: 100, height: 60, x: left, y: top, toJSON: () => ({}) }) as DOMRect
      return el
    }
    const from = at(0, 0)
    const sameRow = at(400, 0)
    const closerBelow = at(130, 70)
    expect(nextInDirection(from, [from, closerBelow, sameRow], [1, 0])).toBe(sameRow)
    // Nothing in line: the nearest one off to the side is still reachable.
    expect(nextInDirection(from, [from, closerBelow], [1, 0])).toBe(closerBelow)
    // Nothing that way at all: no step.
    expect(nextInDirection(sameRow, [from, sameRow], [1, 0])).toBeNull()
  })

  it('↑ in a scrolled list reaches the row above before the controls over the list', async () => {
    const { stepFrom } = await import(/* @vite-ignore */ `${THEME}/lib/spatial.js`)
    const at = (parent: HTMLElement, left: number, top: number) => {
      const el = document.createElement('button')
      el.getBoundingClientRect = () => ({ left, top, right: left + 100, bottom: top + 60,
        width: 100, height: 60, x: left, y: top, toJSON: () => ({}) }) as DOMRect
      parent.append(el)
      return el
    }
    const page = document.createElement('div')
    const grid = document.createElement('div')
    grid.style.overflowY = 'auto'
    page.append(grid)
    document.body.append(page)
    // The grid is scrolled: the row above sits higher than the filter chip.
    const chip = at(page, 0, 100)
    const hiddenAbove = at(grid, 0, -100)
    const card = at(grid, 0, 300)
    const firstRow = at(grid, 0, 200)
    expect(stepFrom(card, [chip, hiddenAbove, firstRow, card], [0, -1])).toBe(firstRow)
    expect(stepFrom(firstRow, [chip, hiddenAbove, firstRow, card], [0, -1])).toBe(hiddenAbove)
    // Nothing left above in the grid: the step leaves it for the chip.
    expect(stepFrom(firstRow, [chip, firstRow, card], [0, -1])).toBe(chip)
    page.remove()
  })

  it('the hint bar counts a controller that reconnects', async () => {
    let pads: unknown[] = []
    vi.stubGlobal('navigator', { ...navigator, getGamepads: () => pads })
    const { container } = await mountHome()
    expect(container.querySelector('.jl-pad-none')).toBeTruthy()
    pads = [{ index: 0, id: 'pad', connected: true }]
    await press('gp:connected')
    expect(container.querySelector('.jl-pad-none')).toBeNull()
    expect(container.querySelector('.jl-pad')?.textContent).toContain('P1')
    pads = []
    await press('gp:disconnected')
    expect(container.querySelector('.jl-pad-none')).toBeTruthy()
  })
})
