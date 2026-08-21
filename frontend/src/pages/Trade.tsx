/** Phase 8 -- trade finder + analyzer. */

import { useRef, useState } from 'react'
import {
  api,
  type TradeFinderResponse,
  type TradeProposal,
  type TradeResponse,
  type WeekPlayer,
} from '../api'
import { Banner, Card, Loading } from '../components'
import { useAsync } from '../useAsync'

const VERDICT_COLOR: Record<string, string> = {
  accept: 'var(--elite)',
  'lean accept': 'var(--good)',
  neutral: 'var(--fair)',
  'lean reject': 'var(--reach)',
  reject: 'var(--danger)',
}

function PlayerPicker({
  players,
  selected,
  onToggle,
  empty,
}: {
  players: WeekPlayer[]
  selected: Set<number>
  onToggle: (id: number) => void
  empty: string
}) {
  if (players.length === 0) return <div className="small faint">{empty}</div>
  return (
    <div className="row wrap" style={{ gap: 6 }}>
      {players.map((player) => {
        const on = selected.has(player.espn_player_id)
        return (
          <button
            key={player.espn_player_id}
            className={`chip ${on ? 'active' : ''}`}
            style={{ minHeight: 36, fontSize: 12.5 }}
            onClick={() => onToggle(player.espn_player_id)}
            aria-pressed={on}
          >
            {player.name} · {player.position}
          </button>
        )
      })}
    </div>
  )
}

