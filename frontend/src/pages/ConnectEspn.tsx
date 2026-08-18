/**
 * Connect ESPN -- the guided connection flow.
 *
 * The old path asked for five things by hand: league id, season, SWID,
 * espn_s2, and which team was yours. Four of them are derivable from the
 * cookies, so this screen asks for the cookies once and discovers the rest.
 *
 * Credentials are write-only here. They go into the inputs, straight to the
 * API, and the fields are cleared. Nothing on this screen can display one,
 * because no endpoint returns one.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  api,
  type DiscoveredLeague,
  type LeagueRules,
  type PairingCode,
} from '../api'
import { Banner, Card, Loading } from '../components'
import { useAsync } from '../useAsync'

type Step = 'credentials' | 'pick-league' | 'confirm' | 'done'

function currentSeason(): number {
  // ESPN's season flips over in the spring; before that, last year's league is
  // still the live one.
  const now = new Date()
  return now.getMonth() >= 4 ? now.getFullYear() : now.getFullYear() - 1
}

function LeagueRow({
  league,
  selected,
  onSelect,
}: {
  league: DiscoveredLeague
  selected: boolean
  onSelect: () => void
}) {
  const bits = [
    `${league.team_count} teams`,
    String(league.season),
    league.is_ppr ? `${league.ppr_value} PPR` : 'Standard',
  ]
  if (league.draft_in_progress) bits.push('DRAFT LIVE')
  else if (league.draft_completed) bits.push('drafted')

  return (
    <button
      type="button"
      className={`league-option${selected ? ' selected' : ''}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <div className="league-option-name">{league.name || `League ${league.league_id}`}</div>
      <div className="tiny faint">{bits.join(' · ')}</div>
      {league.my_team_name ? (
        <div className="tiny">
          Your team: <strong>{league.my_team_name}</strong>
        </div>
      ) : (
        <div className="tiny faint">Team not detected — you can pick it next</div>
      )}
    </button>
  )
}

function RulesReview({ rules, league }: { rules: LeagueRules; league: DiscoveredLeague }) {
  const starters = Object.entries(rules.roster_slots || {})
  return (
    <div className="rules-review">
      <div className="rules-block">
        <h4>Scoring</h4>
        <div className="small">
          {rules.is_ppr ? `${rules.ppr_value} point PPR` : 'Standard (no PPR)'} ·{' '}
          {rules.scoring_rule_count} rules imported
        </div>
      </div>
      <div className="rules-block">
        <h4>Roster</h4>
        <div className="small">
          {starters.map(([slot, count]) => `${count}×${slot}`).join(' · ') || 'Not reported'}
        </div>
        <div className="tiny faint">
          Bench {rules.bench_slots} · IR {rules.ir_slots}
        </div>
      </div>
      <div className="rules-block">
        <h4>Draft</h4>
        <div className="small">
          {rules.draft_type || 'Unknown'}
          {rules.keeper_count ? ` · ${rules.keeper_count} keepers` : ''}
          {rules.draft_order?.length ? ` · order set (${rules.draft_order.length})` : ''}
        </div>
        <div className="tiny faint">
          {league.draft_completed
            ? `Complete — ${league.draft_pick_count} picks`
            : league.draft_in_progress
              ? `In progress — ${league.draft_pick_count} picks so far`
              : 'Not started'}
        </div>
      </div>
      <div className="rules-block">
        <h4>Waivers</h4>
        <div className="small">
          {rules.uses_faab ? `FAAB · $${rules.acquisition_budget}` : rules.waiver_type || 'Unknown'}
        </div>
        <div className="tiny faint">
          {(rules.waiver_process_days || []).join(', ') || 'No process days reported'}
        </div>
      </div>
      <div className="rules-block">
        <h4>Playoffs</h4>
        <div className="small">
          {rules.playoff_team_count} teams · {rules.regular_season_weeks} week regular season
        </div>
        <div className="tiny faint">
          {rules.playoff_matchup_length}-week matchups
          {rules.playoff_seed_tie_rule ? ` · ${rules.playoff_seed_tie_rule}` : ''}
        </div>
      </div>
    </div>
  )
}

export default function ConnectEspn({ onChange }: { onChange?: () => void }) {
  const navigate = useNavigate()
  const status = useAsync(() => api.espnStatus(), [])

  const [step, setStep] = useState<Step>('credentials')
  const [swid, setSwid] = useState('')
  const [s2, setS2] = useState('')
  const [season, setSeason] = useState(String(currentSeason()))
  const [manualLeagueId, setManualLeagueId] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [leagues, setLeagues] = useState<DiscoveredLeague[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [chosen, setChosen] = useState<DiscoveredLeague | null>(null)
  const [rules, setRules] = useState<LeagueRules | null>(null)
  const [teamId, setTeamId] = useState<number | null>(null)
  const [autoDetected, setAutoDetected] = useState(false)
  const [pairing, setPairing] = useState<PairingCode | null>(null)
  const [showExtension, setShowExtension] = useState(false)

  // Credentials already stored (from a previous visit, or the extension) means
  // there is nothing to type -- go straight to finding leagues.
  useEffect(() => {
    if (status.data?.credentials_stored && step === 'credentials') {
      setStep('pick-league')
      setNotice('ESPN credentials are already stored for this account.')
    }
    if (status.data?.espn_season) setSeason(String(status.data.espn_season))
  }, [status.data])

  async function run<T>(work: () => Promise<T>): Promise<T | null> {
    setBusy(true)
    setError('')
    try {
      return await work()
    } catch (err) {
      setError((err as Error).message)
      return null
    } finally {
      setBusy(false)
    }
  }

  async function saveCredentials() {
    const result = await run(() =>
      api.submitEspnCredentials({
        swid: swid.trim(),
        espn_s2: s2.trim(),
        season: Number(season) || undefined,
      }),
    )
    if (!result) return
    // Out of React state the moment the request succeeds.
    setSwid('')
    setS2('')
    status.reload()
    setStep('pick-league')
    await discover()
  }

  async function discover() {
    const result = await run(() =>
      api.discoverEspnLeagues(
        Number(season) || undefined,
        manualLeagueId.trim() ? Number(manualLeagueId.trim()) : undefined,
      ),
    )
    if (!result) return
    setLeagues(result.leagues)
    setWarnings(result.warnings || [])
    if (result.leagues.length === 1) await choose(result.leagues[0])
  }

  async function choose(league: DiscoveredLeague) {
    setChosen(league)
    setTeamId(league.my_team_id)
    setAutoDetected(league.my_team_id !== null)
    const preview = await run(() => api.previewEspnLeague(league.league_id, league.season))
    if (!preview) return
    setRules(preview.rules)
    setChosen(preview.league)
    setStep('confirm')
  }

  async function confirmAndImport() {
    if (!chosen) return
    const selected = await run(() =>
      api.selectEspnLeague({
        league_id: chosen.league_id,
        season: chosen.season,
        team_id: teamId ?? undefined,
      }),
    )
    if (!selected) return
    const imported = await run(() => api.importEspnLeague())
    if (!imported) return
    setNotice(
      `Imported ${chosen.name} — ${imported.players_imported} players.`,
    )
    setStep('done')
    onChange?.()
  }

  async function disconnect() {
    const result = await run(() => api.disconnectEspn())
    if (!result) return
    setLeagues([])
    setChosen(null)
    setRules(null)
    setPairing(null)
    setStep('credentials')
    setNotice('ESPN disconnected. Stored credentials were deleted.')
    status.reload()
    onChange?.()
  }

  async function generateCode() {
    const code = await run(() => api.createPairingCode())
    if (code) setPairing(code)
  }

  if (status.loading) return <Loading what="connection status" />

  const connected = Boolean(status.data?.credentials_stored)

  return (
    <>
      <Card title="Connect ESPN">
        <ol className="connect-steps" aria-label="Connection progress">
          {(
            [
              ['credentials', 'Connect account'],
              ['pick-league', 'Choose league'],
              ['confirm', 'Verify rules'],
              ['done', 'Import'],
            ] as [Step, string][]
          ).map(([key, label], index) => (
            <li key={key} className={step === key ? 'active' : ''}>
              <span className="step-number">{index + 1}</span>
              {label}
            </li>
          ))}
        </ol>

        {error && <Banner kind="error">{error}</Banner>}
        {notice && !error && <Banner kind="info">{notice}</Banner>}

        {step === 'credentials' && (
          <div className="connect-panel">
            <p className="small">
              ESPN has no API key — your league is reached with the same two
              session cookies your browser already holds. They are encrypted
              before storage, never logged, and never returned by this app.
              They are session credentials, though: anyone holding both can act
              as you on ESPN, which is why <strong>Disconnect ESPN</strong> below
              deletes them outright, and why signing out of ESPN kills them at
              source. Full detail in <code>docs/espn-connection.md</code>.
            </p>

            <label htmlFor="connect-swid">SWID cookie</label>
            <input
              id="connect-swid"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={swid}
              onChange={(event) => setSwid(event.target.value)}
              placeholder="{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}"
            />

            <label htmlFor="connect-s2">espn_s2 cookie</label>
            <input
              id="connect-s2"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={s2}
              onChange={(event) => setS2(event.target.value)}
              placeholder="AEB..."
            />

            <label htmlFor="connect-season">Season</label>
            <input
              id="connect-season"
              type="number"
              value={season}
              onChange={(event) => setSeason(event.target.value)}
            />

            <div className="row wrap">
              <button
                className="btn primary"
                disabled={busy || !swid.trim() || !s2.trim()}
                onClick={saveCredentials}
              >
                {busy ? 'Connecting…' : 'Connect ESPN account'}
              </button>
              <button className="btn sm" onClick={() => setShowExtension((v) => !v)}>
                Use the browser extension instead
              </button>
            </div>

            <details className="small" style={{ marginTop: 12 }}>
              <summary>Where do I find these?</summary>
              <p>
                Sign in at espn.com, open DevTools (F12) → Application → Cookies
                → <code>espn.com</code>, and copy <code>SWID</code> and{' '}
                <code>espn_s2</code>.
              </p>
              <p className="tiny faint">
                <code>espn_s2</code> is an HttpOnly cookie, so no bookmarklet or
                page script can read it — that is a browser security boundary,
                not something this app can work around. The extension can,
                because it uses the browser's own cookies API with your
                permission.
              </p>
            </details>

            {showExtension && (
              <div className="connect-panel inset">
                <h4>Browser extension</h4>
                <p className="small">
                  Load <code>browser-extension/</code> at{' '}
                  <code>chrome://extensions</code> (Developer mode → Load
                  unpacked), open your ESPN league, and click it. Generate a
                  pairing code here and type it into the extension once.
                </p>
                <button className="btn sm" onClick={generateCode} disabled={busy}>
                  Generate pairing code
                </button>
                {pairing && (
                  <p className="pairing-code">
                    <code>{pairing.code}</code>
                    <span className="tiny faint">
                      {' '}
                      single use · expires in {Math.round(pairing.expires_in_seconds / 60)} min
                    </span>
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {step === 'pick-league' && (
          <div className="connect-panel">
            <div className="row wrap">
              <input
                type="number"
                value={season}
                onChange={(event) => setSeason(event.target.value)}
                aria-label="Season"
                style={{ maxWidth: 110 }}
              />
              <button className="btn primary" onClick={discover} disabled={busy}>
                {busy ? 'Asking ESPN…' : 'Find my leagues'}
              </button>
            </div>

            {leagues.length > 0 && (
              <>
                <h4>
                  Found {leagues.length} ESPN league{leagues.length === 1 ? '' : 's'}
                </h4>
                <div className="league-options">
                  {leagues.map((league) => (
                    <LeagueRow
                      key={`${league.league_id}-${league.season}`}
                      league={league}
                      selected={chosen?.league_id === league.league_id}
                      onSelect={() => choose(league)}
                    />
                  ))}
                </div>
              </>
            )}

            {warnings.map((warning) => (
              <Banner key={warning} kind="warn">
                {warning}
              </Banner>
            ))}

            <details className="small">
              <summary>Enter a league id by hand</summary>
              <p className="tiny faint">
                Some older leagues do not appear in ESPN's account list. A
                league id typed here is confirmed against ESPN exactly like a
                discovered one.
              </p>
              <div className="row wrap">
                <input
                  type="number"
                  placeholder="League id"
                  value={manualLeagueId}
                  onChange={(event) => setManualLeagueId(event.target.value)}
                />
                <button className="btn sm" onClick={discover} disabled={busy}>
                  Check it
                </button>
              </div>
            </details>
          </div>
        )}

        {step === 'confirm' && chosen && rules && (
          <div className="connect-panel">
            <h4>{chosen.name}</h4>
            <p className="small">
              {chosen.team_count} teams · {chosen.season} · league {chosen.league_id}
            </p>

            <label htmlFor="connect-team">Your team</label>
            <select
              id="connect-team"
              value={teamId ?? ''}
              onChange={(event) =>
                setTeamId(event.target.value ? Number(event.target.value) : null)
              }
            >
              <option value="">Not sure yet</option>
              {chosen.teams.map((team) => (
                <option key={team.espn_team_id} value={team.espn_team_id}>
                  {team.name}
                  {team.owners?.length ? ` — ${team.owners[0]}` : ''}
                </option>
              ))}
            </select>
            <p className="tiny faint">
              {autoDetected
                ? 'Detected from your ESPN account. Change it if that is wrong.'
                : 'Your ESPN id did not match an owner in this league — pick your team.'}
            </p>

            <RulesReview rules={rules} league={chosen} />

            <div className="row wrap">
              <button className="btn primary" onClick={confirmAndImport} disabled={busy}>
                {busy ? 'Importing…' : 'These are correct — import'}
              </button>
              <button className="btn sm" onClick={() => setStep('pick-league')} disabled={busy}>
                Back to leagues
              </button>
            </div>
          </div>
        )}

        {step === 'done' && (
          <div className="connect-panel">
            <Banner kind="info">{notice || 'Imported.'}</Banner>
            <div className="row wrap">
              <button className="btn primary" onClick={() => navigate('/week')}>
                Open the war room
              </button>
              <button className="btn sm" onClick={() => setStep('pick-league')}>
                Connect a different league
              </button>
            </div>
          </div>
        )}
      </Card>

      {connected && (
        <Card title="Stored connection">
          <div className="small">
            ESPN credentials are stored for this account
            {status.data?.espn_league_id
              ? ` · league ${status.data.espn_league_id} · ${status.data.espn_season}`
              : ' · no league selected yet'}
            .
          </div>
          <p className="tiny faint">
            The values themselves are encrypted and cannot be read back — not by
            this screen, and not by any API response.
          </p>
          <button className="btn danger sm" onClick={disconnect} disabled={busy}>
            Disconnect ESPN
          </button>
          <p className="tiny faint">
            Deletes the stored cookies, the selected league and any pairing
            codes. Imported players and draft picks are left alone. To kill the
            cookies at source, sign out of ESPN.
          </p>
        </Card>
      )}
    </>
  )
}
