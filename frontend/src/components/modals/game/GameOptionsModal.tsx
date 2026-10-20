import { useState, useEffect, useRef, useCallback } from 'react'
import { Overlay, OverlayLabel } from '../../ui'
import { api, type PerGameState } from '../../../api'
import { onGp } from '../../../hooks/useGamepad'

/**
 * Per-game options (Options in the library): this game's emulator settings.
 *
 * Settings: no sliders (one vocabulary per emulator would never stay current).
 * The panel reports the catalogue's known-good profile and lets the player
 * remove it, and opens the emulator's OWN settings window; what is set there
 * is kept for this game only. Changes apply at the next launch, and the panel
 * says so.
 */

const ACCENT = 'var(--gc-accent, #b8501b)'
const DIM = 'var(--gc-overlay-muted, rgba(255,255,255,0.35))'
const WARN = 'var(--gc-overlay-warn, #fbbf24)'

type Row = {
  /** Stable across re-renders; the focus index is positional, this is not. */
  key: string
  label: string
  hint: string
  selected: boolean
  run: () => void
}

export default function GameOptionsModal({ systemId, rom, title, onClose }: {
  systemId: string; rom: string; title: string; onClose: () => void
}) {
  const [perGame, setPerGame] = useState<PerGameState | null>(null)
  const [failed, setFailed] = useState(false)
  const [focus, setFocus] = useState(0)
  const [saving, setSaving] = useState(false)

  // The gamepad handlers are registered once and must not close over a stale
  // row list — the pad fires long before the fetch returns.
  const rowsRef = useRef<Row[]>([])
  const focusRef = useRef(0)
  useEffect(() => { focusRef.current = focus }, [focus])

  const refreshPerGame = useCallback(() => api.perGame.state(systemId, rom)
    .then(p => { setPerGame(p); setFailed(false) })
    .catch(() => { setPerGame(null); setFailed(true) }), [systemId, rom])

  useEffect(() => { refreshPerGame() }, [refreshPerGame])

  const actOnProfile = useCallback((action: 'remove' | 'restore') => {
    setSaving(true)
    api.perGame.profile(systemId, rom, action)
      .then(setPerGame)
      .catch(() => refreshPerGame())
      .finally(() => setSaving(false))
  }, [systemId, rom, refreshPerGame])

  const openSettings = useCallback(() => {
    setSaving(true)
    // The emulator takes the screen from here. Closing the panel first means
    // the player is not left with a modal underneath a foreign window they
    // then have to dismiss blind when the emulator quits.
    api.perGame.openSettings(systemId, rom)
      .then(onClose)
      .catch(() => refreshPerGame())
      .finally(() => setSaving(false))
  }, [systemId, rom, onClose, refreshPerGame])

  const gameRows: Row[] = []
  const profile = perGame?.profile
  if (profile?.available) {
    // Three sentences, not two. "Applied", "you removed it" and "it exists but
    // this box runs an emulator it does not claim to cover" are different
    // situations, and the third is the one a player would otherwise read as
    // the setting having silently failed.
    const hint = !profile.inRange
      ? `Not applied. Verified for ${profile.emulator}; this box runs `
        + `${profile.emulatorVersion ?? 'an unknown version'}`
      : profile.dismissed
        ? `Removed. ${profile.why}`
        : profile.why ?? ''
    gameRows.push({
      key: 'profile',
      label: profile.dismissed
        ? `Restore the setting for ${profile.label}`
        : `Remove the setting for ${profile.label}`,
      hint,
      selected: Boolean(profile.applied) && !profile.dismissed,
      run: () => actOnProfile(profile.dismissed ? 'restore' : 'remove'),
    })
  }
  if (perGame?.canOpenSettings) {
    gameRows.push({
      key: 'open',
      label: 'Open the emulator’s settings',
      hint: 'Set it there; GameCore keeps it for this game only',
      selected: false,
      run: openSettings,
    })
  }

  const rows = gameRows
  rowsRef.current = rows

  useEffect(() => {
    const offs = [
      onGp('gp:dpad-up', () => setFocus(f => Math.max(0, f - 1))),
      onGp('gp:dpad-down', () =>
        setFocus(f => Math.min(rowsRef.current.length - 1, f + 1))),
      onGp('gp:confirm', () => rowsRef.current[focusRef.current]?.run()),
      onGp('gp:back', onClose),
      // Options opened this panel; Options closes it. A modal a player cannot
      // leave with the button that opened it is one they leave by killing the box.
      onGp('gp:menu', onClose),
    ]
    return () => offs.forEach(off => off())
  }, [onClose])

  return (
    <Overlay onClose={onClose} width={520}>
      <OverlayLabel text="Game options" />
      <div style={{ fontSize: 17, fontWeight: 600, marginBottom: 4 }}>{title}</div>

      {failed && (
        <div style={{ fontSize: 15.5, color: WARN, margin: '16px 0' }}>
          The game options could not be read. The game still launches.
        </div>
      )}

      {rows.map((row, i) => {
        const focused = focus === i
        return (
          <div key={row.key}>
            {i === 0 && (
              <div style={{ fontSize: 14, color: DIM, margin: '20px 0 10px' }}>
                This game
              </div>
            )}
            <button
              onClick={row.run}
              onMouseEnter={() => setFocus(i)}
              disabled={saving}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, width: '100%',
                textAlign: 'left', cursor: 'pointer', marginBottom: 8,
                padding: '12px 14px', borderRadius: 12,
                background: focused ? 'var(--gc-overlay-row, rgba(255,255,255,0.07))' : 'transparent',
                border: `1px solid ${focused ? ACCENT : 'var(--gc-overlay-row-border, rgba(255,255,255,0.08))'}`,
                color: 'inherit', font: 'inherit',
              }}
            >
              <span style={{ width: 14, color: row.selected ? ACCENT : 'transparent' }}>
                ●
              </span>
              <span style={{ flex: 1 }}>
                <span style={{ fontSize: 14 }}>{row.label}</span>
                <span style={{ display: 'block', fontSize: 14.5, color: DIM,
                               marginTop: 2 }}>
                  {row.hint}
                </span>
              </span>
            </button>
          </div>
        )
      })}

      {perGame && !perGame.supported && perGame.why && (
        <div style={{ fontSize: 14.5, color: DIM, marginTop: 20, lineHeight: 1.5 }}>
          <span style={{ display: 'block', color: 'inherit', marginBottom: 4 }}>
            This system has no per-game settings.
          </span>
          {perGame.why}
        </div>
      )}

      {perGame?.supported && perGame.gameId === null && (
        <div style={{ fontSize: 14.5, color: DIM, marginTop: 20, lineHeight: 1.5 }}>
          This copy carries no identifier GameCore can read, so a setting could
          not be told apart from the next game’s. The emulator’s own settings
          still work.
        </div>
      )}

      <div style={{ fontSize: 14.5, color: DIM, marginTop: 18, lineHeight: 1.5 }}>
        Applies at the next launch.
      </div>
    </Overlay>
  )
}
