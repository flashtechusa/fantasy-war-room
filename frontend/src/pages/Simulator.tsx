/** Phase 7 -- draft simulator. */

import { useState } from 'react'
import { api, type SimulationResponse } from '../api'
import { Banner, Card, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

const VERDICT_COLOR: Record<string, string> = {
  wait: 'var(--elite)',
  balanced: 'var(--warn)',
  dangerous: 'var(--danger)',
}

const VERDICT_LABEL: Record<string, string> = {
  wait: 'SAFE TO WAIT',
  balanced: 'BALANCED',
  dangerous: 'DANGEROUS TO WAIT',
}

export default function Simulator() {
  const draft = useAsync(() => api.draft(), [])
  const [slot, setSlot] = useState<number | null>(null)
  const [count, setCount] = useState(1000)
  const [result, setResult] = useState<SimulationResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const teamCount = draft.data?.team_count ?? 12
  const activeSlot = slot ?? draft.data?.my_draft_slot ?? 1

  async function run() {
    setRunning(true)
    setError(null)
    try {
      setResult(await api.simulate({ my_slot: activeSlot, simulations: count }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <>
      <Card title="Run mock drafts">
        <div className="small muted" style={{ marginBottom: 10 }}>
          Opponents pick probabilistically around their ADP rather than taking the next ranked
          player, so reaches and position runs show up the way they do in a real room.
        </div>
        <div className="grid-2">
          <label>
            <div className="tiny faint" style={{ marginBottom: 4 }}>
              YOUR DRAFT SLOT
            </div>
            <select value={activeSlot} onChange={(event) => setSlot(Number(event.target.value))}>
              {Array.from({ length: teamCount }, (_, index) => index + 1).map((value) => (
                <option key={value} value={value}>
                  Slot {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            <div className="tiny faint" style={{ marginBottom: 4 }}>
              SIMULATIONS
            </div>
            <select value={count} onChange={(event) => setCount(Number(event.target.value))}>
              {[200, 500, 1000, 2500, 5000].map((value) => (
                <option key={value} value={value}>
                  {value.toLocaleString()}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button
          className="btn primary block"
          style={{ marginTop: 12 }}
          onClick={run}
          disabled={running}
        >
          {running ? 'Simulating…' : `Run ${count.toLocaleString()} mock drafts`}
        </button>
      </Card>

      {error && <Banner kind="error">{error}</Banner>}
      {running && <Loading what="simulations" />}

      {result && !running && (
        <>
          <Card title="Recommended strategy for this league">
            <ul className="reasons">
              {result.strategy.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
            <div className="tiny faint" style={{ marginTop: 10 }}>
              Derived from {result.simulations.toLocaleString()} simulated drafts from slot{' '}
              {result.my_slot} using your league's scoring, roster and ADP — not a preset.
            </div>
          </Card>

          <Card title="Expected final roster strength">
            <dl className="kv">
              <dt>Mean projected starter points</dt>
              <dd>{result.expected_roster_points.mean}</dd>
              <dt>10th percentile (bad draft)</dt>
              <dd>{result.expected_roster_points.p10}</dd>
              <dt>90th percentile (good draft)</dt>
              <dd>{result.expected_roster_points.p90}</dd>
            </dl>
            <div className="tiny faint" style={{ marginTop: 8 }}>
              Season totals for the optimal starting lineup from the roster the simulator built.
            </div>
          </Card>

          <Card title="Where waiting is safe, and where it isn't">
            {result.wait_profiles.map((profile) => {
              const max = Math.max(...profile.by_round, 1)
              return (
                <div key={profile.position} style={{ marginBottom: 14 }}>
                  <div className="row between" style={{ marginBottom: 5 }}>
                    <div className="row" style={{ gap: 7 }}>
                      <Pos position={profile.position} />
                      <span
                        className="tiny"
                        style={{ color: VERDICT_COLOR[profile.verdict], fontWeight: 700 }}
                      >
                        {VERDICT_LABEL[profile.verdict] ?? profile.verdict.toUpperCase()}
                      </span>
                    </div>
                    <span className="tiny faint">
                      {profile.cliff_round ? `cliff at round ${profile.cliff_round}` : 'no cliff'}
                    </span>
                  </div>
                  <div
                    className="spark"
                    role="img"
                    aria-label={`Expected best available ${profile.position} value by round: ${profile.by_round.join(', ')}`}
                  >
                    {profile.by_round.map((value, index) => (
                      <span
                        key={index}
                        style={{
                          height: `${Math.max((value / max) * 100, 3)}%`,
                          background:
                            profile.cliff_round && index + 1 >= profile.cliff_round
                              ? 'var(--reach)'
                              : 'var(--accent)',
                        }}
                      />
                    ))}
                  </div>
                  <div className="tiny muted" style={{ marginTop: 5 }}>
                    {profile.note}
                  </div>
                </div>
              )
            })}
          </Card>

          <Card title="What the simulator did each round">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="num">Rd</th>
                    <th className="num">Pick</th>
                    <th>Positions taken</th>
                    <th className="num">Avg VOR</th>
                  </tr>
                </thead>
                <tbody>
                  {result.picks.map((pick) => (
                    <tr key={pick.overall_pick}>
                      <td className="num faint">{pick.round}</td>
                      <td className="num">{pick.overall_pick}</td>
                      <td>
                        {Object.entries(pick.position_share)
                          .sort((a, b) => b[1] - a[1])
                          .slice(0, 3)
                          .map(([position, share]) => (
                            <span key={position} style={{ marginRight: 8 }}>
                              <Pos position={position} />{' '}
                              <span className="tiny faint">{Math.round(share * 100)}%</span>
                            </span>
                          ))}
                      </td>
                      <td className="num">{pick.mean_taken_vor}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Realistically available at your picks">
            {result.picks.slice(0, 6).map((pick) => (
              <div key={pick.overall_pick} style={{ marginBottom: 12 }}>
                <div className="tiny faint" style={{ marginBottom: 4 }}>
                  ROUND {pick.round} · PICK {pick.overall_pick}
                </div>
                {pick.likely_available.length === 0 ? (
                  <div className="small faint">
                    No player is available here in more than half of simulations.
                  </div>
                ) : (
                  <div className="row wrap" style={{ gap: 5 }}>
                    {pick.likely_available.slice(0, 8).map((player) => (
                      <span key={player.name} className="pill">
                        {player.name} · {player.position} · {Math.round(player.probability * 100)}%
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </Card>
        </>
      )}
    </>
  )
}
