/**
 * Phase 8 -- start/sit.
 *
 * The screen you open on Sunday morning. It leads with the decision (who is in
 * the lineup) and only then the reasoning, because that's the order you need
 * them in when you're about to lock rosters.
 */

import { useState } from 'react'
import { api } from '../api'
import { Banner, Card, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

export default function Week() {
  const [week, setWeek] = useState<number | undefined>(undefined)
  const lineup = useAsync(() => api.lineup(week), [week])

  if (lineup.loading && !lineup.data) return <Loading what="this week" />
  if (lineup.error) return <Banner kind="error">{lineup.error}</Banner>
  if (!lineup.data) return null

  const data = lineup.data
  const weeks = Array.from({ length: 18 }, (_, i) => i + 1)

  return (
    <>
      <Card>
        <div className="row between">
          <div>
            <div className="tiny faint">PROJECTED STARTERS</div>
            <div className="mono" style={{ fontSize: 32, fontWeight: 750, lineHeight: 1 }}>
              {data.projected_points}
            </div>
            {data.points_vs_naive > 0 && (
              <div className="tiny" style={{ color: 'var(--elite)', marginTop: 3 }}>
                +{data.points_vs_naive} vs starting your season-best players
              </div>
            )}
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
              {weeks.map((value) => (
                <option key={value} value={value}>
                  Week {value}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {data.projection_basis === 'season_average' && (
        <Banner kind="info">
          <strong>ESPN has not published week-{data.week} projections yet.</strong> Every
          number below is that player's season total divided evenly across the season, so
          future weeks will all look identical until ESPN publishes real weekly numbers —
          usually a week or two ahead. Start/sit calls this far out are not meaningful.
        </Banner>
      )}

      {data.projection_basis === 'mixed' && (
        <Banner kind="info">
          {data.estimated_count} of {data.roster_count} players have no published week-
          {data.week} projection and are shown as season averages. They are listed at the
          bottom.
        </Banner>
      )}

      {data.warnings.length > 0 && (
        <Banner kind="warn">
          <strong>Check these:</strong>
          <ul style={{ margin: '5px 0 0', paddingLeft: 18 }}>
            {data.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </Banner>
      )}

      <Card title="Start">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Slot</th>
                <th>Player</th>
                <th className="num">Proj</th>
              </tr>
            </thead>
            <tbody>
              {data.starters.map((entry, index) => (
                <tr key={`${entry.slot}-${index}`}>
                  <td className="faint">{entry.slot}</td>
                  <td style={{ whiteSpace: 'normal' }}>
                    {entry.player ? (
                      <>
                        <div style={{ fontWeight: 650 }}>
                          {entry.player.name}{' '}
                          {entry.warning && <span title={entry.warning}>⚠</span>}
                          {entry.close_call && (
                            <span className="pill" style={{ marginLeft: 4 }}>
                              CLOSE
                            </span>
                          )}
                        </div>
                        <div className="tiny faint">
                          {entry.player.pro_team} · {entry.reason}
                        </div>
                      </>
                    ) : (
                      <span style={{ color: 'var(--warn)' }}>— nobody eligible —</span>
                    )}
                  </td>
                  <td className="num" style={{ fontWeight: 700 }}>
                    {entry.player ? entry.player.week_points.toFixed(1) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {data.close_calls.length > 0 && (
        <Card title="Coin flips">
          <div className="small muted" style={{ marginBottom: 8 }}>
            These are inside the projections' margin of error — trust your read on the
            matchup over the number.
          </div>
          {data.close_calls.map((call, index) => (
            <div key={index} className="row between small" style={{ padding: '5px 0' }}>
              <span>
                <strong>{call.starting}</strong> over {call.over}
              </span>
              <span className="mono faint">{call.margin.toFixed(1)} pts</span>
            </div>
          ))}
        </Card>
      )}

      <Card title={`Sit (${data.bench.length})`}>
        {data.bench.length === 0 ? (
          <div className="small faint">Everyone is starting.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Pos</th>
                  <th className="num">Proj</th>
                </tr>
              </thead>
              <tbody>
                {data.bench.map((player) => (
                  <tr key={player.espn_player_id}>
                    <td>
                      {player.name}
                      {player.on_bye && <span className="faint tiny"> · BYE</span>}
                    </td>
                    <td>
                      <Pos position={player.position} />
                    </td>
                    <td className="num">{player.week_points.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data.estimated_projections.length > 0 && (
        <Card title="Estimated numbers">
          <div className="small muted">
            ESPN published no week-{data.week} projection for these players, so the
            season total was spread evenly across the season. Treat them as rough:
          </div>
          <div className="row wrap" style={{ gap: 5, marginTop: 8 }}>
            {data.estimated_projections.slice(0, 12).map((name) => (
              <span key={name} className="pill">
                {name}
              </span>
            ))}
          </div>
        </Card>
      )}
    </>
  )
}
