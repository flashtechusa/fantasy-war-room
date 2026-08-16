/**
 * Phase 1 -- League Settings.
 *
 * The point of this screen is verification: every rule the ranking engine uses
 * is shown here, straight from ESPN, so you can confirm the board is being
 * built from YOUR league before you trust a single recommendation.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import { Banner, Card, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

/**
 * ESPN credentials, editable in the app.
 *
 * Exists so the app can be pointed at a league from a device where you can't
 * edit a `.env` -- a Codespace, a tablet, a phone at the draft. Values are
 * stored locally and the API never returns them; it only reports whether they
 * are set.
 */
function EspnConnectionForm({ onSaved }: { onSaved: () => void }) {
  const config = useAsync(() => api.config(), [])
  const [open, setOpen] = useState(false)
  const [leagueId, setLeagueId] = useState('')
  const [season, setSeason] = useState('')
  const [swid, setSwid] = useState('')
  const [s2, setS2] = useState('')
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  const current = config.data
  const configured = Boolean(current?.espn_league_id) && !current?.demo_mode

  // Open automatically when there's nothing configured — that's the first thing
  // a new install needs to do.
  useEffect(() => {
    if (current && !current.espn_league_id) setOpen(true)
  }, [current])

  async function save() {
    setSaving(true)
    setResult(null)
    try {
      const body: Record<string, unknown> = { demo_mode: false }
      if (leagueId.trim()) body.espn_league_id = Number(leagueId.trim())
      if (season.trim()) body.espn_season = Number(season.trim())
      if (swid.trim()) body.espn_swid = swid.trim()
      if (s2.trim()) body.espn_s2 = s2.trim()

      const response = await api.saveConfig(body)
      setSwid('')
      setS2('')
      config.reload()

      if (response.connection?.connected) {
        setResult({ ok: true, text: `Connected to "${response.connection.league_name}".` })
        setOpen(false)
      } else if (response.connection) {
        setResult({ ok: false, text: response.connection.detail ?? 'Could not reach ESPN.' })
      } else {
        setResult({ ok: true, text: 'Saved.' })
      }
      onSaved()
    } catch (error) {
      setResult({ ok: false, text: (error as Error).message })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="ESPN connection">
      <div className="row between" style={{ marginBottom: open ? 12 : 0 }}>
        <div className="small">
          {configured ? (
            <>
              League <strong>{current?.espn_league_id}</strong> · {current?.espn_season}
              <div className="tiny faint">
                {current?.has_private_credentials
                  ? 'Private-league cookies stored'
                  : 'Public league (no cookies)'}
              </div>
            </>
          ) : (
            <span className="muted">No league configured yet.</span>
          )}
        </div>
        <button className="btn sm" onClick={() => setOpen((v) => !v)}>
          {open ? 'Cancel' : configured ? 'Change' : 'Set up'}
        </button>
      </div>

      {open && (
        <>
          <label className="tiny faint">LEAGUE ID</label>
          <input
            type="number"
            inputMode="numeric"
            placeholder={String(current?.espn_league_id ?? '123456')}
            value={leagueId}
            onChange={(e) => setLeagueId(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">SEASON</label>
          <input
            type="number"
            inputMode="numeric"
            placeholder={String(current?.espn_season ?? 2026)}
            value={season}
            onChange={(e) => setSeason(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">
            SWID {current?.swid_set && <span className="muted">(stored — leave blank to keep)</span>}
          </label>
          <input
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="{XXXXXXXX-XXXX-...}"
            value={swid}
            onChange={(e) => setSwid(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">
            ESPN_S2{' '}
            {current?.espn_s2_set && <span className="muted">(stored — leave blank to keep)</span>}
          </label>
          <input
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="AEC..."
            value={s2}
            onChange={(e) => setS2(e.target.value)}
            style={{ marginBottom: 10 }}
          />
          <button className="btn primary block" onClick={save} disabled={saving}>
            {saving ? 'Testing connection…' : 'Save & test connection'}
          </button>
          <div className="tiny faint" style={{ marginTop: 8 }}>
            Cookies are stored in your local database and are never sent back to the browser.
            Public leagues need only the league id.
          </div>
        </>
      )}

      {result && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={result.ok ? 'info' : 'error'}>{result.text}</Banner>
        </div>
      )}
    </Card>
  )
}

export default function LeagueSettings({ onChange }: { onChange?: () => void }) {
  const health = useAsync(() => api.health(), [])
  const league = useAsync(() => api.league().catch(() => null), [])
  const history = useAsync(() => api.history().catch(() => null), [])
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)

  async function runImport() {
    setBusy('import')
    setMessage(null)
    try {
      const result = await api.importLeague()
      setMessage({
        kind: 'info',
        text: `Imported ${result.league.name} with ${result.players_imported} players.`,
      })
      league.reload()
      health.reload()
      history.reload()
      onChange?.()
    } catch (error) {
      setMessage({ kind: 'error', text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function refreshPlayers() {
    setBusy('players')
    setMessage(null)
    try {
      const result = await api.refreshPlayers()
      setMessage({ kind: 'info', text: `Refreshed ${result.players_imported} players.` })
      league.reload()
    } catch (error) {
      setMessage({ kind: 'error', text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const info = league.data
  const seasons = history.data?.seasons ?? []
  const [historySeason, setHistorySeason] = useState<string>('')
  const activeSeason = historySeason || (seasons.length ? String(seasons[0]) : '')
  const historyPicks = history.data?.picks_by_season?.[activeSeason] ?? []

  return (
    <>
      {message && <Banner kind={message.kind === 'error' ? 'error' : 'info'}>{message.text}</Banner>}

      <EspnConnectionForm
        onSaved={() => {
          health.reload()
          league.reload()
          onChange?.()
        }}
      />

      <Card title="Connection">
        {health.loading ? (
          <Loading what="status" />
        ) : health.data ? (
          <>
            <dl className="kv">
              <dt>Season</dt>
              <dd>{health.data.season}</dd>
              <dt>Mode</dt>
              <dd>{health.data.demo_mode ? 'Demo (synthetic data)' : 'ESPN'}</dd>
              <dt>League ID configured</dt>
              <dd>{health.data.espn.league_id_configured ? 'Yes' : 'No'}</dd>
              <dt>Private-league cookies</dt>
              <dd>{health.data.espn.private_credentials_configured ? 'Present' : 'Not set'}</dd>
              <dt>Players loaded</dt>
              <dd>{health.data.players_loaded}</dd>
            </dl>

            {health.data.demo_mode && (
              <div style={{ marginTop: 10 }}>
                <Banner kind="warn">
                  Demo mode is on. Players and projections are <strong>synthetic</strong> — useful
                  for trying the app, not for drafting. Set <code>FWR_DEMO_MODE=false</code> and
                  supply your league ID to use real ESPN data.
                </Banner>
              </div>
            )}
            {!health.data.demo_mode && !health.data.espn.league_id_configured && (
              <div style={{ marginTop: 10 }}>
                <Banner kind="warn">
                  No <code>FWR_ESPN_LEAGUE_ID</code> set, so the demo provider is being used.
                </Banner>
              </div>
            )}

            <div className="row wrap" style={{ gap: 8, marginTop: 12 }}>
              <button className="btn primary" onClick={runImport} disabled={busy !== null}>
                {busy === 'import' ? 'Importing…' : 'Import league'}
              </button>
              <button className="btn" onClick={refreshPlayers} disabled={busy !== null || !info}>
                {busy === 'players' ? 'Refreshing…' : 'Refresh players only'}
              </button>
            </div>
          </>
        ) : (
          <Banner kind="error">{health.error}</Banner>
        )}
      </Card>

      {!info && !league.loading && (
        <Banner kind="info">
          Nothing imported yet. Tap <strong>Import league</strong> above to pull your settings,
          teams, rosters and player pool from ESPN.
        </Banner>
      )}

      {info && (
        <>
          <Card title="League">
            <dl className="kv">
              <dt>Name</dt>
              <dd>{info.name}</dd>
              <dt>ESPN league ID</dt>
              <dd className="mono">{info.espn_league_id}</dd>
              <dt>Season</dt>
              <dd>{info.season}</dd>
              <dt>Teams</dt>
              <dd>{info.team_count}</dd>
              <dt>Scoring format</dt>
              <dd>{info.scoring.format_label}</dd>
              <dt>Scoring type</dt>
              <dd>{info.scoring.type || '—'}</dd>
              <dt>Data source</dt>
              <dd>{info.is_demo ? 'Demo (synthetic)' : 'ESPN'}</dd>
              <dt>Imported</dt>
              <dd>{new Date(info.imported_at).toLocaleString()}</dd>
            </dl>
          </Card>

          <Card title="Roster & starting lineup">
            <div className="row wrap" style={{ gap: 6, marginBottom: 10 }}>
              {Object.entries(info.roster.starting_slots).map(([slot, count]) => (
                <span key={slot} className="pill">
                  {count}× {slot}
                </span>
              ))}
              <span className="pill">{info.roster.bench_slots}× BE</span>
              {info.roster.ir_slots > 0 && <span className="pill">{info.roster.ir_slots}× IR</span>}
            </div>
            <dl className="kv">
              <dt>Starters</dt>
              <dd>{info.roster.starters_total}</dd>
              <dt>Bench</dt>
              <dd>{info.roster.bench_slots}</dd>
              <dt>IR</dt>
              <dd>{info.roster.ir_slots}</dd>
              <dt>Roster size (drafted)</dt>
              <dd>{info.roster.roster_size}</dd>
              <dt>Draft rounds</dt>
              <dd>{info.roster.draft_rounds}</dd>
              <dt>Superflex</dt>
              <dd>{info.roster.is_superflex ? 'Yes' : 'No'}</dd>
            </dl>
            {info.roster.flex_slots.length > 0 && (
              <div className="small muted" style={{ marginTop: 8 }}>
                Flex slots:{' '}
                {info.roster.flex_slots
                  .map((slot) => `${slot.count}× ${slot.label} (${slot.eligible.join('/')})`)
                  .join(', ')}
              </div>
            )}
          </Card>

          <Card title="Draft">
            <dl className="kv">
              <dt>Type</dt>
              <dd>{info.draft.type}</dd>
              <dt>Completed</dt>
              <dd>{info.draft.completed ? 'Yes' : 'Not yet'}</dd>
              <dt>Keepers</dt>
              <dd>{info.draft.keeper_count}</dd>
              <dt>Seconds per pick</dt>
              <dd>{info.draft.seconds_per_pick ?? '—'}</dd>
              <dt>Scheduled</dt>
              <dd>{info.draft.date ? new Date(info.draft.date).toLocaleString() : '—'}</dd>
            </dl>
            {info.draft.order.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="tiny faint" style={{ marginBottom: 5 }}>
                  DRAFT ORDER
                </div>
                <ol className="small" style={{ margin: 0, paddingLeft: 20 }}>
                  {info.draft.order.map((teamId, index) => {
                    const team = info.teams.find((t) => t.espn_team_id === teamId)
                    return (
                      <li key={`${teamId}-${index}`}>
                        {team?.name ?? `Team ${teamId}`}
                        {team?.is_mine && <strong className="muted"> — you</strong>}
                      </li>
                    )
                  })}
                </ol>
              </div>
            )}
          </Card>

          <Card title="Scoring rules used by the engine">
            <div className="small muted" style={{ marginBottom: 8 }}>
              {info.scoring.rules.length} active rules of {info.scoring.rule_count} returned by
              ESPN. Every projection on the board is computed from these.
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Abbr</th>
                    <th className="num">Points</th>
                  </tr>
                </thead>
                <tbody>
                  {info.scoring.rules.map((rule) => (
                    <tr key={rule.stat_id}>
                      <td>{rule.label}</td>
                      <td className="faint mono">{rule.abbrev}</td>
                      <td className="num">{rule.points}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Waivers">
            <dl className="kv">
              <dt>Type</dt>
              <dd>{info.waivers.type || '—'}</dd>
              <dt>FAAB</dt>
              <dd>{info.waivers.uses_faab ? `Yes ($${info.waivers.budget})` : 'No'}</dd>
              <dt>Process days</dt>
              <dd>{info.waivers.process_days.join(', ') || '—'}</dd>
              <dt>Acquisition limit</dt>
              <dd>
                {info.waivers.acquisition_limit === -1 || info.waivers.acquisition_limit === null
                  ? 'None'
                  : info.waivers.acquisition_limit}
              </dd>
            </dl>
          </Card>

          <Card title="Playoffs & trades">
            <dl className="kv">
              <dt>Regular season weeks</dt>
              <dd>{info.playoffs.regular_season_weeks}</dd>
              <dt>Playoff teams</dt>
              <dd>{info.playoffs.team_count}</dd>
              <dt>Playoff matchup length</dt>
              <dd>{info.playoffs.matchup_length} week(s)</dd>
              <dt>Seeding tiebreak</dt>
              <dd>{info.playoffs.seed_tie_rule || '—'}</dd>
              <dt>Veto votes required</dt>
              <dd>{info.trades.veto_votes_required}</dd>
            </dl>
          </Card>

          <Card title={`Teams & owners (${info.teams.length})`}>
            {info.teams.map((team) => (
              <details key={team.espn_team_id} style={{ marginBottom: 6 }}>
                <summary
                  style={{ cursor: 'pointer', padding: '8px 0', minHeight: 40, listStyle: 'none' }}
                >
                  <div className="row between">
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 650 }}>
                        {team.name} {team.is_mine && <span className="pill">YOU</span>}
                      </div>
                      <div className="tiny faint">
                        {team.owners.join(', ') || 'Unknown owner'}
                        {team.draft_slot ? ` · slot ${team.draft_slot}` : ''}
                        {` · ${team.roster_size} rostered`}
                      </div>
                    </div>
                  </div>
                </summary>
                {team.roster.length > 0 ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Slot</th>
                          <th>Player</th>
                          <th>Pos</th>
                          <th>Team</th>
                        </tr>
                      </thead>
                      <tbody>
                        {team.roster.map((player, index) => (
                          <tr key={`${player.name}-${index}`}>
                            <td className="faint">{player.slot}</td>
                            <td>{player.name}</td>
                            <td>
                              <Pos position={player.position} />
                            </td>
                            <td className="faint">{player.pro_team}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="small faint" style={{ paddingBottom: 8 }}>
                    No rostered players (pre-draft).
                  </div>
                )}
              </details>
            ))}
          </Card>

          <Card title="Previous draft results">
            {seasons.length === 0 ? (
              <div className="small faint">
                No draft history available from ESPN for this league.
              </div>
            ) : (
              <>
                <select
                  value={activeSeason}
                  onChange={(event) => setHistorySeason(event.target.value)}
                  style={{ marginBottom: 10 }}
                  aria-label="Draft history season"
                >
                  {seasons.map((season) => (
                    <option key={season} value={String(season)}>
                      {season} draft
                    </option>
                  ))}
                </select>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th className="num">#</th>
                        <th>Player</th>
                        <th>Team</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historyPicks.slice(0, 120).map((pick) => (
                        <tr key={pick.overall_pick}>
                          <td className="num faint">
                            {pick.round_num}.{String(pick.round_pick).padStart(2, '0')}
                          </td>
                          <td>{pick.player_name || '—'}</td>
                          <td className="faint">{pick.team_name}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Card>
        </>
      )}
    </>
  )
}
