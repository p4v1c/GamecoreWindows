import { useState, useEffect, useRef, useCallback } from 'react'
import { Overlay, BackHeader } from '../../ui'
import { api } from '../../../api'
import { onGp } from '../../../hooks/useGamepad'
import { onWsEvent } from '../../../hooks/useWebSocket'
import { useSubPageGamepad } from './useSubPageGamepad'

export function UpdatePage({ onClose, onBack }: { onClose: () => void; onBack: () => void }) {
  const [info, setInfo] = useState<{ update_available: boolean; current: string; latest: string } | null>(null)
  const [checkError, setCheckError] = useState('')
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const [focusIdx, setFocusIdx] = useState(0)
  const logEndRef = useRef<HTMLDivElement>(null)

  const focusIdxRef  = useRef(focusIdx)
  const checkingRef  = useRef(checking)
  const installingRef = useRef(installing)
  const infoRef      = useRef(info)
  useEffect(() => { focusIdxRef.current  = focusIdx  }, [focusIdx])
  useEffect(() => { checkingRef.current  = checking  }, [checking])
  useEffect(() => { installingRef.current = installing }, [installing])
  useEffect(() => { infoRef.current      = info      }, [info])

  useSubPageGamepad(onBack, onClose)

  useEffect(() => {
    const btnCount = () => infoRef.current?.update_available ? 2 : 1
    const offs = [
      onGp('gp:dpad-left',  () => setFocusIdx(i => Math.max(0, i - 1))),
      onGp('gp:dpad-right', () => setFocusIdx(i => Math.min(btnCount() - 1, i + 1))),
      onGp('gp:confirm', () => {
        if (checkingRef.current || installingRef.current) return
        if (focusIdxRef.current === 0) checkRef.current()
        else if (focusIdxRef.current === 1) applyRef.current()
      }),
    ]
    return () => offs.forEach(o => o())
  }, [])

  const checkRef = useRef<() => void>(() => {})
  const applyRef = useRef<() => void>(() => {})

  const check = useCallback(async () => {
    setChecking(true); setCheckError('')
    try {
      setInfo(await api.update.check())
    } catch (e: unknown) {
      setInfo(null)
      const msg = e instanceof Error ? e.message : String(e)
      setCheckError(msg.includes('503') ? 'Couldn’t reach GitHub. Check the network, then try again.' : `Check failed: ${msg}`)
    }
    setChecking(false)
  }, [])

  // Auto-check on open
  useEffect(() => { check() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Ask the backend whether an update is already running, on mount and then
  // periodically. `installing` alone was component state: leaving this page and
  // coming back re-mounted it as false, the Install button went live again, and
  // a second update run started on top of the first.
  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const { running } = await api.update.status()
        if (alive) setInstalling(running)
      } catch { /* keep whatever we have */ }
    }
    poll()
    const t = setInterval(poll, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  // Subscribe to update log stream
  useEffect(() => {
    const off1 = onWsEvent('update:log', d => {
      setLog(prev => [...prev, d.line as string])
      setTimeout(() => logEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 30)
    })
    const off2 = onWsEvent('update:done', () => setInstalling(false))
    return () => { off1(); off2() }
  }, [])

  const apply = async () => {
    setLog([])
    setInstalling(true)
    try {
      await api.update.apply()
    } catch (e: unknown) {
      // 409 means one is already running — stay in the installing state and
      // keep following its log rather than re-enabling the button.
      const msg = e instanceof Error ? e.message : String(e)
      if (msg.includes('409')) {
        setLog(prev => [...prev, '[update] An update is already running. Following it.'])
        return
      }
      setInstalling(false)
    }
  }

  checkRef.current = check
  applyRef.current = apply

  return (
    <Overlay onClose={onClose}>
      <BackHeader label="Update" onBack={onBack} />

      {/* Error banner */}
      {checkError && !checking && (
        <div style={{ padding: '12px 16px', borderRadius: 10, marginBottom: 14, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: 15 }}>
          {checkError}
        </div>
      )}

      {/* Version banner */}
      {info && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '14px 18px', borderRadius: 12, marginBottom: 14,
          background: info.update_available ? 'rgba(74,222,128,0.08)' : 'rgba(255,255,255,0.04)',
          border: info.update_available ? '1px solid rgba(74,222,128,0.3)' : '1px solid rgba(255,255,255,0.08)',
        }}>
          <div>
            <div style={{ fontSize: 14, color: 'var(--gc-ink-3)', marginBottom: 4 }}>Installed</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: '#fff' }}>{info.current}</div>
          </div>
          {info.update_available ? (
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 14, color: 'rgba(74,222,128,0.7)', marginBottom: 4 }}>Available</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#4ade80' }}>{info.latest}</div>
            </div>
          ) : (
            <div style={{ fontSize: 15, color: '#4ade80', fontWeight: 600 }}>Up to date</div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
        <button onClick={check} disabled={checking || installing} style={{
          flex: 1, padding: '10px', borderRadius: 10, cursor: 'pointer', fontSize: 15, fontWeight: 600,
          background: 'color-mix(in srgb, var(--gc-accent, #b8501b) 15%, transparent)', color: 'var(--gc-accent-bright, #f8cfa9)',
          border: focusIdx === 0 ? '2px solid color-mix(in srgb, var(--gc-accent, #b8501b) 90%, transparent)' : '1px solid color-mix(in srgb, var(--gc-accent, #b8501b) 35%, transparent)',
          outline: 'none', opacity: checking || installing ? 0.5 : 1,
        }}>
          {checking ? 'Checking…' : 'Check again'}
        </button>
        {info?.update_available && (
          <button onClick={apply} disabled={installing} style={{
            flex: 1, padding: '10px', borderRadius: 10, cursor: 'pointer', fontSize: 15, fontWeight: 700,
            background: installing ? 'rgba(74,222,128,0.08)' : 'rgba(74,222,128,0.15)',
            border: focusIdx === 1 ? '2px solid rgba(74,222,128,0.9)' : '1px solid rgba(74,222,128,0.4)',
            outline: 'none', color: '#4ade80',
            opacity: installing ? 0.6 : 1,
          }}>
            {installing ? 'Installing…' : 'Install update'}
          </button>
        )}
      </div>

      {log.length > 0 && (
        <div style={{ fontFamily: 'monospace', fontSize: 15, color: 'var(--gc-ink-2)', background: 'rgba(0,0,0,0.35)', borderRadius: 8, padding: 12, maxHeight: 200, overflowY: 'auto' }}>
          {log.map((l, i) => <div key={i} style={{ marginBottom: 2 }}>{l}</div>)}
          <div ref={logEndRef} />
        </div>
      )}
    </Overlay>
  )
}
