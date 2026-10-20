import { useState, useEffect, useRef, useCallback } from 'react'
import { Overlay, OverlayLabel, Glyph } from '../ui'
import { useStore } from '../../store'
import { onGp } from '../../hooks/useGamepad'
import { api } from '../../api'
import { fetchThemeIndex } from '../../lib/themeLoader'
import { WifiPage }      from './settings/WifiPage'
import { AudioPage }     from './settings/AudioPage'
import { BluetoothPage } from './settings/BluetoothPage'
import { UpdatePage }    from './settings/UpdatePage'
import { DesktopPage }   from './settings/DesktopPage'
import { StandbyPage }   from './settings/StandbyPage'
import { ThemesPage } from './settings/ThemesPage'
import { CatalogPage } from './settings/CatalogPage'
import { BiosPage } from './settings/BiosPage'
import { StoragePage } from './settings/StoragePage'
import { LogsPage } from './settings/LogsPage'
import { PadHints } from '../../lib/padKey'
import { biosSummary } from '../../settings/bios'

interface Props { onClose: () => void }

type Page = 'main' | 'wifi' | 'audio' | 'bluetooth' | 'storage' | 'standby' | 'themes' | 'catalog' | 'bios' | 'update' | 'logs' | 'desktop'

const ITEMS = [
  { id: 'wifi',      label: 'Wi-Fi',            sub: 'Networks and passwords' },
  { id: 'audio',     label: 'Audio',            sub: 'Volume, output and UI sounds' },
  { id: 'bluetooth', label: 'Bluetooth',        sub: 'Devices and pairing' },
  { id: 'storage',   label: 'Storage',          sub: 'External disks and safe eject' },
  { id: 'standby',   label: 'Standby',          sub: 'Screensaver and low power' },
  { id: 'themes',    label: 'Themes',           sub: 'Change the look of the UI' },
  { id: 'catalog',   label: 'Emulators & apps', sub: 'Add or remove systems' },
  { id: 'bios',      label: 'BIOS',             sub: 'System files each console needs' },
  { id: 'update',    label: 'Update',           sub: 'Check for a new version' },
  { id: 'logs',      label: 'Logs',             sub: 'Size, and purge' },
  { id: 'desktop',   label: 'Desktop Mode',     sub: 'Leave for the Windows desktop', danger: true },
]

