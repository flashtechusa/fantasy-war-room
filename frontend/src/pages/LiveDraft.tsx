/**
 * Phase 5 -- Live Draft.
 *
 * Designed for one-handed use with the clock running: BEST PICK NOW is pinned
 * at the top, marking a player drafted is one tap from the search field, and
 * the whole model recalculates on every pick.
 */

import { useEffect, useRef, useState } from 'react'
import { api, type DraftState, type PlayerRow, type Recommendations } from '../api'
import { Banner, Card, Grade, Loading, PlayerDetail, Pos, Sheet, pct } from '../components'

export default function LiveDraft() {
  const [recs, setRecs] = useState<Recommendations | null>(null)
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [results, setResults] = useState<PlayerRow[]>([])
  const [detail, setDetail] = useState<PlayerRow | null>(null)
  const [autoSync, setAutoSync] = useState(false)
  const [syncNote, setSyncNote] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  async function refresh() {
    try {
      const [recommendations, state] = await Promise.all([api.recommendations(10), api.draft()])
      setRecs(recommendations)
      setDraft(state)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  // Pull the live ESPN draft as soon as the screen opens, and keep following it
  // if one is running. Without this, someone who opens Live Draft mid-draft --
  // e.g. after joining late -- sees a board frozen at pick 1 unless they happen
  // to know to turn "ESPN sync" on and wait for the next poll. The backfill adds
  // every pick already made, so a late arrival catches up in one call.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const result = await api.sync()
        if (cancelled) return
        if (result.synced && result.added) {
          setSyncNote(`Pulled ${result.added} pick(s) from ESPN`)
          await refresh()
        } else if (result.synced && !result.in_progress && !result.total_espn_picks) {
          setSyncNote('ESPN did not report a draft in progress yet. Manual entry works meanwhile.')
        } else if (!result.synced && result.reason) {
          setSyncNote(result.reason)
        }
        // Auto-follow only a draft ESPN says is live, so polling doesn't run
        // pointlessly before or long after the draft.
        if (result.in_progress) setAutoSync(true)
      } catch {
        // Best-effort: manual entry always works, so a failed backfill is silent.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Optional ESPN polling. The backend rate-limits this independently, so a
  // fast interval here can't turn into a fast request rate against ESPN.
  useEffect(() => {
    if (!autoSync) return
    const timer = setInterval(async () => {
      try {
        const result = await api.sync()
        if (result.synced && result.added) {
          setSyncNote(`Pulled ${result.added} pick(s) from ESPN`)
          await refresh()
        } else if (!result.synced && result.reason) {
          setSyncNote(result.reason)
        }
      } catch (err) {
        setSyncNote((err as Error).message)
      }
    }, 12000)
    return () => clearInterval(timer)
  }, [autoSync])

  useEffect(() => {
    const term = search.trim()
    if (term.length < 2) {
      setResults([])
      return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      api
        .board({ search: term, limit: 8 })
        .then((response) => !cancelled && setResults(response.players))
        .catch(() => !cancelled && setResults([]))
    }, 180)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [search])

  async function markDrafted(player: PlayerRow) {
    try {
      await api.pick(player.espn_player_id)
      setSearch('')
      setResults([])
      setDetail(null)
      searchRef.current?.blur()
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function undo() {
    try {
      await api.undo()
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function openDetail(player: PlayerRow) {
    try {
      setDetail(await api.player(player.espn_player_id))
    } catch (err) {
      setError((err as Error).message)
    }
  }

  if (loading) return <Loading what="the draft" />
  if (error && !recs) return <Banner kind="error">{error}</Banner>
  if (!recs || !draft) return <Banner kind="info">No draft data yet.</Banner>

  const position = recs.draft_position
  const best = recs.best_pick
  const alternatives = recs.top_picks.slice(1, showAll ? 10 : 5)
  const recent = [...draft.picks].sort((a, b) => b.overall_pick - a.overall_pick).slice(0, 8)

  return (
    <>
      {error && <Banner kind="error">{error}</Banner>}
      {syncNote && <Banner kind="info">{syncNote}</Banner>}

      {/* --- clock ---------------------------------------------------- */}
      <Card>
        <div className="row between">
          <div>
            <div className="tiny faint">ON THE CLOCK</div>
            <div style={{ fontSize: 19, fontWeight: 700 }}>
              Pick {position.current_pick}
              <span className="muted" style={{ fontWeight: 400, fontSize: 15 }}>
                {' '}
                · R{position.round}.{String(position.pick_in_round).padStart(2, '0')}
              </span>
            </div>
            <div className="small muted">
              {position.is_my_pick ? (
                <strong style={{ color: 'var(--elite)' }}>Your pick — you are up</strong>
              ) : (
                `Slot ${position.on_the_clock_slot} · you are slot ${position.my_slot}`
              )}
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="tiny faint">YOUR NEXT PICK</div>
            <div className="mono" style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.1 }}>
              {position.my_next_pick ?? '—'}
            </div>
            <div className="tiny muted">
              {position.picks_until_my_next !== null
                ? `${position.picks_until_my_next} picks away`
                : 'draft complete'}
            </div>
          </div>
        </div>
        <div className="tiny faint" style={{ marginTop: 8 }}>
          Upcoming: {position.my_upcoming_picks.join(' · ') || '—'}
        </div>
      </Card>

      {/* --- BEST PICK NOW -------------------------------------------- */}
      {best ? (
        <div className="best-pick">
          <div className="eyebrow">Best pick now</div>
          <div className="row between" style={{ alignItems: 'flex-start' }}>
            <div style={{ minWidth: 0 }}>
              <div className="pname">{best.name}</div>
              <div className="row wrap" style={{ gap: 6 }}>
                <Pos position={best.position} />
                <span className="small muted">{best.pro_team}</span>
                {best.bye_week && <span className="small faint">BYE {best.bye_week}</span>}
                <Grade grade={best.value_grade} />
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div className="big-score">{best.draft_score}</div>
              <div className="tiny faint">DRAFT SCORE</div>
            </div>
          </div>

          <div className="stat-strip" style={{ borderColor: 'rgba(255,255,255,0.12)' }}>
            <div className="cell">
              <span className="v">{best.projected_points}</span>
              <span className="l">Projected</span>
            </div>
            <div className="cell">
              <span className="v">{best.vor > 0 ? `+${best.vor}` : best.vor}</span>
              <span className="l">VOR</span>
            </div>
            <div className="cell">
              <span className="v">{best.adp ?? '—'}</span>
              <span className="l">ADP</span>
            </div>
            <div className="cell">
              <span className="v">{pct(best.gone_probability)}</span>
              <span className="l">Gone by next</span>
            </div>
          </div>

          {best.reasons && (
            <ul className="reasons">
              {best.reasons.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          )}

          <div className="row" style={{ gap: 8, marginTop: 12 }}>
            <button className="btn primary block" onClick={() => markDrafted(best)}>
              Draft {best.name.split(' ').slice(-1)[0]}
            </button>
            <button className="btn" onClick={() => openDetail(best)}>
              Details
            </button>
          </div>
        </div>
      ) : (
        <Banner kind="info">No players left on the board.</Banner>
      )}

      {/* --- mark a player drafted ------------------------------------ */}
      <Card title="Mark a player drafted">
        <input
          ref={searchRef}
          type="search"
          placeholder="Type a name…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search for a player to mark drafted"
        />
        {results.length > 0 && (
          <div className="player-list" style={{ marginTop: 8 }}>
            {results.map((player) => (
              <div className="player" key={player.espn_player_id} style={{ gridTemplateColumns: '1fr auto' }}>
                <div style={{ minWidth: 0 }}>
                  <div className="name">{player.name}</div>
                  <div className="meta">
                    <Pos position={player.position} />
                    <span>{player.pro_team}</span>
                    <span className="faint">ADP {player.adp ?? '—'}</span>
                  </div>
                </div>
                <button className="btn sm primary" onClick={() => markDrafted(player)}>
                  Drafted
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="row wrap" style={{ gap: 8, marginTop: 10 }}>
          <button className="btn sm" onClick={undo} disabled={draft.picks_made === 0}>
            Undo last ({draft.picks_made})
          </button>
          <button
            className={`btn sm ${autoSync ? 'primary' : ''}`}
            onClick={() => setAutoSync((value) => !value)}
            title="Poll ESPN for picks made in the real draft"
          >
            ESPN sync {autoSync ? 'on' : 'off'}
          </button>
        </div>
        <div className="tiny faint" style={{ marginTop: 6 }}>
          Manual entry always works. ESPN sync is optional and only adds picks you haven't
          already recorded.
        </div>
      </Card>

      {/* --- top 10 ---------------------------------------------------- */}
      <Card title={`Top picks right now`}>
        {alternatives.map((player, index) => (
          <details key={player.espn_player_id} style={{ marginBottom: 8 }}>
            <summary style={{ cursor: 'pointer', listStyle: 'none', minHeight: 44 }}>
              <div className="row between">
                <div className="row" style={{ minWidth: 0, gap: 8 }}>
                  <span className="mono faint" style={{ width: 18 }}>
                    {index + 2}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 650 }}>{player.name}</div>
                    <div className="meta tiny">
                      <Pos position={player.position} /> {player.pro_team} · VOR{' '}
                      {player.vor > 0 ? `+${player.vor}` : player.vor} · {pct(player.gone_probability)}{' '}
                      gone
                    </div>
                  </div>
                </div>
                <span className="mono" style={{ fontSize: 18, fontWeight: 700 }}>
                  {player.draft_score}
                </span>
              </div>
            </summary>
            <div className="small" style={{ padding: '6px 0 4px 26px' }}>
              <p style={{ margin: '0 0 5px' }}>
                <strong style={{ color: 'var(--elite)' }}>Take now:</strong> {player.why_now}
              </p>
              <p style={{ margin: '0 0 5px' }}>
                <strong style={{ color: 'var(--warn)' }}>Wait:</strong> {player.why_wait}
              </p>
              <p style={{ margin: '0 0 8px' }}>
                <strong style={{ color: 'var(--danger)' }}>Risk:</strong> {player.principal_risk}
              </p>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn sm primary" onClick={() => markDrafted(player)}>
                  Draft
                </button>
                <button className="btn sm" onClick={() => openDetail(player)}>
                  Details
                </button>
              </div>
            </div>
          </details>
        ))}
        {!showAll && recs.top_picks.length > 6 && (
          <button className="btn sm block" onClick={() => setShowAll(true)}>
            Show all 10
          </button>
        )}
      </Card>

      {/* --- position pressure ----------------------------------------- */}
      <Card title="Position pressure">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pos</th>
                <th className="num">Startable left</th>
                <th className="num">Lost by waiting</th>
                <th className="num">Cliff in</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(recs.meta.scarcity)
                .sort((a, b) => b.dropoff_to_next_pick - a.dropoff_to_next_pick)
                .map((row) => (
                  <tr key={row.position}>
                    <td>
                      <Pos position={row.position} />
                    </td>
                    <td className="num">{row.starters_left}</td>
                    <td className="num">{row.dropoff_to_next_pick}</td>
                    <td className="num">
                      {row.picks_to_cliff !== null ? `${row.picks_to_cliff} picks` : '—'}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* --- recent picks ---------------------------------------------- */}
      <Card title="Recent picks">
        {recent.length === 0 ? (
          <div className="small faint">No picks recorded yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Player</th>
                  <th>Pos</th>
                  <th>Slot</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((pick) => (
                  <tr key={pick.overall_pick}>
                    <td className="num faint">
                      {pick.round}.{String(pick.round_pick).padStart(2, '0')}
                    </td>
                    <td>
                      {pick.player_name}
                      {pick.is_mine && <span className="pill" style={{ marginLeft: 5 }}>YOU</span>}
                    </td>
                    <td>
                      <Pos position={pick.position} />
                    </td>
                    <td className="faint">{pick.draft_slot}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {detail && (
        <Sheet onClose={() => setDetail(null)}>
          <PlayerDetail player={detail} onDraft={markDrafted} onClose={() => setDetail(null)} />
        </Sheet>
      )}
    </>
  )
}
