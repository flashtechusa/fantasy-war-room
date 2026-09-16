/**
 * Phase 8 -- start/sit.
 *
 * The screen you open on Sunday morning. It leads with the decision (who is in
 * the lineup) and only then the reasoning, because that's the order you need
 * them in when you're about to lock rosters.
 */

import { useState } from 'react'
import { api } from '../api'
import type { IrReturnResult, LineupApplyResult, LineupResponse, WeekPlayer } from '../api'
import { Banner, Card, EspnSyncLine, InjuryTag, Loading, Pos } from '../components'
import { useAsync } from '../useAsync'

/**
 * Where ESPN has this player right now, shown only when it differs from what we
 * recommend.
 *
 * Reported from real use, repeatedly: the Start table looks like a statement of
 * fact ("Bowers IS in my lineup") when it is advice ("Bowers SHOULD start"). So
 * when ESPN has him somewhere else, the row says so.
 */
function EspnSlot({ move }: { move?: { from_slot: string; to_slot: string } }) {
  if (!move) return null
  const from = move.from_slot.toUpperCase()
  const label = from === 'BE' ? 'on your bench' : from === 'IR' ? 'on IR' : `at ${from}`
  return (
    <span className="tiny" style={{ color: 'var(--warn, #d08a30)', marginLeft: 6 }}>
      · ESPN has him {label}
    </span>
  )
}

/**
 * Your lineup as ESPN has it right now, and the moves that would change it.
 *
 * Asked for directly: "shouldn't this show what ESPN shows, because how will
 * Auto Mode know what to change?" Auto Mode has always diffed against ESPN's
 * real slots -- that is what it writes -- but nothing on screen showed them, so
 * there was no way to check its arithmetic. Both sides are here now, and the
 * difference between them IS the write.
 */
