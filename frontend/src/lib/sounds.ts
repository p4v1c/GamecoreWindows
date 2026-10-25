/**
 * UI sounds — synthesized with WebAudio (no audio assets, no fetch latency).
 *
 * playSound('move' | 'confirm' | 'back' | 'launch' | 'startup')
 *
 * Enabled state and volume persist in localStorage and are editable from
 * Settings → UI Sounds. Sounds are short and quiet by design — a console
 * "tick", not a phone notification.
 */

import { isPlaying } from '../store'

/** The five the host synthesizes. A theme may replace any of them, and may add
 *  names of its own — hence `string` wherever a sound is played. */
export type SoundName = 'move' | 'confirm' | 'back' | 'launch' | 'startup'

/**
 * What a theme may supply for a sound.
 *
 * A path is the common case and the only thing a manifest can express. A
 * function is there because the shipped themes already synthesize rather than
 * ship audio — Summer builds its surf out of filtered noise — and telling them
 * to bounce a wav to replace one bip would be a step backwards.
 *
 * The function is handed the shared context and an output node that is
 * *already* scaled to the player's volume. Connect to `out`, not to
 * `ctx.destination`: the second bypasses the volume slider, which is the one
 * thing §13 of the theme spec promises a player it cannot do.
 */
export type ThemeSound = string | ((ctx: AudioContext, out: GainNode) => void)

const LS_ENABLED = 'gc:uiSounds'
const LS_VOLUME = 'gc:uiSoundsVolume'

export const soundSettings = {
  get enabled(): boolean { return localStorage.getItem(LS_ENABLED) !== 'off' },
  set enabled(v: boolean) { localStorage.setItem(LS_ENABLED, v ? 'on' : 'off') },
  get volume(): number {
    const v = parseInt(localStorage.getItem(LS_VOLUME) ?? '60', 10)
    return isNaN(v) ? 60 : Math.max(0, Math.min(100, v))
  },
  set volume(v: number) { localStorage.setItem(LS_VOLUME, String(Math.max(0, Math.min(100, v)))) },
}

let ctx: AudioContext | null = null
let asleep = false

function audio(): AudioContext | null {
  if (!ctx) {
    try { ctx = new AudioContext() } catch { return null }
  }
  // Chromium may start the context suspended until a user gesture
  if (ctx.state === 'suspended' && !asleep) ctx.resume().catch(() => {})
  return ctx
}

/** Rest mode: suspend the shared context, and keep it suspended until the wake.
 *  A running context holds an audio stream open, and Windows keeps a Modern
 *  Standby PC busy for it (Kernel-Power 507 reported AudioPlaying). */
export function setAudioAsleep(value: boolean): void {
  asleep = value
  if (!ctx) return
  if (value) ctx.suspend().catch(() => {})
  else ctx.resume().catch(() => {})
}

/** The shared AudioContext — for callers that synthesize their own sounds
 *  (the boot splash). Never close it: the UI sounds live on it too. */
export const getAudioContext = audio

/** One enveloped oscillator note. */
function note(c: AudioContext, freq: number, at: number, dur: number, peak: number,
              type: OscillatorType = 'sine') {
  const osc = c.createOscillator()
  const gain = c.createGain()
  osc.type = type
  osc.frequency.value = freq
  gain.gain.setValueAtTime(0, at)
  gain.gain.linearRampToValueAtTime(peak, at + 0.008)
  gain.gain.exponentialRampToValueAtTime(0.0001, at + dur)
  osc.connect(gain).connect(c.destination)
  osc.start(at)
  osc.stop(at + dur + 0.05)
}

// ── The theme layer ──────────────────────────────────────────────────────────
//
// A theme supplies the *what*; the input bus keeps the *when*. That seam is the
// same one `homeView` draws — a theme changes the UI, never the behaviour — and
// it is why this indirection lives inside playSound rather than in useGamepad:
// every existing caller keeps calling playSound('move') and gets whatever the
// active theme decided 'move' sounds like, including the host's if it decided
// nothing.

/** Theme overrides, by sound name. String entries hold an already-resolved URL. */
let themeSounds: Record<string, ThemeSound> = {}

/** Decoded samples, keyed by the URL they came from. */
const sampleCache = new Map<string, AudioBuffer>()

/**
 * Install a theme's sound set. Entries whose file cannot be fetched or decoded
 * are dropped, so the cascade falls back to the host's sound for that name
 * instead of going silent — a theme with a typo'd path loses one bip, not all
 * of them.
 */
