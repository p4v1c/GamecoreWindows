/**
 * The sound cascade: theme's sound → host's sound → silence.
 *
 * This is the layer that fixes the thing a themed shell could not fix from its
 * own side. The five UI sounds are fired by the input bus, which runs *under*
 * the theme — so a shell that had redrawn every screen still answered every
 * press with the stock bip, and there was no hook anywhere to take it over.
 *
 * What is pinned here is each step of that cascade and, just as importantly,
 * the two things a theme is not allowed to escape: the player's on/off switch
 * and their volume. A theme that can be louder than the slider says is a theme
 * that wakes the house at 2am, and the player's only recourse is uninstalling
 * it.
 *
 * The module memoises its AudioContext, so every test re-imports it fresh
 * against its own fake.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** An AudioContext that records what actually made a noise. */
function fakeAudio() {
  const started: string[] = []
  const gains: { value: number }[] = []
  const param = () => ({
    value: 0,
    setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(), cancelScheduledValues: vi.fn(),
  })
  // `connect` returns its argument: lib/sounds chains `osc.connect(g).connect(dest)`.
  const connect = <T>(n: T) => n
  const ctx = {
    state: 'running', currentTime: 0, sampleRate: 48000,
    destination: { id: 'destination' },
    resume: () => Promise.resolve(),
    createGain: () => {
      const g = { gain: param(), connect }
      gains.push(g.gain)
      return g
    },
    createOscillator: () => ({
      type: 'sine', frequency: param(), detune: param(), connect,
      start: () => { started.push('host-oscillator') }, stop: vi.fn(),
    }),
    createBufferSource: () => ({
      buffer: null, connect,
      start: () => { started.push('theme-sample') },
    }),
    decodeAudioData: async (b: ArrayBuffer) => ({ byteLength: b.byteLength }),
  }
  return { ctx, started, gains }
}

/** A fresh copy of the module wired to a fresh fake context. */
async function load() {
  vi.resetModules()
  const audio = fakeAudio()
  vi.stubGlobal('AudioContext', function () { return audio.ctx })
  const mod = await import('./sounds')
  mod.soundSettings.enabled = true
  mod.soundSettings.volume = 100
  return { ...audio, ...mod }
}

/** setThemeSounds decodes in the background; let those promises settle. */
const settle = () => new Promise(r => setTimeout(r, 0))

beforeEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('the cascade', () => {
  it("plays the theme's sound instead of the host's", async () => {
    const s = await load()
    const themeMove = vi.fn()
    s.setThemeSounds({ move: themeMove })

    s.playSound('move')

    expect(themeMove).toHaveBeenCalledOnce()
    // The whole point: the host's synthesized bip must NOT also fire. Both
    // playing at once is the bug this indirection exists to prevent — the
    // theme's sound layered over the one it was meant to replace.
    expect(s.started).not.toContain('host-oscillator')
  })

  it("falls back to the host for a name the theme did not declare", async () => {
    const s = await load()
    const themeMove = vi.fn()
    s.setThemeSounds({ move: themeMove })

    s.playSound('confirm')

    // Replacing one sound must not cost a theme the other four.
    expect(themeMove).not.toHaveBeenCalled()
    expect(s.started).toContain('host-oscillator')
  })

  it('is silent for a name neither the theme nor the host has', async () => {
    const s = await load()
    s.setThemeSounds({})

    s.playSound('coin')

    // Silence rather than a throw is what lets a theme add names of its own:
    // playSound('coin') from a theme that ships one plays it, and from a theme
    // that does not is a no-op.
    expect(s.started).toEqual([])
  })

  it('gives the name back to the host when the theme set is cleared', async () => {
    const s = await load()
    const themeMove = vi.fn()
    s.setThemeSounds({ move: themeMove })
    s.clearThemeSounds()

    s.playSound('move')

    // A default UI still answering in the voice of the theme you just escaped
    // reads as the rescue having failed.
    expect(themeMove).not.toHaveBeenCalled()
    expect(s.started).toContain('host-oscillator')
  })
})

