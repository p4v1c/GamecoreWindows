/**
 * Settings → System: the console image and its update, standby, logs and disks
 * (the `update`, `standby`, `storage` pages; `desktop` is in the power menu).
 *
 * Not drawn: emulator "Software update" (no update verb), `pacman -Syu` (a
 * NOPASSWD pacman rule is a root shell; see README), kernel version (not
 * read). The internal disk shows a size bar but no Eject.
 */
const SAVER_MINS = [2, 4, 6, 10, 15]
const SLEEP_MINS = [10, 16, 30, 60, 0]           // 0 = never
// After a wake from Rest mode: black this long once the TV link is back, while
// the TV finds the signal. Every TV differs; the backend clamps to 0-8 s.
const WAKE_SECS = [0, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8]
const secs = (s) => (s === 0 ? 'None' : `${s} s`)
const label = (m) => (m === 0 ? 'Never' : m >= 60 ? `${m / 60} h` : `${m} min`)
const megabytes = (b) => `${(b / 1048576).toFixed(b < 10485760 ? 1 : 0)} MB`
const nearest = (list, v) => {
  let best = 0
  list.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(list[best] - v)) best = i })
  return best
}

import { asList, versionLabel } from './list.js'

export const createSystemPage = (sdk, Rows) => {
  const { html, useState, useEffect } = sdk.ui

  return ({ active, onLeave, onLeft }) => {
    const [info, setInfo] = useState(null)
    const [standby, setStandby] = useState(null)
    const [volumes, setVolumes] = useState([])
    const [update, setUpdate] = useState(null)      // null = not checked yet
    const [busy, setBusy] = useState('')
    const [msg, setMsg] = useState('')
    const [logs, setLogs] = useState(null)

    const loadVolumes = () => sdk.api.storage.list()
      .then((r) => setVolumes(asList(r && r.volumes))).catch(() => {})

    useEffect(() => {
      sdk.api.sysinfo().then(setInfo).catch(() => {})
      sdk.api.standby.get().then(setStandby).catch(() => {})
      loadVolumes()
      sdk.api.logs.usage().then(setLogs).catch(() => {})
      // Whether one is already running is the backend's to know: the flag does
      // not survive leaving the page, and an update outlives that by minutes.
      sdk.api.update.status().then((r) => { if (r.running) setBusy('update') }).catch(() => {})
    }, [])

    const saverIdx = standby ? nearest(SAVER_MINS, standby.screensaver_mins) : 2
    const sleepIdx = standby ? nearest(SLEEP_MINS, standby.sleep_mins) : 1
    const wakeIdx = nearest(WAKE_SECS, standby && standby.wake_settle_secs != null
      ? standby.wake_settle_secs : 2.5)

    const rows = [
      {
        id: 'update', type: 'action',
        label: 'System update',
        desc: info
          ? `GameCore ${versionLabel(info.version)}${update === null ? ''
              : update.update_available ? `. ${versionLabel(update.latest)} is available`
              : ', up to date'}`
          : 'Checking this box’s version…',
        label2: update && update.update_available ? `Install ${update.latest}` : 'Check for updates',
        busy: busy === 'update' ? 'Working…' : '',
        // Installing replaces the running front end and restarts it. That is
        // not a keypress you want to be able to make by accident.
        confirm: !!(update && update.update_available),
      },
      ...(standby ? [
        {
          id: 'sb_on', type: 'toggle', value: !!standby.enabled,
          label: 'Standby mode',
          desc: 'Slideshow, then screen off when idle. SSH and updates stay up; any button wakes the box',
        },
        {
          id: 'sb_saver', type: 'value', value: saverIdx,
          options: SAVER_MINS.map(label), label: 'Screensaver after', desc: '',
        },
        {
          id: 'sb_off', type: 'value', value: sleepIdx,
          options: SLEEP_MINS.map(label), label: 'Screen off after', desc: '',
        },
        {
          id: 'sb_wake', type: 'value', value: wakeIdx,
          options: WAKE_SECS.map(secs), label: 'Wait for the TV after waking',
          desc: 'After Rest mode, black this long once the signal is back, so the boot animation plays whole',
        },
      ] : []),
      ...(logs ? [{
        id: 'logs', type: 'action',
        label: 'Logs',
        desc: logs.files ? `${logs.files} files, ${megabytes(logs.bytes)}` : 'Empty',
        label2: 'Purge logs', danger: true,
        busy: busy === 'logs' ? 'Purging…' : '',
        confirm: logs.files > 0,
      }] : []),
    ]

    const purgeLogs = () => {
      if (!logs || !logs.files) return
      setBusy('logs'); setMsg('')
      sdk.api.logs.purge()
        .then((r) => { setLogs({ files: 0, bytes: 0 }); setMsg(`Logs purged, ${megabytes(r.freed.bytes)} freed.`) })
        .catch(() => setMsg('Could not purge the logs.'))
        .finally(() => setBusy(''))
    }

    const onSet = (id, v) => {
      if (!standby) return
      const patch = id === 'sb_on' ? { enabled: v }
        : id === 'sb_saver' ? { screensaver_mins: SAVER_MINS[v] }
        : id === 'sb_off' ? { sleep_mins: SLEEP_MINS[v] }
        : id === 'sb_wake' ? { wake_settle_secs: WAKE_SECS[v] } : null
      if (!patch) return
      setStandby((s) => ({ ...s, ...patch }))
      sdk.api.standby.setConfig(patch)
        .then((r) => setStandby((s) => ({ ...s, ...r })))
        .catch(() => setMsg('Could not save that.'))
    }

    const onAct = (id) => {
      if (id === 'logs' && !busy) return purgeLogs()
      if (id !== 'update' || busy) return
      if (update && update.update_available) {
        setBusy('update'); setMsg('Installing. The interface restarts when it finishes.')
        sdk.api.update.apply().catch(() => { setBusy(''); setMsg('Could not start the update.') })
        return
      }
      setBusy('update'); setMsg('')
      sdk.api.update.check()
        .then((r) => {
          setUpdate(r)
          setMsg(r.update_available ? `v${r.latest} is available.` : 'This box is up to date.')
        })
        .catch(() => setMsg('Could not reach GitHub.'))
        .finally(() => setBusy(''))
    }

    const usedPct = info && info.storage_total_gb
      ? Math.round((info.storage_used_gb / info.storage_total_gb) * 100) : 0

    // Disks are rows, not a block beside the list. A block would look right and
    // be unreachable: everything focusable on this screen is a row, so an Eject
    // button anywhere else is a button no pad can press.
    const diskRows = [
      ...(info ? [{
        id: 'disk:internal', type: 'info',
        label: 'Internal disk',
        desc: `${info.storage_used_gb} GB used of ${info.storage_total_gb} GB`,
        display: 'System', bar: usedPct,
      }] : []),
      ...volumes.map((v) => ({
        id: `disk:${v.device}`,
        type: v.mounted ? 'action' : 'info',
        label: v.label || v.name,
        desc: `${v.device}, ${v.size}${v.mounted ? `, at ${v.stable_path}` : ', not mounted'}`,
        label2: 'Eject safely',
        display: v.mounted ? '' : 'Not mounted',
        busy: busy === v.device ? 'Ejecting…' : '',
      })),
    ]

    const allRows = [...rows, ...diskRows]
    const sections = {}
    if (diskRows.length) sections[diskRows[0].id] = 'Storage'

    const act = (id) => {
      if (!id.startsWith('disk:')) return onAct(id)
      const device = id.slice(5)
      const v = volumes.find((x) => x.device === device)
      if (!v || busy) return
      setBusy(device); setMsg('')
      sdk.api.storage.unmount(device)
        .then(() => { setMsg(`Safe to remove ${v.label || v.name}.`); loadVolumes() })
        // Windows says the disk is busy when a game is still reading it, and
        // that sentence is the only actionable part of the response.
        .catch((e) => setMsg(String((e && e.message) || 'Could not unmount that disk.')))
        .finally(() => setBusy(''))
    }

    return html`
      <${Rows} rows=${allRows} sections=${sections} active=${active} onLeave=${onLeave} onLeft=${onLeft}
        onSet=${onSet} onAct=${act}
        title="System"
        state=${info ? versionLabel(info.version) : ''}
        sub="The console image, standby behaviour, logs, and the disks it reads from."
        aside=${msg ? html`<div class="gcs-wifi-msg">${msg}</div>` : null} />`
  }
}
