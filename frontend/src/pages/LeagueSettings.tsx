/**
 * Phase 1 -- League Settings.
 *
 * The point of this screen is verification: every rule the ranking engine uses
 * is shown here, straight from ESPN, so you can confirm the board is being
 * built from YOUR league before you trust a single recommendation.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Banner, Card, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

/**
 * ESPN connection status.
 *
 * Status and a way in, nothing more. This card used to carry its own league
 * id / SWID / espn_s2 form, which duplicated the Connect ESPN screen field for
 * field -- two places to enter the same credentials, one of which silently
 * opened itself on a fresh install. Everything it did now lives in one flow,
 * including manual league-id entry, so there is a single path to keep working.
 */
function EspnConnectionCard() {
  const config = useAsync(() => api.myConfig(), [])
  const current = config.data
  const configured = Boolean(current?.espn_league_id)

  return (
    <Card title="ESPN connection">
      <div className="row between">
        <div className="small">
          {configured ? (
            <>
              League <strong>{current?.espn_league_id}</strong> · {current?.espn_season}
              <div className="tiny faint">
                {current?.espn_s2_set
                  ? 'Private-league cookies stored, encrypted'
                  : 'Public league (no cookies needed)'}
              </div>
            </>
          ) : (
            <>
              <span className="muted">No league connected yet.</span>
              <div className="tiny faint">
                Connect ESPN finds your leagues and your team for you.
              </div>
            </>
          )}
        </div>
        <Link className="btn primary sm" to="/connect">
          {configured ? 'Manage' : 'Connect ESPN'}
        </Link>
      </div>
    </Card>
  )
}

/**
 * Change your own password.
 *
 * New accounts arrive with a generated password that was, by necessity, sent
 * to them through some chat window. Without this there is no way to replace
 * it, so that password stays valid forever wherever it was pasted.
 */
function PasswordCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (next !== confirm) {
      setResult({ ok: false, text: 'The two new passwords do not match.' })
      return
    }
    setBusy(true)
    setResult(null)
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setResult({
        ok: true,
        text: 'Password changed. Any other browser you were signed in on has been signed out.',
      })
    } catch (error) {
      setResult({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Your password">
      <form onSubmit={submit} method="post">
        <input type="text" name="username" autoComplete="username" hidden readOnly value="" />
        <label className="tiny faint">CURRENT PASSWORD</label>
        <input
          type="password"
          name="current-password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
          style={{ marginBottom: 8 }}
        />
        <label className="tiny faint">NEW PASSWORD</label>
        <input
          type="password"
          name="new-password"
          autoComplete="new-password"
          minLength={10}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
          style={{ marginBottom: 8 }}
        />
        <label className="tiny faint">CONFIRM NEW PASSWORD</label>
        <input
          type="password"
          name="confirm-password"
          autoComplete="new-password"
          minLength={10}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
          style={{ marginBottom: 10 }}
        />
        <button className="btn primary block" type="submit" disabled={busy}>
          {busy ? 'Changing…' : 'Change password'}
        </button>
      </form>
      <div className="tiny faint" style={{ marginTop: 8 }}>
        At least 10 characters. Changing it signs you out everywhere else, which
        is the point if the old one was sent to you in a message.
      </div>
      {result && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={result.ok ? 'info' : 'error'}>{result.text}</Banner>
        </div>
      )}
    </Card>
  )
}

/**
 * Per-user projection source: ESPN, Sleeper, FantasyPros, or a consensus blend.
 *
 * The choice is per-user, so two managers can view the same league under
 * different projections. "ESPN" is the native board, byte-identical to before
 * this existed. Sleeper needs no key. FantasyPros needs the user's own key,
 * stored encrypted; a coverage line shows exactly how much of the roster it
 * really covers so a thin source is never mistaken for a full one. Consensus
 * blends whatever sources have data, per player.
 */
const MODE_LABELS: Record<string, string> = {
  espn: 'ESPN',
  sleeper: 'Sleeper',
  fantasypros: 'FantasyPros',
  consensus: 'Consensus',
}

const MODE_BLURB: Record<string, string> = {
  espn: 'The native ESPN projections — the default, identical to the current board.',
  sleeper: "Sleeper's projections, re-scored under your league's rules. No key needed.",
  fantasypros: "Your own FantasyPros key, re-scored under your rules. Add the key below.",
  consensus: 'An equal-weight blend of whatever sources you have imported, per player.',
}