function EspnLineup({
  data,
  onApplied,
}: {
  data: LineupResponse
  onApplied: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<LineupApplyResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const slots = data.current_slots
  if (!slots || Object.keys(slots).length === 0) return null

  // Everyone on the roster, wherever we happen to have listed them.
  const everyone: WeekPlayer[] = [
    ...data.starters.map((s) => s.player).filter((p): p is WeekPlayer => Boolean(p)),
    ...data.bench,
    ...(data.ir?.players ?? []),
  ]
  const seen = new Set<number>()
  const roster = everyone.filter((p) => {
    if (seen.has(p.espn_player_id)) return false
    seen.add(p.espn_player_id)
    return true
  })

  const current = (p: WeekPlayer) =>
    (slots[String(p.espn_player_id)] || 'BE').toUpperCase()

  const pending = new Map((data.pending_moves ?? []).map((m) => [m.espn_player_id, m]))
  const changes = roster.filter((p) => pending.has(p.espn_player_id))

  // ESPN's own slot order first (it is the league's), then the bench and IR.
  const order: string[] = []
  data.starters.forEach((entry) => {
    if (!order.includes(entry.slot.toUpperCase())) order.push(entry.slot.toUpperCase())
  })
  roster.forEach((p) => {
    const slot = current(p)
    if (slot !== 'BE' && slot !== 'IR' && !order.includes(slot)) order.push(slot)
  })
  order.push('BE', 'IR')

  const bySlot = order
    .map((slot) => ({ slot, players: roster.filter((p) => current(p) === slot) }))
    .filter((group) => group.players.length > 0)

  const startedTotal = roster
    .filter((p) => current(p) !== 'BE' && current(p) !== 'IR')
    .reduce((sum, p) => sum + p.week_points, 0)

  async function apply() {
    setBusy(true)
    setErr(null)
    try {
      setResult(await api.applyLineup(true))
      setConfirming(false)
      onApplied()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="On ESPN right now">
      <div className="small muted" style={{ marginBottom: 8 }}>
        Your actual lineup, as we last read it from ESPN — projected{' '}
        <strong>{startedTotal.toFixed(1)}</strong> from the players ESPN currently has
        starting.{' '}
        {changes.length === 0
          ? 'It already matches what we would set.'
          : `${changes.length} change${changes.length === 1 ? '' : 's'} would turn it into ours.`}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ESPN slot</th>
              <th>Player</th>
              <th className="num">Proj</th>
            </tr>
          </thead>
          <tbody>
            {bySlot.map((group) =>
              group.players.map((player, index) => (
                <tr key={`${group.slot}-${player.espn_player_id}`}>
                  <td className="faint">{index === 0 ? group.slot : ''}</td>
                  <td>
                    {player.name}
                    <InjuryTag status={player.injury_status} />
                    {pending.get(player.espn_player_id) && (
                      <span className="tiny" style={{ color: 'var(--warn, #d08a30)', marginLeft: 6 }}>
                        · we'd move him to {pending.get(player.espn_player_id)!.to_slot}
                      </span>
                    )}
                  </td>
                  <td className="num">{player.week_points.toFixed(1)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>

      {changes.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {!confirming ? (
            <button className="btn sm primary" disabled={busy} onClick={() => setConfirming(true)}>
              Apply our lineup to ESPN
            </button>
          ) : (
            <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="tiny">This writes {changes.length} move(s) to ESPN now.</span>
              <button className="btn sm primary" disabled={busy} onClick={apply}>
                {busy ? 'Applying…' : 'Confirm write'}
              </button>
              <button className="btn sm" disabled={busy} onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {err && <div style={{ marginTop: 8 }}><Banner kind="error">{err}</Banner></div>}
      {result && (
        <div style={{ marginTop: 8 }}>
          <Banner kind={result.ok ? 'info' : 'error'}>
            {!result.ok
              ? `ESPN did not accept it (HTTP ${result.status_code}).`
              : result.moves.length === 0
                ? 'ESPN already matched — no change needed.'
                : `Applied. ${result.moves.length} move(s).`}
            {result.moves.length > 0 && (
              <div className="tiny" style={{ marginTop: 4 }}>
                {result.moves.map((m) => `${m.name}: ${m.from_slot}→${m.to_slot}`).join(', ')}
              </div>
            )}
            {!result.ok && (
              <div className="tiny mono" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>
                {result.response}
              </div>
            )}
          </Banner>
        </div>
      )}
    </Card>
  )
}

/**
 * "Move to bench" for a player sitting in an IR slot.
 *
 * ESPN forces a healed player off IR and blocks every other roster move until he
 * is off it, so this has to be possible from here rather than only in the ESPN
 * app. Two shapes: with bench room it is one reversible lineup move; with a full
 * roster ESPN will not take him back until something is dropped, so the drop is
 * chosen by hand, named, and confirmed -- never picked for you.
 */
function IrReturnButton({
  player,
  onDone,
}: {
  player: WeekPlayer
  onDone: () => void
}) {
  const [plan, setPlan] = useState<IrReturnResult | null>(null)
  const [dropId, setDropId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState<IrReturnResult | null>(null)

  async function preview() {
    setBusy(true)
    setErr(null)
    try {
      setPlan(await api.irReturn({ espn_player_id: player.espn_player_id }))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function apply() {
    setBusy(true)
    setErr(null)
    try {
      const result = await api.irReturn({
        espn_player_id: player.espn_player_id,
        drop_id: dropId ?? undefined,
        confirm: true,
      })
      setDone(result)
      setPlan(null)
      onDone()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <Banner kind={done.ok ? 'info' : 'error'}>
        {done.ok
          ? `${done.player.name} is back on your bench${done.dropped ? `, ${done.dropped.name} dropped` : ''}.`
          : done.detail || `ESPN did not accept it (HTTP ${done.moved?.status_code ?? 0}).`}
        {!done.ok && (done.moved?.response || done.dropped?.response) && (
          <div className="tiny mono" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>
            {done.moved?.response || done.dropped?.response}
          </div>
        )}
      </Banner>
    )
  }

  if (!plan) {
    return (
      <>
        <button className="btn sm" disabled={busy} onClick={preview}>
          {busy ? 'Checking…' : 'Move to bench'}
        </button>
        {err && <div style={{ marginTop: 6 }}><Banner kind="error">{err}</Banner></div>}
      </>
    )
  }

  return (
    <div style={{ marginTop: 6 }}>
      {plan.needs_drop ? (
        <>
          <div className="small">
            Your roster is full, so ESPN won't take {plan.player.name} back until you
            drop someone. Pick who — <strong>this can't be undone</strong>:
          </div>
          <div className="row wrap" style={{ gap: 6, margin: '8px 0' }}>
            {plan.candidates.map((c) => (
              <button
                key={c.espn_player_id}
                className={`btn sm ${dropId === c.espn_player_id ? 'primary' : ''}`}
                onClick={() => setDropId(c.espn_player_id)}
              >
                {c.name} ({c.position}) · {c.projected_points.toFixed(1)}
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="small">
          This moves {plan.player.name} from IR to your bench on ESPN. You have{' '}
          {plan.bench_free} open spot{plan.bench_free === 1 ? '' : 's'}.
        </div>
      )}
      <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
        <button
          className="btn sm primary"
          disabled={busy || (plan.needs_drop && dropId === null)}
          onClick={apply}
        >
          {busy
            ? 'Working…'
            : plan.needs_drop
              ? 'Drop and move to bench'
              : 'Confirm — move to bench'}
        </button>
        <button className="btn sm" disabled={busy} onClick={() => { setPlan(null); setDropId(null) }}>
          Cancel
        </button>
      </div>
      {err && <div style={{ marginTop: 6 }}><Banner kind="error">{err}</Banner></div>}
    </div>
  )
}

export default function Week() {
  const [week, setWeek] = useState<number | undefined>(undefined)
  const lineup = useAsync(() => api.lineup(week), [week])

  if (lineup.loading && !lineup.data) return <Loading what="this week" />
  if (lineup.error) return <Banner kind="error">{lineup.error}</Banner>
  if (!lineup.data) return null

  const data = lineup.data
  const weeks = Array.from({ length: 18 }, (_, i) => i + 1)
  // The writes Apply would send, straight from the backend -- so a row only says
  // "ESPN has him elsewhere" when that would actually be changed.
  const pendingById = new Map(
    (data.pending_moves ?? []).map((m) => [m.espn_player_id, m]),
  )

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
        <div style={{ marginTop: 8 }}>
          <EspnSyncLine sync={data.espn_sync} onSynced={lineup.reload} />
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
          bottom. If that's most of your roster in a week that has already started,
          hit <strong>Refresh players</strong> on the League screen — ESPN only sends a
          week's projections when we ask for that week.
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
        <div className="small muted" style={{ marginBottom: 8 }}>
          This is the lineup we'd set — not necessarily what ESPN has right now.
          Where they differ, the row says so; the Auto tab applies it for you.
        </div>
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
                          <InjuryTag status={entry.player.injury_status} />
                          {entry.warning && <span title={entry.warning}>⚠</span>}
                          {entry.close_call && (
                            <span className="pill" style={{ marginLeft: 4 }}>
                              CLOSE
                            </span>
                          )}
                          <EspnSlot move={pendingById.get(entry.player.espn_player_id)} />
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

      <EspnLineup data={data} onApplied={lineup.reload} />

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
                      <InjuryTag status={player.injury_status} />
                      {player.on_bye && <span className="faint tiny"> · BYE</span>}
                      <EspnSlot move={pendingById.get(player.espn_player_id)} />
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

      {/* Injured reserve, shown like the bench: it is part of your roster, it is
          where a hurt player frees up an active spot, and a healed player left
          here blocks every other roster move you try to make. */}
      {data.ir && data.ir.slots > 0 && (
        <Card title={`Injured reserve (${data.ir.used}/${data.ir.slots})`}>
          {data.ir.must_return.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <Banner kind="error">
                <strong>
                  {data.ir.must_return.map((p) => p.name).join(', ')} no longer
                  {data.ir.must_return.length === 1 ? ' qualifies' : ' qualify'} for IR.
                </strong>
                <div className="tiny" style={{ marginTop: 4 }}>
                  ESPN blocks all of your other roster moves — waivers included —
                  until they're off IR.
                </div>
              </Banner>
            </div>
          )}
          {data.ir.players.length === 0 ? (
            <div className="small faint">
              {data.ir.slots === 1 ? 'Your IR spot is empty' : `All ${data.ir.slots} IR spots are empty`}.
              A player tagged Out can sit here without counting against your roster
              limit — that's a free spot for a pickup.
            </div>
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
                  {data.ir.players.map((player) => (
                    <tr key={player.espn_player_id}>
                      <td>
                        {player.name}
                        <InjuryTag status={player.injury_status} />
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
          {data.ir.players.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {data.ir.players.map((player) => (
                <div key={player.espn_player_id} style={{ marginBottom: 8 }}>
                  <div className="tiny faint" style={{ marginBottom: 2 }}>
                    {player.name}
                  </div>
                  <IrReturnButton player={player} onDone={lineup.reload} />
                </div>
              ))}
            </div>
          )}
          <div className="tiny faint" style={{ marginTop: 8 }}>
            {data.ir.open > 0
              ? `${data.ir.open} IR spot${data.ir.open === 1 ? '' : 's'} open. `
              : 'No IR room left. '}
            They don't count against your active roster, and they're not eligible to
            start.
          </div>
        </Card>
      )}

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
