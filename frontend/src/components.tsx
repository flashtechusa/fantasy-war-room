/** Shared presentational pieces. */

import type { ReactNode } from 'react'
import type { PlayerRow, ScoreComponent } from './api'

export function Pos({ position }: { position: string }) {
  return <span className={`pos ${position}`}>{position}</span>
}

export function Loading({ what = 'data' }: { what?: string }) {
  return <div className="loading">Loading {what}…</div>
}

export function Banner({
  kind = 'info',
  children,
}: {
  kind?: 'info' | 'warn' | 'error'
  children: ReactNode
}) {
  const icon = kind === 'error' ? '⚠' : kind === 'warn' ? '!' : 'i'
  return (
    <div className={`banner ${kind}`} role={kind === 'error' ? 'alert' : undefined}>
      <span aria-hidden="true">{icon}</span>
      <div>{children}</div>
    </div>
  )
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title && <h2>{title}</h2>}
      {children}
    </section>
  )
}

const GRADE_LABEL: Record<string, string> = {
  elite: 'ELITE VALUE',
  good: 'GOOD VALUE',
  fair: 'FAIR PRICE',
  reach: 'REACH',
}

/**
 * Value grade. Colour is never the only signal -- the text label carries the
 * same information for anyone who can't rely on hue.
 */
export function Grade({ grade }: { grade: string }) {
  return <span className={`grade ${grade}`}>{GRADE_LABEL[grade] ?? grade.toUpperCase()}</span>
}

export function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** Compact player card. Tapping opens the detail sheet. */
export function PlayerCard({
  player,
  onOpen,
  showStats = true,
  actionLabel,
  onAction,
}: {
  player: PlayerRow
  onOpen?: (player: PlayerRow) => void
  showStats?: boolean
  actionLabel?: string
  onAction?: (player: PlayerRow) => void
}) {
  const disappearing = player.flags.includes('disappearing')
  const injured = !['ACTIVE', 'NORMAL', ''].includes((player.injury_status || '').toUpperCase())

  return (
    <div className={`player ${player.value_grade} ${player.is_drafted ? 'drafted' : ''}`}>
      <button
        className="rank"
        onClick={() => onOpen?.(player)}
        aria-label={`Open details for ${player.name}`}
        style={{ all: 'unset', textAlign: 'center', cursor: 'pointer' }}
      >
        <span className="mono faint">{player.rank}</span>
      </button>

      <button
        onClick={() => onOpen?.(player)}
        style={{ all: 'unset', cursor: 'pointer', minWidth: 0 }}
      >
        <div className="name">
          {player.name}
          {disappearing && (
            <span title="Likely gone before your next pick" style={{ marginLeft: 5 }}>
              🔥
            </span>
          )}
          {injured && (
            <span title={`Injury: ${player.injury_status}`} style={{ marginLeft: 4 }}>
              ⚕
            </span>
          )}
        </div>
        <div className="meta">
          <Pos position={player.position} />
          <span>{player.pro_team}</span>
          {player.bye_week && <span className="faint">BYE {player.bye_week}</span>}
          <span className="faint">T{player.tier}</span>
          <Grade grade={player.value_grade} />
        </div>
      </button>

      <div className="score">
        {onAction ? (
          <button className="btn sm primary" onClick={() => onAction(player)}>
            {actionLabel ?? 'Draft'}
          </button>
        ) : (
          <>
            <div className="value">{player.draft_score}</div>
            <div className="label">Score</div>
          </>
        )}
      </div>

      {showStats && (
        <div className="stat-strip" style={{ gridColumn: '1 / -1' }}>
          <div className="cell">
            <span className="v">{player.projected_points}</span>
            <span className="l">Proj</span>
          </div>
          <div className="cell">
            <span className="v">{player.vor > 0 ? `+${player.vor}` : player.vor}</span>
            <span className="l">VOR</span>
          </div>
          <div className="cell">
            <span className="v">{player.adp ?? '—'}</span>
            <span className="l">ADP</span>
          </div>
          <div className="cell">
            <span className="v">{pct(player.gone_probability)}</span>
            <span className="l">Gone</span>
          </div>
        </div>
      )}
    </div>
  )
}

