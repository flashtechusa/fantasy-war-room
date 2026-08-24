/**
 * Auto Mode — autonomous post-draft team management.
 *
 * Three gates are required before any ESPN write can happen: the install-wide
 * switch, an owner-granted capability on this account, and the user's own
 * opt-in. Lineup + wire tiers can execute; trades are recommendation-only and
 * still require the user's approval.
 */

import { useEffect, useState } from 'react'
import { api, type AutoModeStatus } from '../api'
import { Banner, Card, Loading } from '../components'
import { useAsync } from '../useAsync'

function Toggle({
  label,
  on,
  disabled,
  onClick,
}: {
  label: string
  on: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      className={`btn sm ${on ? 'primary' : ''}`}
      onClick={onClick}
      disabled={disabled}
      style={{ minWidth: 64 }}
    >
      {on ? 'On' : 'Off'} · {label}
    </button>
  )
}

export default function Auto() {
  const status = useAsync<AutoModeStatus>(() => api.autoModeStatus(), [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [faab, setFaab] = useState(0)

  const s = status.data

  useEffect(() => {
    if (s) setFaab(s.faab_max)
  }, [s?.faab_max])

  async function save(patch: Record<string, boolean | number>) {
    setBusy(true)
    setErr(null)
    setNotice(null)
    try {
      await api.setAutoModeSettings(patch)
      status.reload()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function runNow() {
    setBusy(true)
    setErr(null)
    setNotice(null)
    try {
      const response = await fetch('/api/season/automode/run', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body?.detail || `${response.status} ${response.statusText}`)
      const actions = (body?.actions ?? []) as { tier: string; status: string; summary: string }[]
      setNotice(
        actions.length
          ? actions.map((a) => `${a.tier}: ${a.status} — ${a.summary}`).join(' · ')
          : body?.reason || 'Auto Mode cycle completed.',
      )
      status.reload()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (status.loading) return <Loading what="Auto Mode" />
  if (!s) return <Banner kind="error">Could not load Auto Mode.</Banner>

  const canOptIn = s.gates.capable
  const active = s.plan.active
  const live = s.gates.install_enabled && s.gates.capable && s.gates.user_enabled

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <Card title="Auto Mode">
        <div className="small muted" style={{ marginBottom: 10 }}>
          Auto Mode runs your team after the draft — setting the weekly lineup,
          working the waiver/free-agent wire, and finding trade opportunities. It is
          <strong> off by default</strong> and needs three things lined up: the admin
          master switch, permission on your account, and your own opt-in.
        </div>

        <div className="tiny faint" style={{ marginBottom: 10 }}>
          Install switch: <strong>{s.gates.install_enabled ? 'on' : 'off'}</strong> ·
          Your account: <strong>{s.gates.capable ? 'permitted' : 'not permitted'}</strong> ·
          Your opt-in: <strong>{s.gates.user_enabled ? 'on' : 'off'}</strong>
        </div>

        {!canOptIn && (
          <Banner kind="info">
            Auto Mode isn't enabled for your account yet. Ask your league admin to
            grant it on the Administration screen.
          </Banner>
        )}

        {canOptIn && (
          <>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <button
                className={`btn ${s.gates.user_enabled ? 'primary' : ''}`}
                disabled={busy}
                onClick={() => save({ auto_mode: !s.gates.user_enabled })}
              >
                {s.gates.user_enabled ? 'Auto Mode: ON' : 'Auto Mode: OFF'}
              </button>
              <Toggle label="Lineup" on={s.tiers.lineup} disabled={busy}
                onClick={() => save({ auto_lineup: !s.tiers.lineup })} />
              <Toggle label="Waivers" on={s.tiers.waivers} disabled={busy}
                onClick={() => save({ auto_waivers: !s.tiers.waivers })} />
              <Toggle label="Trades" on={s.tiers.trades} disabled={busy}
                onClick={() => save({ auto_trades: !s.tiers.trades })} />
            </div>

            {s.tiers.waivers && (
              <div className="row" style={{ gap: 8, alignItems: 'center', marginTop: 10 }}>
                <label className="tiny muted" htmlFor="auto-faab">Max FAAB per claim</label>
                <input
                  id="auto-faab"
                  type="number"
                  min={0}
                  max={1000}
                  value={faab}
                  disabled={busy}
                  onChange={(e) => setFaab(Math.max(0, Number(e.target.value) || 0))}
                  style={{ width: 80 }}
                />
                <button className="btn sm" disabled={busy || faab === s.faab_max}
                  onClick={() => save({ auto_faab_max: faab })}>
                  Save cap
                </button>
              </div>
            )}

            <div style={{ marginTop: 12 }}>
              <button className="btn" disabled={busy || !live} onClick={runNow}>
                {busy ? 'Running…' : 'Run Auto Mode now'}
              </button>
            </div>
          </>
        )}

        {err && <div style={{ marginTop: 8 }}><Banner kind="error">{err}</Banner></div>}
        {notice && <div style={{ marginTop: 8 }}><Banner kind="info">{notice}</Banner></div>}

        <div className="tiny faint" style={{ marginTop: 10 }}>
          When all three gates are on, lineup and wire tiers are live and the server
          checks them about every 30 minutes. Each cycle refreshes ESPN first and
          makes at most one wire move. Trade finding can run automatically, but a
          trade proposal is never sent without your approval.
        </div>
      </Card>

      {active && (
        <Card title="What Auto Mode is set to do">
          {s.plan.lineup && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 650 }}>Lineup</div>
              {s.plan.lineup.already_optimal ? (
                <div className="small muted">Season-level preview is already optimal.</div>
              ) : (
                <>
                  <div className="small muted">
                    Preview: optimal season-level lineup ({s.plan.lineup.gain! > 0 ? '+' : ''}
                    {s.plan.lineup.gain} projected pts).
                  </div>
                  {(s.plan.lineup.start ?? []).length > 0 && (
                    <div className="tiny" style={{ marginTop: 4 }}>
                      Start: {(s.plan.lineup.start ?? []).map((p) => `${p.name} (${p.position})`).join(', ')}
                    </div>
                  )}
                  {(s.plan.lineup.sit ?? []).length > 0 && (
                    <div className="tiny">
                      Bench: {(s.plan.lineup.sit ?? []).map((p) => `${p.name} (${p.position})`).join(', ')}
                    </div>
                  )}
                </>
              )}
              <div className="tiny faint" style={{ marginTop: 2 }}>{s.plan.lineup.note}</div>
            </div>
          )}

          {s.plan.waivers && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 650 }}>Waivers / free agents</div>
              <div className="small muted">{s.plan.waivers.note}</div>
              <div className="tiny faint" style={{ marginTop: 2 }}>
                FAAB cap per claim: ${s.plan.waivers.faab_max}.
              </div>
            </div>
          )}

          {s.plan.trades && (
            <div>
              <div style={{ fontWeight: 650 }}>Trades</div>
              <div className="small muted">
                {s.plan.trades.headline ? s.plan.trades.headline : 'No qualifying trade right now.'}
              </div>
              <div className="tiny faint" style={{ marginTop: 2 }}>{s.plan.trades.note}</div>
            </div>
          )}
        </Card>
      )}

      <Card title="Activity">
        {s.activity.length === 0 ? (
          <div className="small faint">Nothing yet. Auto Mode records plans and live outcomes here.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>When</th><th>Tier</th><th>Status</th><th>Summary</th></tr>
              </thead>
              <tbody>
                {s.activity.map((a, i) => (
                  <tr key={i}>
                    <td className="tiny faint">{new Date(a.at).toLocaleString()}</td>
                    <td className="tiny">{a.tier}</td>
                    <td className="tiny">{a.status.replace(/_/g, ' ')}</td>
                    <td className="small">{a.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
