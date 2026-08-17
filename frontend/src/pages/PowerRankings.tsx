/** Every team in the league, scored the same way and ranked. */

import { useState } from 'react'
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

/** Where a score sits relative to the league, in words. */
function standing(rank: number, total: number): string {
  if (total < 2) return ''
  const suffix = ['th', 'st', 'nd', 'rd'][rank % 10 > 3 || (rank % 100) - (rank % 10) === 10 ? 0 : rank % 10]
  return `${rank}${suffix} of ${total}`
}

export default function PowerRankings() {
  const league = useAsync(() => api.leagueTeams(), [])
  const [expanded, setExpanded] = useState<number | null>(null)

  if (league.loading) return <Loading what="the league" />
  if (league.error) return <Banner kind="error">{league.error}</Banner>
  if (!league.data) return <Banner kind="info">No league data.</Banner>

  const { teams, team_count, league_median_points, league_median_score, note } = league.data

  if (teams.length === 0) {
    return <Banner kind="info">{note}</Banner>
  }

  return (
    <>
      <Card>
        <div className="tiny faint">LEAGUE MEDIAN</div>
        <div className="row between" style={{ marginTop: 6 }}>
          <div>
            <div className="mono" style={{ fontSize: 26, fontWeight: 750, lineHeight: 1 }}>
              {league_median_points}
            </div>
            <div className="tiny muted">projected starter points</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="mono" style={{ fontSize: 26, fontWeight: 750, lineHeight: 1 }}>
              {league_median_score}
            </div>
            <div className="tiny muted">construction score</div>
          </div>
        </div>
        <div className="small faint" style={{ marginTop: 10, whiteSpace: 'normal' }}>
          {note}
        </div>
      </Card>

      <Card title={`All ${team_count} teams`}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Team</th>
                <th className="num">Proj</th>
                <th className="num">Build</th>
              </tr>
            </thead>
            <tbody>
              {teams.map((team) => (
                <tr
                  key={team.espn_team_id}
                  onClick={() => setExpanded(expanded === team.espn_team_id ? null : team.espn_team_id)}
                  style={{
                    cursor: 'pointer',
                    background: team.is_mine ? 'var(--row-highlight, rgba(255,255,255,0.05))' : undefined,
                  }}
                >
                  <td className="mono faint">{team.rank}</td>
                  <td>
                    <span style={{ fontWeight: team.is_mine ? 700 : 500 }}>{team.name}</span>
                    {team.is_mine && (
                      <span className="pill" style={{ marginLeft: 5, color: 'var(--accent)' }}>
                        YOU
                      </span>
                    )}
                    {team.weaknesses.length > 0 && (
                      <div className="tiny faint">weak: {team.weaknesses.join(', ')}</div>
                    )}
                  </td>
                  <td className="num mono">{team.projected_starter_points}</td>
                  <td className="num mono faint">
                    {team.roster_construction_score}
                    <div className="tiny faint">#{team.construction_rank}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="small faint" style={{ marginTop: 8 }}>
          Tap a team for detail.
        </div>
      </Card>

      {expanded !== null &&
        (() => {
          const team = teams.find((t) => t.espn_team_id === expanded)
          if (!team) return null
          return (
            <Card title={team.name}>
              <div className="row between">
                <div>
                  <div className="tiny faint">PROJECTED STARTERS</div>
                  <div className="mono" style={{ fontSize: 22, fontWeight: 700 }}>
                    {team.projected_starter_points}
                  </div>
                  <div className="tiny muted">{standing(team.rank, team_count)}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div className="tiny faint">ROSTER BUILD</div>
                  <div className="mono" style={{ fontSize: 22, fontWeight: 700 }}>
                    {team.roster_construction_score}
                  </div>
                  <div className="tiny muted">{standing(team.construction_rank, team_count)}</div>
                </div>
              </div>

              {team.owners.length > 0 && (
                <div className="small faint" style={{ marginTop: 8 }}>
                  {team.owners.join(', ')} · {team.roster_size} players
                </div>
              )}

              <div className="table-wrap" style={{ marginTop: 12 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Pos</th>
                      <th className="num">Theirs</th>
                      <th className="num">Lg avg</th>
                      <th className="num">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {team.positional_strength.map((row) => (
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
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {team.roster_construction_notes.length > 0 && (
                <ul className="reasons" style={{ marginTop: 10 }}>
                  {team.roster_construction_notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              )}
            </Card>
          )
        })()}
    </>
  )
}
