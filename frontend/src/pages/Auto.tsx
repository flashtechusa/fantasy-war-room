/**
 * Auto Mode — autonomous team management, staged and dry-run first.
 *
 * Shows the three gates (install switch, account permission, your opt-in), lets
 * a permitted user turn Auto Mode and its tiers on, and displays the plan Auto
 * Mode *would* run — the optimal lineup moves, a waiver note, a trade
 * suggestion — plus the activity log. Nothing here writes to ESPN yet: every
 * action is held until its ESPN payload is captured, and the page says so.
 */

import { useState } from 'react'
import { api, type AutoModeStatus, type LineupApplyResult } from '../api'
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
  const [confirmingLineup, setConfirmingLineup] = useState(false)
  const [applyingLineup, setApplyingLineup] = useState(false)
  const [lineupResult, setLineupResult] = useState<LineupApplyResult | null>(null)
  const [lineupErr, setLineupErr] = useState<string | null>(null)

  const s = status.data

  async function applyLineup() {
    setApplyingLineup(true)
    setLineupErr(null)
    setLineupResult(null)
    try {
      const result = await api.applyLineup(true)
      setLineupResult(result)
      setConfirmingLineup(false)
      status.reload()
    } catch (e) {
      setLineupErr((e as Error).message)
    } finally {
      setApplyingLineup(false)
    }
  }

  async function save(patch: Record<string, boolean | number>) {
    setBusy(true)
    setErr(null)
    try {
      await api.setAutoModeSettings(patch)
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

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <Card title="Auto Mode">
        <div className="small muted" style={{ marginBottom: 10 }}>
          Auto Mode runs your team for you after the draft — setting your lineup,
          working the waiver wire, and surfacing trades. It's <strong>off by
          default</strong> and needs three things lined up: the league admin's
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
        )}

        {err && <div style={{ marginTop: 8 }}><Banner kind="error">{err}</Banner></div>}

        <div className="tiny faint" style={{ marginTop: 10 }}>
          <strong>Lineup writing is live</strong>: you can apply your optimal
          lineup straight to ESPN below (a real, reversible write behind an
          explicit confirm). Waivers and trades still plan and log only — those
          turn on per tier once each ESPN write is verified.
        </div>
      </Card>

      {active && (
        <Card title="What Auto Mode would do now">
          {/* Lineup */}
          {s.plan.lineup && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 650 }}>Lineup</div>
              {s.plan.lineup.already_optimal ? (
                <div className="small muted">Your lineup is already optimal — no change.</div>
              ) : (
                <>
                  <div className="small muted">
                    Set your optimal lineup ({s.plan.lineup.gain! > 0 ? '+' : ''}
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

              {/* The real ESPN write. Two-step so nothing goes out on one tap. */}
              {!s.plan.lineup.already_optimal && (
                <div style={{ marginTop: 8 }}>
                  {!confirmingLineup ? (
                    <button
                      className="btn sm primary"
                      disabled={applyingLineup}
                      onClick={() => { setConfirmingLineup(true); setLineupResult(null); setLineupErr(null) }}
                    >
                      Apply optimal lineup to ESPN
                    </button>
                  ) : (
                    <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span className="tiny">This writes your lineup to ESPN now.</span>
                      <button className="btn sm primary" disabled={applyingLineup} onClick={applyLineup}>
                        {applyingLineup ? 'Applying…' : 'Confirm write'}
                      </button>
                      <button className="btn sm" disabled={applyingLineup} onClick={() => setConfirmingLineup(false)}>
                        Cancel
                      </button>
                    </div>
                  )}
                </div>
              )}

              {lineupErr && (
                <div style={{ marginTop: 8 }}><Banner kind="error">{lineupErr}</Banner></div>
              )}
              {lineupResult && (
                <div style={{ marginTop: 8 }}>
                  <Banner kind={lineupResult.ok ? 'info' : 'error'}>
                    {lineupResult.ok
                      ? `Lineup applied to ESPN (HTTP ${lineupResult.status_code}). ${lineupResult.moves.length} move(s).`
                      : `ESPN did not accept it (HTTP ${lineupResult.status_code}).`}
                    {lineupResult.moves.length > 0 && (
                      <div className="tiny" style={{ marginTop: 4 }}>
                        {lineupResult.moves.map((m) => `${m.name}: ${m.from_slot}→${m.to_slot}`).join(', ')}
                      </div>
                    )}
                    <div className="tiny mono" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>
                      {lineupResult.response}
                    </div>
                  </Banner>
                </div>
              )}
            </div>
          )}

          {/* Waivers */}
          {s.plan.waivers && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 650 }}>Waivers</div>
              <div className="small muted">{s.plan.waivers.note}</div>
              <div className="tiny faint" style={{ marginTop: 2 }}>
                FAAB cap per claim: ${s.plan.waivers.faab_max}. Held — pending ESPN capture.
              </div>
            </div>
          )}

          {/* Trades */}
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
          <div className="small faint">Nothing yet. Auto Mode logs each cycle here.</div>
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