function ProjectionSourceCard({ onChanged }: { onChanged: () => void }) {
  const status = useAsync(() => api.projectionStatus(), [])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [fpKey, setFpKey] = useState('')

  const s = status.data
  const mode = s?.mode ?? 'espn'

  async function choose(next: string) {
    if (next === mode || busy) return
    setBusy(true)
    setNote(null)
    try {
      const res = await api.setProjectionMode(next as never)
      setNote({ ok: true, text: `Now using ${MODE_LABELS[res.mode] ?? res.mode}.` })
      status.reload()
      onChanged()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(false)
    }
  }

  async function saveKey() {
    setBusy(true)
    setNote(null)
    try {
      const res = await api.saveFantasyProsKey(fpKey, true)
      setFpKey('')
      const imp = res.import
      setNote({
        ok: true,
        text: imp
          ? `Key saved. FantasyPros matched ${imp.matched} of ${imp.received} players (${Math.round(
              imp.coverage * 100,
            )}% of your pool).${imp.warning ? ` ${imp.warning}` : ''}`
          : 'Key saved.',
      })
      status.reload()
      onChanged()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(false)
    }
  }

  async function clearKey() {
    setBusy(true)
    setNote(null)
    try {
      await api.saveFantasyProsKey(null, false)
      setNote({ ok: true, text: 'FantasyPros key removed.' })
      status.reload()
      onChanged()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const fpKeySet = Boolean(s?.fantasypros.key_set)

  return (
    <Card title="Projection source">
      <div className="small muted" style={{ marginBottom: 10 }}>
        Which projections build your board. This is a per-user choice — it changes
        what <strong>you</strong> see, not the league. {MODE_BLURB[mode]}
      </div>

      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        {(s?.modes ?? ['espn', 'sleeper', 'fantasypros', 'consensus']).map((m) => (
          <button
            key={m}
            className={`btn ${m === mode ? 'primary' : ''}`}
            onClick={() => choose(m)}
            disabled={busy || status.loading}
          >
            {MODE_LABELS[m] ?? m}
          </button>
        ))}
      </div>

      {s && (
        <div className="tiny faint" style={{ marginTop: 8 }}>
          Sleeper: {s.sleeper.imported
            ? `${s.sleeper.players_matched}/${s.sleeper.pool_size} (${Math.round(
                s.sleeper.coverage * 100,
              )}%)`
            : 'not imported'}
          {' · '}
          FantasyPros: {s.fantasypros.key_set
            ? s.fantasypros.imported
              ? `${s.fantasypros.players_matched}/${s.fantasypros.pool_size} (${Math.round(
                  s.fantasypros.coverage * 100,
                )}%)`
              : 'key set, not imported'
            : 'no key'}
        </div>
      )}

      {s?.warnings?.map((w) => (
        <div key={w} style={{ marginTop: 8 }}>
          <Banner kind="info">{w}</Banner>
        </div>
      ))}

      <div style={{ marginTop: 14, borderTop: '1px solid var(--line, #2a2a2a)', paddingTop: 12 }}>
        <div className="small" style={{ marginBottom: 6 }}>
          <strong>Your FantasyPros API key</strong>{' '}
          <span className="faint">{fpKeySet ? '(set)' : '(not set)'}</span>
        </div>
        <div className="tiny muted" style={{ marginBottom: 8 }}>
          Bring your own key — it is stored encrypted and never shared. Saving it
          imports FantasyPros immediately and reports how much of your roster it
          covers. Free keys only return the top of each position; the coverage
          number tells you how much falls back to ESPN.
        </div>
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <input
            className="input"
            type="password"
            placeholder={fpKeySet ? '•••••••• (replace)' : 'Paste your FantasyPros API key'}
            value={fpKey}
            onChange={(e) => setFpKey(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="btn primary" onClick={saveKey} disabled={busy || !fpKey.trim()}>
            Save &amp; test
          </button>
          {fpKeySet && (
            <button className="btn" onClick={clearKey} disabled={busy}>
              Remove
            </button>
          )}
        </div>
      </div>

      {note && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={note.ok ? 'info' : 'error'}>{note.text}</Banner>
        </div>
      )}
    </Card>
  )
}

/**
 * In-app update.
 *
 * The app is usually running somewhere you only have a browser, so shipping a
 * fix shouldn't require a terminal. This pulls the branch and lets the
 * auto-reloading server pick it up.
 */
function UpdateCard() {
  const version = useAsync(() => api.version(), [])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  async function run() {
    setBusy(true)
    setNote(null)
    try {
      const result = await api.updateApp()
      setNote({ ok: true, text: result.detail })
      if (result.updated) {
        // The server is restarting; give it a moment, then reload.
        setTimeout(() => window.location.reload(), 6000)
      }
      version.reload()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(false)
    }
  }

  if (version.data && !version.data.git) return null

  const behind = version.data?.updates_available ?? 0

  return (
    <Card title="App version">
      <div className="row between">
        <div className="small" style={{ minWidth: 0 }}>
          {version.data?.commit ? (
            <>
              <div className="mono tiny faint">{version.data.commit}</div>
              <div className="tiny faint">
                {version.data.branch} · {version.data.committed}
              </div>
            </>
          ) : (
            <span className="muted">Checking…</span>
          )}
          {behind > 0 && (
            <div className="tiny" style={{ color: 'var(--accent)', marginTop: 3 }}>
              {behind} update{behind === 1 ? '' : 's'} available
            </div>
          )}
        </div>
        <button
          className={`btn sm ${behind > 0 ? 'primary' : ''}`}
          onClick={run}
          disabled={busy}
        >
          {busy ? 'Updating…' : behind > 0 ? `Update (${behind})` : 'Check for updates'}
        </button>
      </div>
      {note && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={note.ok ? 'info' : 'error'}>{note.text}</Banner>
        </div>
      )}
    </Card>
  )
}

/**
 * Which team is yours, and how much waiver money you have left.
 *
 * Auto-detection matches your SWID cookie against ESPN's owner ids, which
 * works for a solo-owned team. Co-owned teams and shared logins need to be
 * told, and every season screen is empty until they are -- so this is a
 * first-class control, not a hidden setting.
 */
function MyTeamPicker() {
  const config = useAsync(() => api.myConfig(), [])
  const league = useAsync(() => api.league().catch(() => null), [])
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [faab, setFaab] = useState('')

  const teams = league.data?.teams ?? []
  const detected = teams.find((t) => t.is_mine)
  const current = config.data?.my_team_id ?? detected?.espn_team_id ?? ''

  async function save(patch: Record<string, unknown>) {
    setSaving(true)
    setNote(null)
    try {
      await api.saveMyConfig(patch)
      config.reload()
      // Re-import so team ownership is re-evaluated across the league.
      await api.importLeague()
      league.reload()
      setNote('Saved. Your roster should now appear on the season screens.')
    } catch (error) {
      setNote((error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!league.data) return null

  return (
    <Card title="My team">
      {detected ? (
        <div className="small" style={{ marginBottom: 10 }}>
          Currently <strong>{detected.name}</strong>
          {detected.roster_size > 0 ? (
            <span className="muted"> · {detected.roster_size} players</span>
          ) : (
            <span style={{ color: 'var(--warn)' }}> · no roster imported</span>
          )}
        </div>
      ) : (
        <Banner kind="warn">
          No team is marked as yours, so My Team, Week, Waivers and Trade will all be
          empty. Pick your team below.
        </Banner>
      )}

      <label className="tiny faint">WHICH TEAM IS YOURS</label>
      <select
        value={current}
        disabled={saving}
        onChange={(event) =>
          event.target.value && save({ my_team_id: Number(event.target.value) })
        }
        style={{ marginBottom: 12 }}
      >
        <option value="">Pick your team…</option>
        {teams.map((team) => (
          <option key={team.espn_team_id} value={team.espn_team_id}>
            {team.name}
            {team.owners.length ? ` — ${team.owners.join(', ')}` : ''}
          </option>
        ))}
      </select>

      <label className="tiny faint">
        WAIVER BUDGET REMAINING{' '}
        {config.data?.faab_remaining != null && (
          <span className="muted">(currently ${config.data.faab_remaining})</span>
        )}
      </label>
      <div className="row" style={{ gap: 8 }}>
        <input
          type="number"
          inputMode="numeric"
          placeholder={String(league.data.waivers.budget || 100)}
          value={faab}
          onChange={(event) => setFaab(event.target.value)}
        />
        <button
          className="btn"
          disabled={saving || !faab.trim()}
          onClick={() => save({ faab_remaining: Number(faab.trim()) })}
        >
          Save
        </button>
      </div>
      <div className="tiny faint" style={{ marginTop: 6 }}>
        ESPN doesn't report remaining budget reliably, so enter it here and the waiver
        bids will be scaled to what you actually have left.
      </div>

      {note && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={note.startsWith('Saved') ? 'info' : 'error'}>{note}</Banner>
        </div>
      )}
    </Card>
  )
}

export default function LeagueSettings({
  onChange,
}: {
  onChange?: () => void
  /** Accepted for compatibility with the parent; the projection source and
   * connection cards are per-user, so this screen no longer branches on role. */
  role?: string
}) {
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

      <PasswordCard />

      <ProjectionSourceCard onChanged={() => onChange?.()} />

      <EspnConnectionCard />

      <UpdateCard />

      <MyTeamPicker />

      <Card title="Draft tools">
        <div className="small muted" style={{ marginBottom: 9 }}>
          For draft day. The weekly tools live in the other tabs.
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          <a className="btn sm" href="/board">Draft board</a>
          <a className="btn sm" href="/live">Live draft</a>
          <a className="btn sm" href="/simulate">Simulator</a>
        </div>
      </Card>

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
