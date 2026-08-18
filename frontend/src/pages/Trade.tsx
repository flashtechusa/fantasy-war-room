/** Phase 8 -- trade analyzer. */

import { useState } from 'react'
import { api, type TradeResponse, type WeekPlayer } from '../api'
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

  if (roster.loading) return <Loading what="your roster" />
  if (roster.error) return <Banner kind="error">{roster.error}</Banner>

  const players = roster.data?.players ?? []
  const teams = (roster.data?.teams ?? []).filter((t) => !t.is_mine)

  return (
    <>
      {error && <Banner kind="error">{error}</Banner>}

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