/** One weighted Draft Score component, with its bar and explanation. */
export function ComponentBar({ component }: { component: ScoreComponent }) {
  return (
    <div className="component">
      <div className="head">
        <span>{component.label}</span>
        <span className="mono muted">
          {component.value.toFixed(0)}
          <span className="faint"> × {(component.weight * 100).toFixed(0)}% = </span>
          <strong>{component.contribution.toFixed(1)}</strong>
        </span>
      </div>
      <div
        className="bar"
        role="meter"
        aria-valuenow={component.value}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={component.label}
      >
        <span style={{ width: `${Math.max(component.value, 1)}%` }} />
      </div>
      {component.detail && <div className="detail">{component.detail}</div>}
    </div>
  )
}

/** Bottom sheet on phones, centred modal on desktop. */
export function Sheet({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <div
      className="sheet"
      role="dialog"
      aria-modal="true"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="sheet-inner">
        <div className="sheet-grabber" />
        {children}
      </div>
    </div>
  )
}

/** Full player detail: every component, every reason, the scoring maths. */
export function PlayerDetail({
  player,
  onDraft,
  onClose,
}: {
  player: PlayerRow
  onDraft?: (player: PlayerRow) => void
  onClose: () => void
}) {
  return (
    <>
      <div className="row between" style={{ alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ fontSize: 21, margin: '0 0 5px' }}>{player.name}</h3>
          <div className="row wrap" style={{ gap: 6 }}>
            <Pos position={player.position} />
            <span className="small muted">{player.pro_team}</span>
            {player.bye_week && <span className="small faint">BYE {player.bye_week}</span>}
            <span className="small faint">
              {player.position}
              {player.position_rank} · Tier {player.tier}
            </span>
            <Grade grade={player.value_grade} />
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono" style={{ fontSize: 32, fontWeight: 700, lineHeight: 1 }}>
            {player.draft_score}
          </div>
          <div className="tiny faint">DRAFT SCORE</div>
        </div>
      </div>

      <div className="stat-strip" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        <div className="cell">
          <span className="v">{player.projected_points}</span>
          <span className="l">Projected</span>
        </div>
        <div className="cell">
          <span className="v">{player.vor > 0 ? `+${player.vor}` : player.vor}</span>
          <span className="l">VOR</span>
        </div>
        <div className="cell">
          <span className="v">{player.adp ?? '—'}</span>
          <span className="l">ADP</span>
        </div>
        <div className="cell">
          <span className="v">{pct(player.gone_probability)}</span>
          <span className="l">Gone next</span>
        </div>
      </div>

      {player.reasons && player.reasons.length > 0 && (
        <Card title="Why the engine likes him">
          <ul className="reasons">
            {player.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        </Card>
      )}

      {(player.why_now || player.why_wait || player.principal_risk) && (
        <Card title="Take now vs wait">
          <dl className="kv" style={{ gridTemplateColumns: '1fr', gap: 10 }}>
            {player.why_now && (
              <div>
                <dt className="tiny" style={{ color: 'var(--elite)', fontWeight: 700 }}>
                  WHY TAKE HIM NOW
                </dt>
                <dd style={{ textAlign: 'left', fontWeight: 400 }} className="small">
                  {player.why_now}
                </dd>
              </div>
            )}
            {player.why_wait && (
              <div>
                <dt className="tiny" style={{ color: 'var(--warn)', fontWeight: 700 }}>
                  WHY WAIT
                </dt>
                <dd style={{ textAlign: 'left', fontWeight: 400 }} className="small">
                  {player.why_wait}
                </dd>
              </div>
            )}
            {player.principal_risk && (
              <div>
                <dt className="tiny" style={{ color: 'var(--danger)', fontWeight: 700 }}>
                  PRINCIPAL RISK
                </dt>
                <dd style={{ textAlign: 'left', fontWeight: 400 }} className="small">
                  {player.principal_risk}
                </dd>
              </div>
            )}
          </dl>
        </Card>
      )}

      {player.components && (
        <Card title="Draft Score breakdown">
          {player.components.map((component) => (
            <ComponentBar key={component.key} component={component} />
          ))}
          <div className="tiny faint" style={{ marginTop: 8 }}>
            Each sub-score is 0-100 and is multiplied by its weight. Kickers, defenses and
            injured players get a documented final multiplier.
          </div>
        </Card>
      )}

      <Card title="Detail">
        <dl className="kv">
          <dt>Points per game</dt>
          <dd>{player.points_per_game}</dd>
          <dt>Replacement level ({player.position})</dt>
          <dd>{player.replacement_points}</dd>
          <dt>Upside / floor band</dt>
          <dd>
            {player.floor_points} – {player.upside_points}
          </dd>
          <dt>ESPN projection</dt>
          <dd>{player.espn_projected_points || '—'}</dd>
          <dt>ESPN rank</dt>
          <dd>{player.espn_rank ?? '—'}</dd>
          <dt>ADP vs our rank</dt>
          <dd>
            {player.adp_delta > 0 ? '+' : ''}
            {player.adp_delta}
          </dd>
          <dt>Opportunity cost elsewhere</dt>
          <dd>{player.opportunity_cost} pts</dd>
          <dt>Cost of waiting here</dt>
          <dd>{player.wait_cost} pts</dd>
          <dt>Rostered</dt>
          <dd>{player.percent_owned}%</dd>
          <dt>Projected games</dt>
          <dd>{player.projected_games}</dd>
          <dt>Injury status</dt>
          <dd>{player.injury_status}</dd>
        </dl>
      </Card>

      {player.source_projections && player.source_projections.length > 1 && (
        <Card title="What each source projects">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th className="num">Projected</th>
                  <th className="num">Used</th>
                </tr>
              </thead>
              <tbody>
                {player.source_projections.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    <td className="num mono">{row.points ?? '—'}</td>
                    <td className="num">
                      {row.counts_towards_blend ? (
                        <span style={{ color: 'var(--good)' }}>yes</span>
                      ) : (
                        <span className="faint">no</span>
                      )}
                    </td>
                  </tr>
                ))}
                <tr>
                  <td style={{ fontWeight: 700 }}>Used for rankings</td>
                  <td className="num mono" style={{ fontWeight: 700 }}>
                    {player.projected_points}
                  </td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
          <div className="tiny faint" style={{ marginTop: 8, whiteSpace: 'normal' }}>
            Every source is scored under your league's rules, so these are directly
            comparable. A source marked "no" is stored for comparison but does not
            affect rankings — usually because it covers too little of the player pool
            to blend fairly.
          </div>
        </Card>
      )}

      {player.scoring_breakdown && player.scoring_breakdown.length > 0 && (
        <Card title="How the projection was scored (your league's rules)">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Category</th>
                  <th className="num">Stat</th>
                  <th className="num">Per</th>
                  <th className="num">Points</th>
                </tr>
              </thead>
              <tbody>
                {player.scoring_breakdown.map((item) => (
                  <tr key={item.stat_id}>
                    <td>{item.label}</td>
                    <td className="num">{item.stat_value}</td>
                    <td className="num">{item.points_per}</td>
                    <td className="num">{item.points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="row" style={{ gap: 8, marginTop: 4 }}>
        {onDraft && !player.is_drafted && (
          <button className="btn primary block" onClick={() => onDraft(player)}>
            Mark drafted
          </button>
        )}
        <button className="btn block" onClick={onClose}>
          Close
        </button>
      </div>
    </>
  )
}