function SideCard({ side }: { side: NonNullable<TradeResponse['their_side']> }) {
  const good = side.season_delta > 0
  return (
    <Card title={side.label}>
      <div className="row between">
        <div>
          <div className="tiny faint">REST OF SEASON</div>
          <div
            className="mono"
            style={{
              fontSize: 24,
              fontWeight: 750,
              color: good ? 'var(--elite)' : 'var(--danger)',
            }}
          >
            {side.season_delta > 0 ? '+' : ''}
            {side.season_delta.toFixed(1)}
          </div>
          <div className="tiny faint">
            {side.season_before.toFixed(0)} → {side.season_after.toFixed(0)}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="tiny faint">THIS WEEK</div>
          <div className="mono" style={{ fontSize: 20, fontWeight: 700 }}>
            {side.week_delta > 0 ? '+' : ''}
            {side.week_delta.toFixed(1)}
          </div>
        </div>
      </div>
      {Object.keys(side.position_changes).length > 0 && (
        <div className="row wrap" style={{ gap: 5, marginTop: 9 }}>
          {Object.entries(side.position_changes).map(([position, delta]) => (
            <span key={position} className="pill">
              {position} {delta > 0 ? '+' : ''}
              {delta}
            </span>
          ))}
        </div>
      )}
      {side.notes.length > 0 && (
        <ul className="reasons" style={{ marginTop: 9 }}>
          {side.notes.map((note, i) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ProposalCard({
  proposal,
  horizon,
  onLoad,
  busy,
  canSend = false,
}: {
  proposal: TradeProposal
  horizon: 'season' | 'week'
  onLoad: (p: TradeProposal) => void
  busy: boolean
  canSend?: boolean
}) {
  const fmt = (n: number) => `${n > 0 ? '+' : ''}${n.toFixed(1)}`
  const horizonLabel = horizon === 'week' ? 'this week' : 'rest of season'
  const [preview, setPreview] = useState<import('../api').TradePreview | null>(null)
  const [pvBusy, setPvBusy] = useState(false)
  const [pvErr, setPvErr] = useState<string | null>(null)
  const [showPayload, setShowPayload] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sendBusy, setSendBusy] = useState(false)
  const [sendResult, setSendResult] = useState<import('../api').TradeSendResult | null>(null)
  const [dropIds, setDropIds] = useState<Set<number>>(new Set())

  const ids = {
    their_team_id: proposal.their_team_id,
    give_ids: proposal.give.map((p) => p.espn_player_id),
    receive_ids: proposal.receive.map((p) => p.espn_player_id),
  }

  const move = preview?.roster_move
  const needDrops = move?.mine.drops_required ?? 0
  const dropsReady = needDrops === 0 || dropIds.size === needDrops

  function toggleDrop(id: number) {
    setDropIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else if (next.size < needDrops) next.add(id)
      return next
    })
  }

  async function doPreview() {
    setPvBusy(true)
    setPvErr(null)
    setSendResult(null)
    setConfirming(false)
    try {
      const res = await api.tradePreview(ids)
      setPreview(res)
      // Pre-select the recommended drops so the safe choice is one tap away.
      setDropIds(new Set(res.roster_move?.recommended_ids ?? []))
    } catch (e) {
      setPvErr((e as Error).message)
    } finally {
      setPvBusy(false)
    }
  }

  async function doSend() {
    setSendBusy(true)
    setPvErr(null)
    try {
      const res = await api.tradeSend({ ...ids, confirm: true, drop_ids: Array.from(dropIds) })
      setSendResult(res)
      setConfirming(false)
    } catch (e) {
      setPvErr((e as Error).message)
    } finally {
      setSendBusy(false)
    }
  }

  return (
    <div className="call" style={{ marginBottom: 10 }}>
      <div className="row between" style={{ alignItems: 'flex-start', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div className="tiny faint">
            {proposal.kind === 'mutual' ? 'BOTH IMPROVE' : 'LONGSHOT'} · with{' '}
            {proposal.their_label}
          </div>
          <div style={{ fontWeight: 650, marginTop: 2, overflowWrap: 'anywhere' }}>
            {proposal.headline}
          </div>
        </div>
        <div style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
          <div className="mono" style={{ fontSize: 20, fontWeight: 750, color: 'var(--elite)' }}>
            You {fmt(proposal.my_delta)}
          </div>
          <div
            className="tiny"
            style={{ color: proposal.their_delta > 0 ? 'var(--good)' : 'var(--muted)' }}
          >
            Them {fmt(proposal.their_delta)}
          </div>
        </div>
      </div>
      <div className="tiny faint" style={{ marginTop: 2 }}>
        pts to each starting lineup, {horizonLabel}
      </div>
      {proposal.reasons.length > 0 && (
        <ul className="reasons" style={{ marginTop: 8 }}>
          {proposal.reasons.slice(0, 3).map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <button className="btn sm" disabled={busy} onClick={() => onLoad(proposal)}>
          Load into analyzer →
        </button>
        {canSend && (
          <button className="btn sm" disabled={pvBusy} onClick={doPreview}>
            {pvBusy ? 'Building…' : preview ? 'Rebuild preview' : 'Preview send to ESPN'}
          </button>
        )}
      </div>

      {pvErr && (
        <div style={{ marginTop: 8 }}>
          <Banner kind="error">{pvErr}</Banner>
        </div>
      )}

      {preview && (
        <div
          style={{
            marginTop: 8,
            padding: 10,
            borderRadius: 8,
            border: '1px solid var(--line, #2a2a2a)',
          }}
        >
          <div className="tiny" style={{ fontWeight: 700, color: 'var(--warn, #d8a657)' }}>
            PREVIEW ONLY — nothing was sent to ESPN
          </div>
          <div className="small" style={{ marginTop: 4 }}>
            {preview.summary}
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {preview.note}
          </div>
          <button
            className="linklike tiny"
            style={{ marginTop: 6 }}
            onClick={() => setShowPayload((s) => !s)}
          >
            {showPayload ? 'Hide' : 'Show'} the exact request
          </button>
          {showPayload && (
            <pre
              className="mono tiny"
              style={{ marginTop: 6, overflowX: 'auto', whiteSpace: 'pre-wrap' }}
            >
              {preview.request.method} {preview.request.url}
              {'\n'}
              {JSON.stringify(preview.request.body, null, 2)}
            </pre>
          )}

          {/* Roster move required — an uneven trade would overflow your roster. */}
          {move?.requires_drop && (
            <div
              style={{
                marginTop: 10,
                padding: 10,
                borderRadius: 8,
                border: '1px solid var(--warn, #d8a657)',
              }}
            >
              <div className="tiny" style={{ fontWeight: 700, color: 'var(--warn, #d8a657)' }}>
                ROSTER MOVE REQUIRED
              </div>
              <div className="small" style={{ marginTop: 4 }}>
                You have {move.mine.current_size}/{move.mine.limit}. This trade sends{' '}
                {proposal.give.length} and receives {proposal.receive.length}, leaving you at{' '}
                {move.mine.resulting_size}/{move.mine.limit}. Pick{' '}
                <strong>{needDrops}</strong> player{needDrops === 1 ? '' : 's'} to drop
                {' '}if it's accepted.
              </div>
              <div className="tiny faint" style={{ margin: '6px 0 4px' }}>
                Selected {dropIds.size}/{needDrops}. Recommended picks are pre-checked.
              </div>
              {move.candidates.map((c) => (
                <label
                  key={c.espn_player_id}
                  className="row"
                  style={{ gap: 8, alignItems: 'center', padding: '3px 0', cursor: 'pointer' }}
                >
                  <input
                    type="checkbox"
                    checked={dropIds.has(c.espn_player_id)}
                    onChange={() => toggleDrop(c.espn_player_id)}
                    disabled={!dropIds.has(c.espn_player_id) && dropIds.size >= needDrops}
                  />
                  <span className="small" style={{ flex: 1, minWidth: 0 }}>
                    {c.name} <span className="faint">({c.position})</span>
                    {c.recommended && (
                      <span className="tiny" style={{ color: 'var(--good, #7ac07a)' }}> · recommended</span>
                    )}
                    {c.creates_hole && (
                      <span className="tiny" style={{ color: 'var(--danger, #d66)' }}> · opens a hole</span>
                    )}
                    {c.is_starter && !c.creates_hole && (
                      <span className="tiny faint"> · current starter</span>
                    )}
                  </span>
                  <span className="tiny faint" style={{ whiteSpace: 'nowrap' }}>
                    {c.projected_points.toFixed(0)} pts · VOR {c.vor.toFixed(0)}
                  </span>
                </label>
              ))}
              {move.their_overflow && (
                <div className="tiny faint" style={{ marginTop: 6 }}>
                  {move.theirs.team_name} would be at {move.theirs.resulting_size}/
                  {move.theirs.limit}; ESPN has them make room when they accept.
                </div>
              )}
            </div>
          )}

          {/* Send controls — a deliberate two-step, because this is irreversible. */}
          {!sendResult && (
            <div style={{ marginTop: 10, borderTop: '1px solid var(--line, #2a2a2a)', paddingTop: 10 }}>
              {!confirming ? (
                <button className="btn sm" disabled={!dropsReady} onClick={() => setConfirming(true)}>
                  {dropsReady ? 'Send this to ESPN…' : `Select ${needDrops} drop(s) first`}
                </button>
              ) : (
                <div>
                  <div className="small" style={{ marginBottom: 6 }}>
                    This sends a <strong>real</strong> proposal to {proposal.their_label}. It
                    cannot be undone from here.
                  </div>
                  <div className="row" style={{ gap: 8 }}>
                    <button
                      className="btn sm primary"
                      disabled={sendBusy || !dropsReady}
                      onClick={doSend}
                    >
                      {sendBusy ? 'Sending…' : 'Confirm — send to ESPN'}
                    </button>
                    <button className="btn sm" disabled={sendBusy} onClick={() => setConfirming(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {sendResult && (
            <div style={{ marginTop: 10 }}>
              <Banner kind={sendResult.ok ? 'info' : 'error'}>
                {sendResult.ok
                  ? `Sent. ESPN accepted the proposal (HTTP ${sendResult.status_code}). Check it in the ESPN app.`
                  : `ESPN did not accept it (HTTP ${sendResult.status_code}).`}
              </Banner>
              <pre
                className="mono tiny"
                style={{ marginTop: 6, overflowX: 'auto', whiteSpace: 'pre-wrap' }}
              >
                {sendResult.response}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FoundTrades({
  onLoad,
  busy,
}: {
  onLoad: (p: TradeProposal) => void
  busy: boolean
}) {
  const [horizon, setHorizon] = useState<'season' | 'week'>('season')
  const [showLongshots, setShowLongshots] = useState(false)
  const found = useAsync<TradeFinderResponse>(() => api.tradeFinder(horizon), [horizon])
  const me = useAsync(() => api.me(), [])
  const canSend = Boolean(me.data?.user?.can_send_trades)

  return (
    <Card title="Trades we found for you">
      <div className="method-picker" role="tablist" aria-label="Trade horizon">
        <button
          role="tab"
          aria-selected={horizon === 'season'}
          className={`method-tab${horizon === 'season' ? ' active' : ''}`}
          onClick={() => setHorizon('season')}
        >
          Rest of season
        </button>
        <button
          role="tab"
          aria-selected={horizon === 'week'}
          className={`method-tab${horizon === 'week' ? ' active' : ''}`}
          onClick={() => setHorizon('week')}
        >
          Win this week
        </button>
      </div>
      <div className="tiny faint" style={{ marginBottom: 10 }}>
        We scan every other team for swaps that lift <strong>your</strong> starting
        lineup — and flag the ones that lift theirs too, since those actually get
        accepted. Nothing is sent to ESPN; propose it there yourself.
      </div>

      {found.loading && <Loading what="trades across the league" />}
      {found.error && <Banner kind="error">{found.error}</Banner>}

      {found.data && found.data.reason === 'no_my_roster' && (
        <Banner kind="info">
          Import your league (and your roster) on the League tab first, then we can
          find trades for you.
        </Banner>
      )}

      {found.data && !found.data.reason && found.data.mutual.length === 0 && (
        <div className="small faint">
          No trade improves both lineups right now. Try “Win this week”, check
          longshots below, or build one by hand.
        </div>
      )}

      {found.data?.mutual.map((p, i) => (
        <ProposalCard
          key={i}
          proposal={p}
          horizon={horizon}
          onLoad={onLoad}
          busy={busy}
          canSend={canSend}
        />
      ))}

      {found.data && found.data.longshots.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <button className="linklike" onClick={() => setShowLongshots((s) => !s)}>
            {showLongshots ? 'Hide' : 'Show'} {found.data.longshots.length} longshot
            {found.data.longshots.length === 1 ? '' : 's'} (good for you, not for them)
          </button>
          {showLongshots &&
            found.data.longshots.map((p, i) => (
              <ProposalCard
                key={`ls-${i}`}
                proposal={p}
                horizon={horizon}
                onLoad={onLoad}
                busy={busy}
                canSend={canSend}
              />
            ))}
        </div>
      )}
    </Card>
  )
}

export default function Trade() {
  const roster = useAsync(() => api.seasonRoster(), [])
  const [give, setGive] = useState<Set<number>>(new Set())
  const [receive, setReceive] = useState<Set<number>>(new Set())
  const [theirTeam, setTheirTeam] = useState<number | ''>('')
  const [theirRoster, setTheirRoster] = useState<WeekPlayer[]>([])
  const [result, setResult] = useState<TradeResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function toggle(set: Set<number>, apply: (s: Set<number>) => void, id: number) {
    const next = new Set(set)
    next.has(id) ? next.delete(id) : next.add(id)
    apply(next)
    setResult(null)
  }

  async function pickTeam(teamId: number | '') {
    setTheirTeam(teamId)
    setReceive(new Set())
    setResult(null)
    if (!teamId) {
      setTheirRoster([])
      return
    }
    // Ask the API for *their* roster directly. This used to fetch my own and
    // filter it by their player ids, which could never match anything, so the
    // picker was always empty.
    try {
      const theirs = await api.seasonRoster(undefined, teamId)
      setTheirRoster(theirs.players)
      setError(
        theirs.players.length === 0
          ? 'That team has no roster imported yet. Re-import on the League tab.'
          : null,
      )
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const analyzerRef = useRef<HTMLDivElement>(null)

  async function analyse() {
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.analyseTrade({
          give: [...give],
          receive: [...receive],
          their_team_id: theirTeam || null,
        }),
      )
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // Populate the manual analyzer from a found proposal and grade it, so the
  // finder and the by-hand tool are one flow, not two.
  async function loadProposal(proposal: TradeProposal) {
    setBusy(true)
    setError(null)
    const giveIds = proposal.give.map((p) => p.espn_player_id)
    const receiveIds = proposal.receive.map((p) => p.espn_player_id)
    setTheirTeam(proposal.their_team_id)
    setGive(new Set(giveIds))
    setReceive(new Set(receiveIds))
    try {
      const theirs = await api.seasonRoster(undefined, proposal.their_team_id)
      setTheirRoster(theirs.players)
      setResult(
        await api.analyseTrade({
          give: giveIds,
          receive: receiveIds,
          their_team_id: proposal.their_team_id,
        }),
      )
      analyzerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (roster.loading) return <Loading what="your roster" />
  if (roster.error) return <Banner kind="error">{roster.error}</Banner>

  const players = roster.data?.players ?? []
  const teams = (roster.data?.teams ?? []).filter((t) => !t.is_mine)

  return (
    <>
      {error && <Banner kind="error">{error}</Banner>}

      <FoundTrades onLoad={loadProposal} busy={busy} />

      <div ref={analyzerRef} className="eyebrow" style={{ margin: '14px 0 6px' }}>
        Or build one by hand
      </div>

      <Card title="You give">
        <PlayerPicker
          players={players}
          selected={give}
          onToggle={(id) => toggle(give, setGive, id)}
          empty="No roster imported yet."
        />
      </Card>

      <Card title="You get">
        <select
          value={theirTeam}
          onChange={(event) =>
            pickTeam(event.target.value ? Number(event.target.value) : '')
          }
          style={{ marginBottom: 10 }}
          aria-label="Trade partner"
        >
          <option value="">Pick a team…</option>
          {teams.map((team) => (
            <option key={team.espn_team_id} value={team.espn_team_id}>
              {team.name}
            </option>
          ))}
        </select>
        <PlayerPicker
          players={theirRoster}
          selected={receive}
          onToggle={(id) => toggle(receive, setReceive, id)}
          empty="Pick a team to see their players."
        />
      </Card>

      <button
        className="btn primary block"
        onClick={analyse}
        disabled={busy || (give.size === 0 && receive.size === 0)}
      >
        {busy ? 'Analysing…' : 'Analyse trade'}
      </button>

      {result && (
        <div style={{ marginTop: 12 }}>
          <div className="call">
            <div className="eyebrow">Verdict</div>
            <div
              className="pname"
              style={{ color: VERDICT_COLOR[result.verdict], fontSize: 26 }}
            >
              {result.verdict.toUpperCase()}
            </div>
            <div className="small muted">{result.summary}</div>
            <ul className="reasons">
              {result.reasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          </div>

          <SideCard side={result.my_side} />
          {result.their_side && <SideCard side={result.their_side} />}
        </div>
      )}
    </>
  )
}