export function setThemeSounds(map: Record<string, ThemeSound>): void {
  themeSounds = { ...map }
  for (const [name, entry] of Object.entries(themeSounds)) {
    if (typeof entry !== 'string' || sampleCache.has(entry)) continue
    // Decoded up front: doing it on first press means the press that triggered
    // it is the one press that does not make a sound.
    fetch(entry)
      .then(r => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(`${r.status}`))))
      .then(b => audio()?.decodeAudioData(b))
      .then(buf => { if (buf) sampleCache.set(entry, buf) })
      .catch(e => {
        console.warn(`[gamecore] theme sound "${name}" (${entry}) unusable — using the host's:`, e)
        if (themeSounds[name] === entry) delete themeSounds[name]
      })
  }
}

/** Called when falling back to the default theme, like clearThemeStyles. */
export function clearThemeSounds(): void {
  themeSounds = {}
  sampleCache.clear()
}

/**
 * Play a theme's version of `name`. False means "the theme has nothing usable
 * for this", which is the caller's cue to fall through to the host's.
 */
function playThemeSound(name: string): boolean {
  const entry = themeSounds[name]
  if (entry === undefined) return false
  const c = audio()
  if (!c) return false

  // Read per play, not per registration: the player can move the slider in
  // Settings → Audio while a theme is loaded, and a gain fixed at install time
  // would ignore them until the next reload.
  const out = c.createGain()
  out.gain.value = soundSettings.volume / 100
  out.connect(c.destination)

  if (typeof entry === 'function') {
    // A theme throwing here must not take the press down with it.
    try { entry(c, out) } catch (e) {
      console.error(`[gamecore] theme sound "${name}" threw — using the host's:`, e)
      return false
    }
    return true
  }

  const buf = sampleCache.get(entry)
  if (!buf) return false   // still decoding, or it failed: the host covers it
  const src = c.createBufferSource()
  src.buffer = buf
  src.connect(out)
  src.start()
  return true
}

/**
 * Theme's sound → host's sound → silence.
 *
 * The last step is what lets a theme *add* names: playSound('coin') from a
 * theme that declares one plays it, and from a theme that does not is a no-op
 * rather than an error.
 */
export function playSound(name: SoundName | string) {
  // The one gate for every caller (bus, settings rows, theme handlers): a menu
  // sound over a game is heard as the game's, whichever path asked for it.
  if (!soundSettings.enabled || isPlaying()) return
  if (playThemeSound(name)) return
  const c = audio()
  if (!c) return
  const v = soundSettings.volume / 100
  const t = c.currentTime

  switch (name) {
    case 'move':
      note(c, 880, t, 0.05, 0.06 * v, 'sine')
      break
    case 'confirm':
      note(c, 523.25, t, 0.07, 0.10 * v, 'triangle')          // C5
      note(c, 659.25, t + 0.06, 0.10, 0.10 * v, 'triangle')   // E5
      break
    case 'back':
      note(c, 440, t, 0.06, 0.08 * v, 'triangle')             // A4
      note(c, 349.23, t + 0.05, 0.09, 0.08 * v, 'triangle')   // F4
      break
    case 'launch':
      // Rising arpeggio — the "here we go" chime
      note(c, 523.25, t, 0.12, 0.11 * v, 'triangle')
      note(c, 659.25, t + 0.09, 0.12, 0.11 * v, 'triangle')
      note(c, 783.99, t + 0.18, 0.14, 0.11 * v, 'triangle')
      note(c, 1046.5, t + 0.27, 0.30, 0.10 * v, 'sine')
      break
    case 'startup':
      // Soft C major swell
      note(c, 261.63, t, 1.1, 0.05 * v, 'sine')
      note(c, 329.63, t + 0.12, 1.0, 0.05 * v, 'sine')
      note(c, 392.0, t + 0.24, 0.9, 0.05 * v, 'sine')
      note(c, 523.25, t + 0.36, 0.9, 0.04 * v, 'sine')
      break
  }
}

/** Sound for a gamepad UI event name, or null for silent events. */
export function soundForGpEvent(event: string): SoundName | null {
  switch (event) {
    case 'gp:dpad-up':
    case 'gp:dpad-down':
    case 'gp:dpad-left':
    case 'gp:dpad-right':
    case 'gp:l1':
    case 'gp:r1':
    case 'gp:l2':
    case 'gp:r2':
      return 'move'
    case 'gp:confirm':
    case 'gp:menu':
    case 'gp:power':
    case 'gp:x':
      return 'confirm'
    case 'gp:back':
      return 'back'
    default:
      return null
  }
}
