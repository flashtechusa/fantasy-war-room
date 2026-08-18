/**
 * ESPN Draft Sync Diagnostics -- a temporary debug screen.
 *
 * Enabled with `FWR_DEBUG_SCREENS=true`. It exists to answer one question
 * during a real draft: is ESPN keeping up? Everything here is counts, timings
 * and redacted error text -- the endpoint deliberately returns no cookies, no
 * headers and no ESPN payloads, so there is nothing on this screen that would
 * be unsafe on a shared monitor at a draft party.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import { Banner, Card, Loading } from '../components'
import { useAsync } from '../useAsync'

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string | number
  hint?: string
  tone?: 'ok' | 'warn' | 'bad'
}) {
  return (
    <div className="diag-stat">
      <div className="tiny faint">{label}</div>
      <div className={`diag-value${tone ? ` ${tone}` : ''}`}>{value}</div>
      {hint && <div className="tiny faint">{hint}</div>}
    </div>
  )
}

export default function DraftDiagnostics() {
  const diag = useAsync(() => api.draftDiagnostics(), [])
  const [auto, setAuto] = useState(true)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!auto) return
    const timer = setInterval(() => diag.reload(), 5000)
    return () => clearInterval(timer)
  }, [auto, diag])

  async function syncNow() {
    setBusy(true)
    setNote('')
    try {
      const result = await api.sync()
      setNote(
        result.synced
          ? `Sync ran — ${result.added ?? 0} new pick(s).`
          : (result.reason ?? 'Rate limited.'),
      )
    } catch (error) {
      setNote((error as Error).message)
    } finally {
      setBusy(false)
      diag.reload()
    }
  }

  async function clear() {
    await api.clearDraftDiagnostics()
    setNote('Cleared recorded attempts.')
    diag.reload()
  }

  if (diag.loading && !diag.data) return <Loading what="diagnostics" />
  if (diag.error) return <Banner kind="error">{diag.error}</Banner>
  if (!diag.data) return null

  const data = diag.data
  const behind = data.picks.behind_by
  const stale = data.response.seconds_since_last_success

  return (
    <>
      <Banner kind="warn">
        Debug screen. Reports counts and timings only — no cookies, headers or
        ESPN payloads are exposed here.
      </Banner>

      {note && <Banner kind="info">{note}</Banner>}

      <Card title="ESPN Draft Sync Diagnostics">
        <div className="diag-grid">
          <Stat
            label="ESPN latest pick"
            value={data.picks.espn_latest_pick || '—'}
            hint={`${data.picks.espn_pick_count} picks on ESPN's board`}
          />
          <Stat
            label="Local latest pick"
            value={data.picks.local_latest_pick || '—'}
            hint={`${data.picks.local_pick_count} recorded here`}
          />
          <Stat
            label="Behind by"
            value={behind}
            tone={behind === 0 ? 'ok' : behind > 2 ? 'bad' : 'warn'}
            hint={data.picks.new_picks_detected ? 'new picks last sync' : 'no new picks last sync'}
          />
          <Stat
            label="Last response"
            value={`${Math.round(data.response.last_latency_ms)} ms`}
            hint={`avg ${Math.round(data.response.average_latency_ms)} · max ${Math.round(
              data.response.max_latency_ms,
            )}`}
          />
          <Stat
            label="Last success"
            value={stale === null ? 'never' : `${stale}s ago`}
            tone={stale === null ? 'bad' : stale > 60 ? 'warn' : 'ok'}
            hint={
              data.response.success_rate === null
                ? 'no attempts yet'
                : `${Math.round(data.response.success_rate * 100)}% of attempts succeeded`
            }
          />
          <Stat
            label="Poll interval"
            value={`${data.polling.interval_seconds}s`}
            hint={
              data.polling.enabled
                ? `next allowed in ${data.polling.seconds_until_next_allowed ?? 0}s`
                : 'sync not enabled yet'
            }
          />
        </div>
      </Card>

      <Card title="Endpoint">
        <div className="small">
          Answering source: <strong>{data.endpoint.source || 'none yet'}</strong>
        </div>
        <div className="tiny faint mono" style={{ wordBreak: 'break-all' }}>
          {data.endpoint.url || 'no request recorded yet'}
        </div>
        <div className="small" style={{ marginTop: 10 }}>
          ESPN board: {data.picks.direct_pick_count} picks via mDraftDetail ·{' '}
          {data.picks.library_pick_count} via espn-api
          {data.picks.espn_draft_in_progress && ' · draft IN PROGRESS'}
          {data.picks.espn_draft_complete && ' · draft complete'}
        </div>
        <ul className="tiny faint">
          {data.endpoint.candidates.map((candidate) => (
            <li key={candidate.source}>
              <strong>{candidate.source}</strong> — {candidate.role}
            </li>
          ))}
        </ul>
      </Card>

      {data.last_error && (
        <Card title="Last error">
          <div className="tiny faint">{data.last_error.at}</div>
          <div className="small">{data.last_error.detail}</div>
        </Card>
      )}

      <Card title="Recent attempts">
        <div className="table-scroll">
          <table className="diag-table">
            <thead>
              <tr>
                <th>At</th>
                <th>OK</th>
                <th>Source</th>
                <th>ms</th>
                <th>ESPN</th>
                <th>Local</th>
                <th>New</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {data.recent.length === 0 && (
                <tr>
                  <td colSpan={8} className="faint">
                    No sync attempts recorded yet.
                  </td>
                </tr>
              )}
              {[...data.recent].reverse().map((row, index) => (
                <tr key={`${row.at}-${index}`}>
                  <td className="mono tiny">{String(row.at).slice(11, 19)}</td>
                  <td>{row.ok ? '✓' : '✗'}</td>
                  <td className="tiny">{String(row.source || '—')}</td>
                  <td className="tiny">{Math.round(Number(row.latency_ms) || 0)}</td>
                  <td className="tiny">{String(row.espn_latest_pick ?? 0)}</td>
                  <td className="tiny">{String(row.local_latest_pick ?? 0)}</td>
                  <td className="tiny">{String(row.new_picks ?? 0)}</td>
                  <td className="tiny faint">{String(row.error || '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="row wrap" style={{ marginTop: 12 }}>
          <button className="btn primary sm" onClick={syncNow} disabled={busy}>
            {busy ? 'Syncing…' : 'Sync now'}
          </button>
          <button className="btn sm" onClick={() => setAuto((value) => !value)}>
            {auto ? 'Stop auto-refresh' : 'Auto-refresh every 5s'}
          </button>
          <button className="btn sm" onClick={clear}>
            Clear history
          </button>
        </div>
      </Card>
    </>
  )
}