export default function SettingsModal({ onClose }: Props) {
  const [page, setPage] = useState<Page>('main')
  const [focusIdx, setFocusIdx] = useState(0)
  const [meta, setMeta] = useState<Record<string, string>>({})
  const { openModal, closeModal } = useStore()

  /**
   * The live value at the end of each row — the SSID you are on, how many pads
   * answered, how many BIOS sets are complete.
   *
   * Ten independent reads, each landing on its own: one service being down
   * leaves one row without a value and the others intact. Nothing falls back to
   * a plausible string, and a row whose endpoint did not answer shows nothing
   * rather than a dash — a dash reads as a measurement of "none".
   *
   * This is the fallback UI, so every one of them is caught: a settings menu
   * that fails to open because a service is unreachable is the last thing this
   * screen may do.
   */
  useEffect(() => {
    let alive = true
    const put = (k: string, v?: string) => { if (alive && v) setMeta(m => ({ ...m, [k]: v })) }

    api.wifi.status()
      .then(s => put('wifi', s.connected ? s.ssid
        : s.ethernet?.connected ? 'Wired' : 'Not connected')).catch(() => {})
    api.bluetooth.devices()
      .then(d => put('bluetooth', `${d.filter(x => x.connected).length} connected`)).catch(() => {})
    api.audio.sinks()
      .then(s => put('audio', s.find(x => x.default)?.name)).catch(() => {})
    api.storage.list()
      .then(r => put('storage', `${(r.volumes ?? []).length} external`)).catch(() => {})
    api.standby.get()
      .then(s => put('standby', s.enabled ? `On, ${s.screensaver_mins} min` : 'Off')).catch(() => {})
    api.catalog.list()
      .then(c => put('catalog', `${c.filter(x => x.installed).length} installed`)).catch(() => {})
    api.bios.list()
      .then(b => put('bios', biosSummary(b))).catch(() => {})
    api.sysinfo().then(s => put('update', `v${s.version}`)).catch(() => {})
    fetchThemeIndex()
      .then(i => put('themes', i.themes.find(t => t.id === i.active)?.name ?? 'Default'))
      .catch(() => {})

    return () => { alive = false }
  }, [])

  const back = useCallback(() => setPage('main'), [])

  // Stable ref for focusIdx to avoid stale closures
  const focusIdxRef = useRef(focusIdx)
  useEffect(() => { focusIdxRef.current = focusIdx }, [focusIdx])

  // Gamepad — only active on the main page
  useEffect(() => {
    if (page !== 'main') return
    const offs = [
      onGp('gp:dpad-up',   () => setFocusIdx(i => Math.max(0, i - 1))),
      onGp('gp:dpad-down', () => setFocusIdx(i => Math.min(ITEMS.length - 1, i + 1))),
      onGp('gp:confirm',   () => setPage(ITEMS[focusIdxRef.current].id as Page)),
      onGp('gp:back',      onClose),
      onGp('gp:menu',      onClose),
    ]
    return () => offs.forEach(o => o())
  }, [page, onClose])

  if (page === 'wifi')      return <WifiPage      onClose={onClose} onBack={back} />
  if (page === 'audio')     return <AudioPage     onClose={onClose} onBack={back} />
  if (page === 'bluetooth') return <BluetoothPage onClose={onClose} onBack={back} />
  if (page === 'storage')   return <StoragePage   onClose={onClose} onBack={back} />
  if (page === 'standby')   return <StandbyPage   onClose={onClose} onBack={back} />
  if (page === 'themes')    return <ThemesPage    onClose={onClose} onBack={back} />
  if (page === 'catalog')   return <CatalogPage   onClose={onClose} onBack={back} />
  if (page === 'bios')      return <BiosPage      onClose={onClose} onBack={back} />
  if (page === 'update')    return <UpdatePage    onClose={onClose} onBack={back} />
  if (page === 'logs')      return <LogsPage      onClose={onClose} onBack={back} />
  if (page === 'desktop')   return <DesktopPage   onClose={onClose} onBack={back} />

  return (
    <Overlay onClose={onClose} width={560}>
      <OverlayLabel text="Settings" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {ITEMS.map((it, idx) => {
          const on = idx === focusIdx
          const danger = (it as typeof it & { danger?: boolean }).danger
          return (
          <div key={it.id} onClick={() => setPage(it.id as Page)} style={{
            // A fixed height, so ten rows read as one column rather than as ten
            // cards of different sizes — the subtitles are not the same length
            // and the tallest one used to set its own rhythm.
            display: 'flex', alignItems: 'center', gap: 16, padding: '0 18px',
            height: 66, borderRadius: 14, cursor: 'pointer',
            background: on ? 'color-mix(in srgb, var(--gc-accent, #b8501b) 15%, transparent)' : 'rgba(255,255,255,0.04)',
            border: on ? '1px solid color-mix(in srgb, var(--gc-accent, #b8501b) 40%, transparent)' : '1px solid rgba(255,255,255,0.07)',
            transition: 'all 0.15s',
          }}>
            <div style={{
              flexShrink: 0, width: 38, height: 38, borderRadius: 11,
              display: 'grid', placeItems: 'center',
              background: on ? 'color-mix(in srgb, var(--gc-accent, #b8501b) 22%, transparent)' : 'rgba(255,255,255,0.05)',
              color: danger ? '#fca5a5' : on ? 'var(--gc-accent-bright, #f8cfa9)' : 'rgba(255,255,255,0.55)',
            }}>
              <Glyph name={it.id} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 600, color: danger ? '#fca5a5' : '#fff' }}>{it.label}</div>
              <div style={{
                fontSize: 15, color: 'var(--gc-ink-3)', marginTop: 3,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{it.sub}</div>
            </div>
            {meta[it.id] && (
              <div style={{
                fontFamily: 'ui-monospace, monospace', fontSize: 15,
                color: 'var(--gc-accent-bright, #f8cfa9)', textAlign: 'right',
                maxWidth: '38%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{meta[it.id]}</div>
            )}
            <div style={{ color: on ? 'var(--gc-accent-bright, #f8cfa9)' : 'rgba(255,255,255,0.25)', fontSize: 22 }}>›</div>
          </div>
        )})}
      </div>
      <div style={{ marginTop: 12, textAlign: 'center', fontSize: 14, color: 'var(--gc-ink-3)', }}>
        <PadHints text="↑↓ Navigate · ✕ Select · ○ Close" />
      </div>
    </Overlay>
  )
}
