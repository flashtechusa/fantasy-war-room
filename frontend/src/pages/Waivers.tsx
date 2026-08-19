/** Phase 8 -- waiver wire. */

import { useState } from 'react'
import { api } from '../api'
import { Banner, Card, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

const VERDICT_COLOR: Record<string, string> = {
  'must-add': 'var(--elite)',
  starter: 'var(--good)',
  streamer: 'var(--warn)',
  stash: 'var(--accent)',
  depth: 'var(--fair)',
}

export default function Waivers() {
  const [week, setWeek] = useState<number | undefined>(undefined)
  const [refreshing, setRefreshing] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const waivers = useAsync(() => api.waivers(week, 15), [week])

  async function refresh() {
    setRefreshing(true)
    setNote(null)
    try {
      const result = await api.refreshWaivers(week)
      setNote(`Pulled ${result.free_agents_imported} free agents from ESPN.`)
      waivers.reload()
    } catch (error) {
      setNote((error as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  if (waivers.loading && !waivers.data) return <Loading what="the wire" />
  if (waivers.error) return <Banner kind="error">{waivers.error}</Banner>
  if (!waivers.data) return null

  const data = waivers.data

  return (
    <>
      {note && <Banner kind="info">{note}</Banner>}

      <Card>
        <div className="row between">
          <div className="small">
            <div className="muted">
              {data.free_agents_considered} considered
              {data.free_agents_available
                ? ` of ${data.free_agents_available} free agents`
                : ' free agents'}
            </div>
            <div className="tiny faint">
              Roster {data.roster_size} · {data.roster_is_full ? 'full' : 'has space'}
              {data.uses_faab && ` · $${data.faab_budget} FAAB`}
            </div>
          </div>
          <label style={{ width: 110 }}>
            <div className="tiny faint" style={{ marginBottom: 4 }}>
              WEEK
            </div>
            <select
              value={data.week}
              onChange={(event) => setWeek(Number(event.target.value))}
              aria-label="Week"
            >
              {Array.from({ length: 18 }, (_, i) => i + 1).map((value) => (
                <option key={value} value={value}>
                  Week {value}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button
          className="btn sm block"
          style={{ marginTop: 10 }}
          onClick={refresh}
          disabled={refreshing}
        >
          {refreshing ? 'Refreshing…' : 'Refresh free agents from ESPN'}
        </button>
      </Card>

      {/* A wire that returns one position reads as broken. It usually isn't --
          it means everything else you start already beats what is out there --
          but that has to be said, not left to be inferred from an empty list.

          Not a table: the useful part is a sentence, and a sentence in a table
          cell on a phone either overflows the screen or gets truncated. Rows
          that stack and wrap say the same thing and fit. */}
      {data.by_position && data.by_position.length > 0 && (
        <Card title="Every position, and why">
          <ul className="wire-verdicts">
            {data.by_position.map((row) => (
              <li key={row.position} className={row.helps ? 'helps' : undefined}>
                <div className="row between" style={{ gap: 8 }}>
                  <div className="row" style={{ gap: 7, minWidth: 0 }}>
                    <span className={`pos ${row.position}`}>{row.position}</span>
                    <span className="small">{row.best_name}</span>
                    <span className="tiny faint">{row.best_points.toFixed(0)} pts</span>
                  </div>
                  <span className="tiny faint" style={{ flexShrink: 0 }}>
                    {row.considered} free
                  </span>
                </div>
                <div className={`tiny ${row.helps ? 'good' : 'faint'}`}>
                  {row.helps ? 'Upgrade — see below' : row.note}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data.targets.length === 0 ? (
        <Banner kind="info">
          Nothing on the wire improves your lineup right now. Try refreshing, or check a
          different week.
        </Banner>
      ) : (
        data.targets.map((target, index) => (
          <div className="card" key={target.player.espn_player_id}>
            <div className="row between" style={{ alignItems: 'flex-start' }}>
              <div style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 7 }}>
                  <span className="mono faint">{index + 1}</span>
                  <span style={{ fontWeight: 700, fontSize: 16 }}>{target.player.name}</span>
                </div>
                <div className="row wrap" style={{ gap: 6, marginTop: 4 }}>
                  <Pos position={target.player.position} />
                  <span className="small muted">{target.player.pro_team}</span>
                  <span
                    className="tiny"
                    style={{ color: VERDICT_COLOR[target.verdict], fontWeight: 800 }}
                  >
                    {target.verdict.toUpperCase()}
                  </span>
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="mono" style={{ fontSize: 22, fontWeight: 750, lineHeight: 1 }}>
                  {target.priority}
                </div>
                <div className="tiny faint">PRIORITY</div>
              </div>
            </div>

            <div className="stat-strip">
              <div className="cell">
                <span className="v">
                  {target.week_gain > 0 ? `+${target.week_gain.toFixed(1)}` : '—'}
                </span>
                <span className="l">This week</span>
              </div>
              <div className="cell">
                <span className="v">
                  {target.season_gain > 0 ? `+${target.season_gain.toFixed(0)}` : '—'}
                </span>
                <span className="l">Rest of yr</span>
              </div>
              <div className="cell">
                <span className="v">{target.player.percent_owned.toFixed(0)}%</span>
                <span className="l">Rostered</span>
              </div>
              <div className="cell">
                <span className="v">{data.uses_faab ? `$${target.faab_bid}` : '—'}</span>
                <span className="l">Bid</span>
              </div>
            </div>

            <ul className="reasons">
              {target.reasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          </div>
        ))
      )}
    </>
  )
}
