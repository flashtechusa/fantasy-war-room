/**
 * Phase 1 -- League Settings.
 *
 * The point of this screen is verification: every rule the ranking engine uses
 * is shown here, straight from the platform, so you can confirm the board is
 * being built from YOUR league before you trust a single recommendation.
 *
 * It is also where the platform is chosen. ESPN and Yahoo are alternatives --
 * a league lives on one of them -- so the connection card swaps rather than
 * stacking both sets of credentials on screen.
 */

import { useEffect, useState } from 'react'
import { api, type YahooLeagueOption } from '../api'
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

/**
 * Which platform this install reads.
 *
 * Shown above the connection card because picking the wrong one is the single
 * failure that looks like success: the app imports *something* and every
 * number is quietly about the wrong league.
 */
function PlatformPicker({ onChanged }: { onChanged: () => void }) {
  const config = useAsync(() => api.config(), [])
  const [saving, setSaving] = useState(false)
  const platform = config.data?.platform ?? 'espn'

  async function choose(next: 'espn' | 'yahoo') {
    if (next === platform) return
    setSaving(true)
    try {
      await api.saveConfig({ platform: next })
      config.reload()
      onChanged()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="Platform">
      <div className="row" style={{ gap: 8 }}>
        <button
          className={`btn block ${platform === 'espn' ? 'primary' : ''}`}
          disabled={saving}
          onClick={() => choose('espn')}
        >
          ESPN
        </button>
        <button
          className={`btn block ${platform === 'yahoo' ? 'primary' : ''}`}
          disabled={saving}
          onClick={() => choose('yahoo')}
        >
          Yahoo
        </button>
      </div>
      <div className="tiny faint" style={{ marginTop: 8 }}>
        Everything past the import — rankings, lineups, waivers, trades — works the same
        on either. Switching platforms means importing that league again.
      </div>
    </Card>
  )
}

/**
 * Connecting a Yahoo account.
 *
 * Yahoo has no cookie shortcut, so this is three steps rather than one: a free
 * app registered at developer.yahoo.com, Yahoo's approval screen, and the code
 * it hands back. Once done it stays done -- the token refreshes itself.
 */
function YahooConnectionForm({ onSaved }: { onSaved: () => void }) {
  const status = useAsync(() => api.yahooStatus(), [])
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [redirect, setRedirect] = useState('')
  const [code, setCode] = useState('')
  const [authUrl, setAuthUrl] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [leagues, setLeagues] = useState<YahooLeagueOption[] | null>(null)

  const current = status.data
  const connected = Boolean(current?.connected)
  const appConfigured = Boolean(current?.app_configured)

  async function saveApp() {
    setBusy('app')
    setNote(null)
    try {
      await api.saveYahooApp({
        yahoo_client_id: clientId.trim(),
        yahoo_client_secret: clientSecret.trim(),
        yahoo_redirect_uri: redirect.trim() || 'oob',
      })
      setClientId('')
      setClientSecret('')
      status.reload()
      setNote({ ok: true, text: 'App saved. Now connect your Yahoo account.' })
      onSaved()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function startAuth() {
    setBusy('auth')
    setNote(null)
    try {
      const result = await api.startYahooAuth()
      setAuthUrl(result.authorize_url)
      window.open(result.authorize_url, '_blank', 'noopener')
      setNote({ ok: true, text: result.instructions })
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function completeAuth() {
    setBusy('code')
    setNote(null)
    try {
      await api.completeYahooAuth(code.trim())
      setCode('')
      setAuthUrl('')
      status.reload()
      setNote({ ok: true, text: 'Connected to Yahoo. Now pick your league.' })
      await loadLeagues()
      onSaved()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function loadLeagues() {
    setBusy('leagues')
    try {
      const result = await api.yahooLeagues()
      setLeagues(result.leagues)
      if (!result.leagues.length) {
        setNote({ ok: false, text: 'Yahoo returned no leagues for this account.' })
      } else if (!result.filtered_to_season) {
        setNote({
          ok: false,
          text: `No leagues for ${result.season}. The seasons below are what this account is in — check the season setting.`,
        })
      }
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function chooseLeague(leagueId: number) {
    setBusy('choose')
    setNote(null)
    try {
      const response = await api.saveConfig({ platform: 'yahoo', yahoo_league_id: leagueId })
      status.reload()
      if (response.connection?.connected) {
        setNote({ ok: true, text: `Connected to "${response.connection.league_name}". Import it below.` })
      } else if (response.connection) {
        setNote({ ok: false, text: response.connection.detail ?? 'Could not reach Yahoo.' })
      }
      onSaved()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function disconnect() {
    setBusy('disconnect')
    try {
      await api.disconnectYahoo()
      setLeagues(null)
      status.reload()
      onSaved()
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card title="Yahoo connection">
      <div className="small" style={{ marginBottom: 10 }}>
        {connected ? (
          <>
            Connected
            {current?.league_id ? (
              <>
                {' '}· league <strong>{current.league_id}</strong> · {current.season}
              </>
            ) : (
              <span style={{ color: 'var(--warn)' }}> · no league chosen yet</span>
            )}
            {current?.connection?.league_name && (
              <div className="tiny faint">{current.connection.league_name}</div>
            )}
          </>
        ) : (
          <span className="muted">
            {appConfigured
              ? 'App registered. Connect your Yahoo account to continue.'
              : 'Yahoo needs a free developer app before it will hand over league data.'}
          </span>
        )}
      </div>

      {!connected && (
        <>
          <div className="tiny faint" style={{ marginBottom: 8 }}>
            Step 1 — create an app at developer.yahoo.com/apps/create (Fantasy Sports,
            Read permission), then paste its two values here.
          </div>
          <label className="tiny faint">
            CLIENT ID {appConfigured && <span className="muted">(stored — leave blank to keep)</span>}
          </label>
          <input
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="dj0yJmk9..."
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">CLIENT SECRET</label>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={appConfigured ? '••••••••••••  (stored)' : 'Paste the client secret'}
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">
            REDIRECT URI <span className="muted">(leave blank for "oob")</span>
          </label>
          <input
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder={current?.redirect_uri ?? 'oob'}
            value={redirect}
            onChange={(e) => setRedirect(e.target.value)}
            style={{ marginBottom: 10 }}
          />
          <button
            className="btn block"
            onClick={saveApp}
            disabled={busy !== null || !clientId.trim() || !clientSecret.trim()}
          >
            {busy === 'app' ? 'Saving…' : 'Save Yahoo app'}
          </button>

          <div className="tiny faint" style={{ margin: '12px 0 8px' }}>
            Step 2 — approve access at Yahoo, then paste the code it shows you.
          </div>
          <button
            className="btn primary block"
            onClick={startAuth}
            disabled={busy !== null || !appConfigured}
          >
            {busy === 'auth' ? 'Opening Yahoo…' : 'Connect Yahoo'}
          </button>
          {authUrl && (
            <div className="tiny faint" style={{ marginTop: 6, wordBreak: 'break-all' }}>
              Didn't open?{' '}
              <a href={authUrl} target="_blank" rel="noreferrer">
                Use this link
              </a>
              .
            </div>
          )}
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="Paste the code from Yahoo"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <button
              className="btn"
              onClick={completeAuth}
              disabled={busy !== null || !code.trim()}
            >
              {busy === 'code' ? 'Connecting…' : 'Finish'}
            </button>
          </div>
        </>
      )}

      {connected && (
        <>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn block" onClick={loadLeagues} disabled={busy !== null}>
              {busy === 'leagues' ? 'Loading…' : 'Show my leagues'}
            </button>
            <button className="btn block" onClick={disconnect} disabled={busy !== null}>
              Disconnect
            </button>
          </div>

          {leagues && leagues.length > 0 && (
            <select
              value={current?.league_id ?? ''}
              disabled={busy !== null}
              onChange={(event) =>
                event.target.value && chooseLeague(Number(event.target.value))
              }
              style={{ marginTop: 10 }}
            >
              <option value="">Pick your league…</option>
              {leagues.map((entry) => (
                <option key={entry.league_key} value={entry.league_id}>
                  {entry.name} — {entry.season} · {entry.team_count} teams
                </option>
              ))}
            </select>
          )}
        </>
      )}

      <div className="tiny faint" style={{ marginTop: 8 }}>
        The client secret and the tokens are stored in your local database and are never
        sent back to the browser. Yahoo access is read-only.
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
 * ESPN's public projections.
 *
 * Yahoo publishes no projections at all -- not one projected stat for anybody
 * -- so a Yahoo league has nothing to rank until this runs. It needs no
 * credentials: ESPN publishes projections for a default league, and their raw
 * stat lines get re-scored under the Yahoo league's own rules like every other
 * source.
 */
function PublicProjectionsCard({ onImported }: { onImported: () => void }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  async function run() {
    setBusy(true)
    setNote(null)
    try {
      const report = await api.importPublicProjections()
      const base = `Matched ${report.matched} of ${report.received} players — ${Math.round(
        report.coverage * 100,
      )}% of your player pool.`
      setNote({ ok: report.enabled, text: report.warning ? `${base} ${report.warning}` : base })
      onImported()
    } catch (error) {
      setNote({ ok: false, text: (error as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Projections for this league">
      <div className="small muted" style={{ marginBottom: 10 }}>
        Yahoo publishes ownership, ADP and rosters but no projections, so the rankings run
        on ESPN's public projections instead — re-scored under your Yahoo league's rules.
        This runs automatically on import; use the button after ESPN updates its numbers.
      </div>
      <button className="btn primary block" onClick={run} disabled={busy}>
        {busy ? 'Importing…' : 'Refresh projections'}
      </button>
      {note && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={note.ok ? 'info' : 'error'}>{note.text}</Banner>
        </div>
      )}
    </Card>
  )
}

/**
 * A second projection source, so everything does not rest on ESPN's numbers.
 *
 * Bring your own key: nothing is bundled, and the source stays inert until one
 * is entered. FantasyPros issue free keys for personal, non-commercial use, so
 * whether a given install may use this is a question for whoever holds the key.
 */
function FantasyProsCard({ onImported }: { onImported: () => void }) {
  const config = useAsync(() => api.config(), [])
  const [key, setKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [covered, setCovered] = useState<string[]>([])

  const stored = Boolean(config.data?.fantasypros_key_set)

  async function saveKey() {
    setSaving(true)
    setResult(null)
    try {
      await api.saveConfig({ fantasypros_api_key: key.trim() })
      setKey('')
      // Confirm against the server rather than assuming: a save that is
      // accepted but not stored is exactly the failure this had.
      const after = await api.config()
      if (after.fantasypros_key_set) {
        setResult({ ok: true, text: 'Key saved. Import to pull projections.' })
      } else {
        setResult({ ok: false, text: 'The server did not store the key. Nothing was saved.' })
      }
      config.reload()
    } catch (error) {
      setResult({ ok: false, text: (error as Error).message })
    } finally {
      setSaving(false)
    }
  }

  async function runImport() {
    setImporting(true)
    setResult(null)
    try {
      const report = await api.importFantasyPros()
      const missed = report.unmatched_count + report.ambiguous_count
      const base =
        `Matched ${report.matched} of ${report.received} players` +
        (missed ? `, ${missed} skipped` : '') +
        ` — ${Math.round(report.coverage * 100)}% of your player pool.`
      setCovered(report.matched_sample ?? [])
      setResult({
        // Partial coverage is not a success: the source is stored but not used.
        ok: report.enabled,
        text: report.warning ? `${base} ${report.warning}` : base,
      })
      onImported()
    } catch (error) {
      setResult({ ok: false, text: (error as Error).message })
    } finally {
      setImporting(false)
    }
  }

  return (
    <Card title="FantasyPros projections (optional)">
      <div className="small muted" style={{ marginBottom: 10 }}>
        A second opinion alongside ESPN's numbers. Their stat lines get re-scored under
        your league's rules the same way, so this changes the inputs, not the method.
      </div>

      <label className="tiny faint">
        API key {stored && <span className="muted">(stored — leave blank to keep)</span>}
      </label>
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        placeholder={stored ? '••••••••••••  (stored)' : 'Paste your FantasyPros API key'}
        value={key}
        onChange={(e) => setKey(e.target.value)}
        style={{ marginBottom: 10 }}
      />
      <div className="row" style={{ gap: 8 }}>
        <button className="btn block" onClick={saveKey} disabled={saving || !key.trim()}>
          {saving ? 'Saving…' : 'Save key'}
        </button>
        <button
          className="btn primary block"
          onClick={runImport}
          disabled={importing || !stored}
        >
          {importing ? 'Importing…' : 'Import projections'}
        </button>
      </div>

      <div className="tiny faint" style={{ marginTop: 8 }}>
        Get a key at api.fantasypros.com. Free keys allow 50 requests a day and an import
        uses six. The key is stored locally and never sent back to the browser.
      </div>

      {result && (
        <div style={{ marginTop: 10 }}>
          <Banner kind={result.ok ? 'info' : 'error'}>{result.text}</Banner>
        </div>
      )}

      {covered.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary className="small muted" style={{ cursor: 'pointer' }}>
            Which {covered.length} players FantasyPros covered
          </summary>
          <div className="row wrap" style={{ gap: 5, marginTop: 8 }}>
            {covered.map((name) => (
              <span key={name} className="pill">
                {name}
              </span>
            ))}
          </div>
        </details>
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
  const config = useAsync(() => api.config(), [])
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
      await api.saveConfig(patch)
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

export default function LeagueSettings({ onChange }: { onChange?: () => void }) {
  const health = useAsync(() => api.health(), [])
  const config = useAsync(() => api.config(), [])
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

      <FantasyProsCard onImported={() => onChange?.()} />

      {config.data?.platform === 'yahoo' && (
        <PublicProjectionsCard onImported={() => onChange?.()} />
      )}

      <PlatformPicker
        onChanged={() => {
          config.reload()
          health.reload()
          onChange?.()
        }}
      />

      {config.data?.platform === 'yahoo' ? (
        <YahooConnectionForm
          onSaved={() => {
            config.reload()
            health.reload()
            league.reload()
            onChange?.()
          }}
        />
      ) : (
        <EspnConnectionForm
          onSaved={() => {
            health.reload()
            league.reload()
            onChange?.()
          }}
        />
      )}

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
              {info.scoring.rules.length} active rules of {info.scoring.rule_count} returned by{' '}
              {info.source === 'yahoo' ? 'Yahoo' : 'ESPN'}. Every projection on the board is
              computed from these.
            </div>

            {/* Yahoo numbers its stat categories differently, so they are
                translated on import. Where that translation is lossy, saying so
                here is the difference between a board you can check and one you
                have to trust. */}
            {info.scoring.translation && (
              <>
                {info.scoring.translation.combined.length > 0 && (
                  <Banner kind="warn">
                    {info.scoring.translation.combined.length} of your categories share a
                    scoring category here: {info.scoring.translation.combined.join('; ')}.
                  </Banner>
                )}
                {info.scoring.translation.approximate.length > 0 && (
                  <div className="tiny faint" style={{ marginBottom: 8 }}>
                    Band edges differ slightly for:{' '}
                    {info.scoring.translation.approximate.join(', ')}.
                  </div>
                )}
                {info.scoring.translation.unmapped.length > 0 && (
                  <Banner kind="warn">
                    No projection source covers{' '}
                    {info.scoring.translation.unmapped.join(', ')}, so those rules score
                    zero on the board. They are listed below for completeness.
                  </Banner>
                )}
              </>
            )}
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
