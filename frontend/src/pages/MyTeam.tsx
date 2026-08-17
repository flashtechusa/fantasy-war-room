/** Phase 6 -- My Team. */

import { api } from '../api'
import { Banner, Card, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

const GRADE_COLOR: Record<string, string> = {
  elite: 'var(--elite)',
  strong: 'var(--good)',
  average: 'var(--fair)',
  weak: 'var(--reach)',
  critical: 'var(--danger)',
}

export default function MyTeam() {
  const team = useAsync(() => api.team(), [])

  if (team.loading) return <Loading what="your team" />
  if (team.error) return <Banner kind="error">{team.error}</Banner>
  if (!team.data) return <Banner kind="info">No team data.</Banner>

  const data = team.data
  const lineup = data.lineup

  return (
    <>
      <Card>
        <div className="row between">
          <div>
            <div className="tiny faint">ROSTER CONSTRUCTION</div>
            <div className="mono" style={{ fontSize: 34, fontWeight: 750, lineHeight: 1 }}>
              {data.roster_construction_score}
              <span className="faint" style={{ fontSize: 15 }}>
                /100
              </span>
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="tiny faint">PROJECTED STARTERS</div>
            <div className="mono" style={{ fontSize: 22, fontWeight: 700 }}>
              {lineup.total_points}
            </div>
            <div className="tiny muted">{lineup.weekly_points} / week</div>
          </div>
        </div>
        <ul className="reasons" style={{ marginTop: 10 }}>
          {data.roster_construction_notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      </Card>

      {data.picks_made === 0 && (
        <Banner kind="info">
          {data.my_team_identified
            ? "Your team has no players on it yet."
            : "No team is marked as yours. Pick it on the League tab and this screen fills in."}
        </Banner>
      )}

      {data.highest_marginal_value && (
        <Card title="Highest marginal value for your next pick">
          <div className="row" style={{ gap: 10 }}>
            <Pos position={data.highest_marginal_value.position} />
            <div>
              <div style={{ fontWeight: 650 }}>
                +{data.highest_marginal_value.marginal_value} pts to your starting lineup
              </div>
              <div className="small muted">{data.highest_marginal_value.note}</div>
            </div>
          </div>
        </Card>
      )}

      <Card title="Starting lineup">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Slot</th>
                <th>Player</th>
                <th className="num">Proj</th>
                <th className="num">VOR</th>
                <th className="num">Bye</th>
              </tr>
            </thead>
            <tbody>
              {lineup.starters.map((entry, index) => (
                <tr key={`${entry.slot}-${index}`}>
                  <td className="faint">{entry.slot}</td>
                  <td>
                    {entry.player ? (
                      <>
                        {entry.player.name}{' '}
                        <span className="faint tiny">{entry.player.pro_team}</span>
                      </>
                    ) : (
                      <span style={{ color: 'var(--warn)' }}>— empty —</span>
                    )}
                  </td>
                  <td className="num">{entry.player?.projected_points ?? '—'}</td>
                  <td className="num">{entry.player ? entry.player.vor : '—'}</td>
                  <td className="num faint">{entry.player?.bye_week ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={`Bench (${lineup.bench.length})`}>
        {lineup.bench.length === 0 ? (
          <div className="small faint">Nobody on the bench yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Pos</th>
                  <th className="num">Proj</th>
                  <th className="num">VOR</th>
                </tr>
              </thead>
              <tbody>
                {lineup.bench.map((player) => (
                  <tr key={player.espn_player_id}>
                    <td>{player.name}</td>
                    <td>
                      <Pos position={player.position} />
                    </td>
                    <td className="num">{player.projected_points}</td>
                    <td className="num">{player.vor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Positional strength vs the league">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pos</th>
                <th className="num">Yours</th>
                <th className="num">League avg</th>
                <th className="num">Edge</th>
                <th>Grade</th>
              </tr>
            </thead>
            <tbody>
              {data.positional_strength.map((row) => (
                <tr key={row.position}>
                  <td>
                    <Pos position={row.position} />
                  </td>
                  <td className="num">{row.starters_points}</td>
                  <td className="num faint">{row.league_average_points}</td>
                  <td className="num" style={{ color: GRADE_COLOR[row.grade] }}>
                    {row.edge > 0 ? '+' : ''}
                    {row.edge}
                  </td>
                  <td style={{ color: GRADE_COLOR[row.grade], fontWeight: 650 }}>
                    {row.grade.toUpperCase()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={data.roster_source === 'espn' ? 'Where an upgrade helps most' : 'Remaining draft needs'}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pos</th>
                <th className="num">Starters</th>
                <th className="num">Marginal value</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {data.remaining_needs.map((need) => (
                <tr key={need.position}>
                  <td>
                    <Pos position={need.position} />
                    {need.urgent && (
                      <span className="pill" style={{ marginLeft: 5, color: 'var(--danger)' }}>
                        URGENT
                      </span>
                    )}
                  </td>
                  <td className="num">
                    {need.starters_filled}/{need.starters_required}
                  </td>
                  <td className="num">+{need.marginal_value}</td>
                  <td className="small faint" style={{ whiteSpace: 'normal' }}>
                    {need.note}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Bye-week conflicts">
        {data.bye_conflicts.length === 0 ? (
          <div className="small faint">No starters share a bye week.</div>
        ) : (
          data.bye_conflicts.map((conflict) => (
            <div key={conflict.week} style={{ marginBottom: 8 }}>
              <div className="row" style={{ gap: 8 }}>
                <span
                  className="pill"
                  style={{
                    color: conflict.severity === 'high' ? 'var(--danger)' : 'var(--warn)',
                  }}
                >
                  WEEK {conflict.week} · {conflict.severity.toUpperCase()}
                </span>
              </div>
              <div className="small muted" style={{ marginTop: 3 }}>
                {conflict.players.join(', ')}
              </div>
            </div>
          ))
        )}
      </Card>
    </>
  )
}
