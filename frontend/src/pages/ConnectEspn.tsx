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

import { useEffect, useMemo, useState } from 'react'
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

//: Credential-acquisition methods, in the order they are offered. They all
//: converge on the same stored SWID + espn_s2 — only the acquisition differs.
type Method = 'otp' | 'public' | 'extension' | 'manual'

/**
 * Whether this is a phone or tablet.
 *
 * Used only to change what we *say*, never what we allow -- a desktop user who
 * trips this still gets every option. The point is that a phone user should be
 * told up front that a private league needs a laptop once, rather than
 * discovering it after hunting for DevTools that do not exist on iOS.
 */
function isMobileBrowser(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Android|iPhone|iPad|iPod|Mobile|Silk/i.test(navigator.userAgent)
}

function currentSeason(): number {
  // ESPN's season flips over in the spring; before that, last year's league is
  // still the live one.
  const now = new Date()
  return now.getMonth() >= 4 ? now.getFullYear() : now.getFullYear() - 1
}

/** Pull a league id out of a pasted ESPN URL, or keep a plain number. */
function extractLeagueId(input: string): string {
  const value = (input || '').trim()
  const fromUrl = value.match(/leagueid=(\d+)/i) || value.match(/\/leagues?\/(\d+)/i)
  if (fromUrl) return fromUrl[1]
  const digits = value.match(/\d+/)
  return digits ? digits[0] : value.replace(/\D/g, '')
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
  const [method, setMethod] = useState<Method>('otp')
  const [methodPinned, setMethodPinned] = useState(false)
  const [autoAdvanced, setAutoAdvanced] = useState(false)
  const isMobile = useMemo(isMobileBrowser, [])

  // OTP (ESPN Email Code) — the primary method.
  const [email, setEmail] = useState('')
  const [otpFlowId, setOtpFlowId] = useState('')
  const [otpSent, setOtpSent] = useState(false)
  const [code, setCode] = useState('')

  const otpAvailable = status.data?.otp_available ?? false

  // Default to Email Code when the server supports it; otherwise Public Link,
  // since that is the only other method a phone can complete. Only until the
  // user picks one themselves.
  useEffect(() => {
    if (!status.data || methodPinned) return
    setMethod(status.data.otp_available ? 'otp' : 'public')
  }, [status.data, methodPinned])

  function chooseMethod(next: Method) {
    setMethodPinned(true)
    setError('')
    setMethod(next)
  }

  // Credentials already stored (from a previous visit, or the extension) means
  // there is nothing to type -- go straight to finding leagues. Only once,
  // though: a user who deliberately came back to re-enter expired cookies must
  // not be bounced straight out of the form again.
  useEffect(() => {
    if (status.data?.credentials_stored && step === 'credentials' && !autoAdvanced) {
      setAutoAdvanced(true)
      setStep('pick-league')
      setNotice('ESPN credentials are already stored for this account.')
    }
    if (status.data?.espn_season) setSeason(String(status.data.espn_season))
  }, [status.data])

  function reenterCredentials() {
    setAutoAdvanced(true)
    setMethodPinned(true)
    setMethod(otpAvailable ? 'otp' : 'manual')
    setError('')
    setNotice('Reconnect your ESPN account.')
    setStep('credentials')
  }

  async function sendOtpCode() {
    const result = await run(() => api.espnOtpStart(email.trim()))
    if (!result) return
    setOtpFlowId(result.flow_id)
    setOtpSent(true)
    setNotice('ESPN sent a login code to your email. Enter it below.')
  }

  async function verifyOtpCode() {
    const result = await run(() => api.espnOtpVerify(otpFlowId, code.trim()))
    if (!result) return
    setCode('')
    setOtpSent(false)
    setNotice(
      result.proof?.confirmed
        ? `Connected and verified — ${result.proof.detail}`
        : 'Connected.',
    )
    status.reload()
    setStep('pick-league')
    await discover()
  }

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

  async function connectPublicLeague() {
    // Nothing is stored on the way in. A public league is readable with no
    // credentials at all, so this asks ESPN directly and only persists a choice
    // once the user confirms the rules, exactly like the private path.
    const id = Number(manualLeagueId.trim())
    if (!id) return
    const result = await run(() => api.discoverEspnLeagues(Number(season) || undefined, id))
    if (!result) return
    if (result.leagues.length === 0) {
      setError(
        `ESPN did not return league ${id} without credentials. It is probably ` +
          'private — connect your ESPN account from a desktop browser instead.',
      )
      return
    }
    setLeagues(result.leagues)
    setWarnings([])
    setStep('pick-league')
    await choose(result.leagues[0])
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

        {error && (
          <Banner kind="error">
            {error}
            {status.data?.credentials_stored && step !== 'credentials' && (
              <div style={{ marginTop: 8 }}>
                <button className="btn sm" onClick={reenterCredentials}>
                  Re-enter ESPN cookies
                </button>
                <div className="tiny faint" style={{ marginTop: 4 }}>
                  ESPN cookies expire when you sign out of ESPN, and eventually
                  on their own. Re-copying them fixes it.
                </div>
              </div>
            )}
          </Banner>
        )}
        {notice && !error && <Banner kind="info">{notice}</Banner>}

        {step === 'credentials' && (
          <div className="connect-panel">
            {/* Method picker, in priority order. All four converge on the same
                stored SWID + espn_s2 — only how they are acquired differs. */}
            <div className="method-picker" role="tablist" aria-label="Connection method">
              <button
                role="tab"
                aria-selected={method === 'otp'}
                className={`method-tab${method === 'otp' ? ' active' : ''}`}
                onClick={() => chooseMethod('otp')}
              >
                <span>ESPN Email Code</span>
                <span className="method-badge">Recommended</span>
              </button>
              <button
                role="tab"
                aria-selected={method === 'public'}
                className={`method-tab${method === 'public' ? ' active' : ''}`}
                onClick={() => chooseMethod('public')}
              >
                <span>Public League Link</span>
              </button>
              {!isMobile && (
                <button
                  role="tab"
                  aria-selected={method === 'extension'}
                  className={`method-tab${method === 'extension' ? ' active' : ''}`}
                  onClick={() => chooseMethod('extension')}
                >
                  <span>Browser Extension</span>
                </button>
              )}
              <button
                role="tab"
                aria-selected={method === 'manual'}
                className={`method-tab${method === 'manual' ? ' active' : ''}`}
                onClick={() => chooseMethod('manual')}
              >
                <span>Manual — Advanced</span>
              </button>
            </div>

            {/* 1 — ESPN Email Code (OTP): the primary method. */}
            {method === 'otp' && (
              <div className="connect-panel">
                <Banner kind="warn">
                  <strong>Experimental.</strong> New, and not yet proven against
                  ESPN live. If it does not work, use Public League Link or Manual
                  below — those are stable.
                </Banner>

                {!otpAvailable ? (
                  <p className="small">
                    ESPN Email Code is not enabled on this server yet. Use{' '}
                    <button className="linklike" onClick={() => chooseMethod('public')}>
                      Public League Link
                    </button>{' '}
                    or{' '}
                    <button className="linklike" onClick={() => chooseMethod('manual')}>
                      Manual
                    </button>{' '}
                    instead.
                  </p>
                ) : (
                  <>
                    <p className="small">
                      Enter your ESPN email. ESPN sends you a six-digit login
                      code — no password needed. This works for public and private
                      leagues, and because it signs you in, it verifies your team
                      is really yours.
                    </p>

                    {!otpSent ? (
                      <>
                        <label htmlFor="otp-email">ESPN email</label>
                        <input
                          id="otp-email"
                          type="email"
                          autoComplete="email"
                          inputMode="email"
                          value={email}
                          onChange={(event) => setEmail(event.target.value)}
                          placeholder="you@example.com"
                        />
                        <button
                          className="btn primary"
                          disabled={busy || !email.trim()}
                          onClick={sendOtpCode}
                        >
                          {busy ? 'Sending…' : 'Send ESPN code'}
                        </button>
                      </>
                    ) : (
                      <>
                        <label htmlFor="otp-code">Six-digit code</label>
                        <input
                          id="otp-code"
                          type="text"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={8}
                          value={code}
                          onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))}
                          placeholder="123456"
                        />
                        <div className="row wrap">
                          <button
                            className="btn primary"
                            disabled={busy || code.trim().length < 4}
                            onClick={verifyOtpCode}
                          >
                            {busy ? 'Verifying…' : 'Verify & connect'}
                          </button>
                          <button
                            className="btn sm"
                            disabled={busy}
                            onClick={() => {
                              setOtpSent(false)
                              setCode('')
                              setNotice('')
                            }}
                          >
                            Use a different email
                          </button>
                        </div>
                        <p className="tiny faint">
                          The code expires quickly. Didn't get it? Check spam, then
                          start again.
                        </p>
                      </>
                    )}
                  </>
                )}
              </div>
            )}

            {/* 2 — Public League Link: quick, but ownership stays unverified. */}
            {method === 'public' && (
              <div className="connect-panel">
                <p className="small">
                  Paste a public ESPN league's link or id. This works on a phone
                  with no sign-in — but because we can't confirm your ESPN
                  identity, your team ownership is marked{' '}
                  <strong>Unverified</strong>. A private league can't be read this
                  way; use Email Code or Manual for that.
                </p>

                <label htmlFor="public-league-id">League link or id</label>
                <input
                  id="public-league-id"
                  type="text"
                  inputMode="numeric"
                  value={manualLeagueId}
                  onChange={(event) => setManualLeagueId(extractLeagueId(event.target.value))}
                  placeholder="123456 or paste the league URL"
                />
                <p className="tiny faint">
                  From your league URL: <code>…/league?leagueId=123456</code>
                </p>

                <label htmlFor="public-season">Season</label>
                <input
                  id="public-season"
                  type="number"
                  value={season}
                  onChange={(event) => setSeason(event.target.value)}
                />

                <button
                  className="btn primary"
                  disabled={busy || !manualLeagueId.trim()}
                  onClick={connectPublicLeague}
                >
                  {busy ? 'Checking…' : 'Find this league'}
                </button>
              </div>
            )}

            {/* 3 — Browser extension: desktop backup, developer-mode only. */}
            {method === 'extension' && !isMobile && (
              <div className="connect-panel">
                <Banner kind="info">
                  Backup method. A proof-of-concept extension, not published to any
                  store — Chrome/Edge on desktop, loaded in Developer mode.
                </Banner>
                <p className="small">
                  Load <code>browser-extension/</code> at{' '}
                  <code>chrome://extensions</code> (Developer mode → Load unpacked),
                  open your ESPN league, and click it. Generate a pairing code here
                  and type it into the extension once. It reads ESPN's cookies and
                  sends them straight to this server.
                </p>
                <button className="btn sm" onClick={generateCode} disabled={busy}>
                  Generate pairing code
                </button>
                {pairing && (
                  <p className="pairing-code">
                    <code>{pairing.code}</code>
                    <span className="tiny faint">
                      {' '}
                      single use · expires in{' '}
                      {Math.round(pairing.expires_in_seconds / 60)} min
                    </span>
                  </p>
                )}
              </div>
            )}

            {/* 4 — Manual: the permanent last resort, always available. */}
            {method === 'manual' && (
              <div className="connect-panel">
                <Banner kind="info">
                  Advanced / backup. Use this when Email Code is down, or to
                  connect a private league from a desktop. On a phone the values
                  below are not reachable — a desktop browser is needed once.
                </Banner>
                <p className="small">
                  Paste ESPN's two session cookies. They are encrypted before
                  storage, never logged, and never shown again. Anyone holding both
                  can act as you on ESPN — never post them, screenshot them, or put
                  them in GitHub. <strong>Disconnect ESPN</strong> deletes them;
                  signing out of ESPN invalidates them at source.
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

                <button
                  className="btn primary"
                  disabled={busy || !swid.trim() || !s2.trim()}
                  onClick={saveCredentials}
                >
                  {busy ? 'Connecting…' : 'Connect ESPN account'}
                </button>

                <details className="small" style={{ marginTop: 12 }}>
                  <summary>Where do I find these? (desktop)</summary>
                  <ol className="tiny">
                    <li>Sign in at espn.com and open your league.</li>
                    <li>Press F12 → <strong>Application</strong> tab.</li>
                    <li>Storage → Cookies → <code>espn.com</code>.</li>
                    <li>
                      Copy the <strong>Value</strong> of <code>SWID</code> (keep the
                      braces) and <code>espn_s2</code> (a long string — copy it
                      exactly, do not decode or trim it).
                    </li>
                  </ol>
                  <p className="tiny faint">
                    <code>espn_s2</code> is HttpOnly, so phone browsers cannot read
                    it and no bookmarklet can — this path needs a desktop once.
                    Full runbook: <code>docs/espn-connection-backup.md</code>.
                  </p>
                </details>
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
