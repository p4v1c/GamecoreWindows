import { useEffect, useRef } from 'react'
import { Overlay, BackHeader } from '../../ui'
import { onGp } from '../../../hooks/useGamepad'
import { useSubPageGamepad } from './useSubPageGamepad'
import { PadHints } from '../../../lib/padKey'

export function DesktopPage({ onClose, onBack }: { onClose: () => void; onBack: () => void }) {
  const exitRef = useRef<() => void>(() => {})

  const doExit = () => { window.gamecore?.quit(); window.close() }
  exitRef.current = doExit

  useSubPageGamepad(onBack, onClose)

  useEffect(() => {
    const off = onGp('gp:confirm', () => exitRef.current())
    return off
  }, [])

  return (
    <Overlay onClose={onClose}>
      <BackHeader label="Desktop mode" onBack={onBack} />
      <div style={{ padding: '20px 22px', borderRadius: 14, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', marginBottom: 24, color: '#fca5a5', fontSize: 15, lineHeight: 1.8 }}>
        Quit GameCore and hand the screen back to the Windows desktop.
      </div>
      <div style={{ padding: '14px 18px', borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', marginBottom: 24, color: 'var(--gc-ink-2)', fontSize: 15, lineHeight: 1.7 }}>
        {/* The way back has to be findable from the desktop itself. */}
        GameCore starts again at the next sign-in. To come back sooner, open
        the GameCore shortcut on the desktop.
      </div>
      <div onClick={doExit} style={{ padding: 16, borderRadius: 14, cursor: 'pointer', background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)', color: '#fca5a5', fontWeight: 700, textAlign: 'center', fontSize: 16 }}>
        ✕ Exit to desktop
      </div>
      <div style={{ marginTop: 8, textAlign: 'center', fontSize: 14, color: 'var(--gc-ink-3)', }}>
        <PadHints text="✕ Confirm · ○ Cancel" />
      </div>
    </Overlay>
  )
}