describe("the player's settings win", () => {
  it('plays no theme sound when sound is off', async () => {
    const s = await load()
    const themeMove = vi.fn()
    s.setThemeSounds({ move: themeMove })
    s.soundSettings.enabled = false

    s.playSound('move')

    expect(themeMove).not.toHaveBeenCalled()
    expect(s.started).toEqual([])
  })

  it("hands the theme an output already scaled to the player's volume", async () => {
    const s = await load()
    let out: GainNode | undefined
    s.setThemeSounds({ move: (_ctx, node) => { out = node } })
    s.soundSettings.volume = 40

    s.playSound('move')

    // The theme synthesizes into this node, so the slider applies to a sound
    // the host never wrote. Read at play time, not at registration: the player
    // can move the slider in Settings → Audio while the theme is loaded.
    expect(out?.gain.value).toBeCloseTo(0.4)
  })

  it('re-reads the volume on every play', async () => {
    const s = await load()
    const seen: number[] = []
    s.setThemeSounds({ move: (_ctx, node) => seen.push(node.gain.value) })

    s.soundSettings.volume = 100
    s.playSound('move')
    s.soundSettings.volume = 20
    s.playSound('move')

    expect(seen).toEqual([1, 0.2])
  })
})

describe('a game on screen', () => {
  // Every UI sound, not only the bus's: settings rows, theme handlers and
  // `sdk.system.playSound` call playSound directly.
  it('silences the host and the theme while a game holds the screen', async () => {
    const s = await load()
    const themeMove = vi.fn()
    s.setThemeSounds({ move: themeMove })
    const { useStore } = await import('../store')
    useStore.getState().setSession('FIFA 19', 'rpcs3')

    s.playSound('move')
    s.playSound('confirm')

    expect(themeMove).not.toHaveBeenCalled()
    expect(s.started).toEqual([])
  })

  it('plays again once the game is suspended or closed', async () => {
    const s = await load()
    const { useStore } = await import('../store')
    useStore.getState().setSession('FIFA 19', 'rpcs3')
    useStore.getState().setSession(null, null)

    s.playSound('move')

    expect(s.started).toEqual(['host-oscillator'])
  })
})

describe('a theme that misbehaves does not take the press down with it', () => {
  it("falls back to the host's sound when the theme's throws", async () => {
    const s = await load()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    s.setThemeSounds({ move: () => { throw new Error('bad synth') } })

    expect(() => s.playSound('move')).not.toThrow()
    // emit() in the input bus calls playSound before dispatching the event, so
    // a throw here does not just lose a bip — it loses the button press.
    expect(s.started).toContain('host-oscillator')
  })

  it("falls back to the host's sound when the theme's file cannot be fetched", async () => {
    const s = await load()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })))

    s.setThemeSounds({ move: '/themes/t/assets/typo.wav' })
    await settle()
    s.playSound('move')

    // One typo'd path costs one bip, not the whole theme's sound.
    expect(s.started).toContain('host-oscillator')
  })
})

describe('a theme that ships an audio file', () => {
  it('decodes it up front and plays it in place of the host sound', async () => {
    const s = await load()
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, arrayBuffer: async () => new ArrayBuffer(8),
    })))

    s.setThemeSounds({ move: '/themes/t/assets/move.wav' })
    await settle()
    s.playSound('move')

    // Decoded at registration, not on first press: doing it lazily means the
    // press that triggers the decode is the one press that stays silent.
    expect(s.started).toEqual(['theme-sample'])
  })
})

describe('Rest mode', () => {
  // A running context holds an audio stream, and a Modern Standby PC stays busy for it.
  it('suspends the context and keeps it suspended until the wake', async () => {
    const s = await load()
    const calls: string[] = []
    Object.assign(s.ctx, {
      suspend: () => { calls.push('suspend'); s.ctx.state = 'suspended'; return Promise.resolve() },
      resume: () => { calls.push('resume'); s.ctx.state = 'running'; return Promise.resolve() },
    })
    s.playSound('move')
    s.setAudioAsleep(true)
    s.playSound('move')
    expect(calls).toEqual(['suspend'])
    s.setAudioAsleep(false)
    expect(calls).toEqual(['suspend', 'resume'])
  })
})
