/** Phase 4 -- the Draft Board. */

import { useEffect, useMemo, useState } from 'react'
import { api, type PlayerRow } from '../api'
import { Banner, Card, Loading, PlayerCard, PlayerDetail, Sheet } from '../components'
import { useAsync } from '../useAsync'

const FILTERS = ['ALL', 'QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DST']
const PAGE_SIZE = 60

export default function DraftBoard() {
  const [filter, setFilter] = useState('ALL')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [selected, setSelected] = useState<PlayerRow | null>(null)
  const [detail, setDetail] = useState<PlayerRow | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 220)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => setLimit(PAGE_SIZE), [filter, debounced])

  const board = useAsync(
    () => api.board({ position: filter, search: debounced || undefined, limit }),
    [filter, debounced, limit],
  )

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    api
      .player(selected.espn_player_id)
      .then((full) => !cancelled && setDetail(full))
      .catch((err: Error) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [selected])

  async function draftPlayer(player: PlayerRow) {
    try {
      await api.pick(player.espn_player_id)
      setSelected(null)
      setDetail(null)
      board.reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const meta = board.data?.meta
  const legend = useMemo(
    () => [
      { grade: 'elite', text: 'Elite value — falling well past our ranking' },
      { grade: 'good', text: 'Good value' },
      { grade: 'fair', text: 'Fair price — near ADP' },
      { grade: 'reach', text: 'Reach — costs more than he is worth here' },
    ],
    [],
  )

  return (
    <>
      <div className="filters">
        <input
          type="search"
          placeholder="Search player or NFL team…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search players"
          style={{ marginBottom: 8 }}
        />
        <div className="chips" role="tablist" aria-label="Position filter">
          {FILTERS.map((option) => (
            <button
              key={option}
              role="tab"
              aria-selected={filter === option}
              className={`chip ${filter === option ? 'active' : ''}`}
              onClick={() => setFilter(option)}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {board.error && <Banner kind="error">{board.error}</Banner>}

      {meta && (
        <Card>
          <div className="row between small">
            <span className="muted">{meta.scoring_format}</span>
            <span className="faint">
              Pick {meta.current_pick}
              {meta.next_pick ? ` · next at ${meta.next_pick}` : ''}
            </span>
          </div>
          <div className="row between tiny faint" style={{ marginTop: 3 }}>
            <span>{meta.league_shape}</span>
            <span>{board.data?.total ?? 0} available</span>
          </div>
          {meta.market_drift !== 0 && (
            <div className="tiny faint" style={{ marginTop: 4 }}>
              Market drift {meta.market_drift > 0 ? '+' : ''}
              {meta.market_drift} picks — the room is drafting{' '}
              {meta.market_drift > 0 ? 'ahead of' : 'behind'} ADP.
            </div>
          )}
        </Card>
      )}

      {board.loading && !board.data ? (
        <Loading what="the board" />
      ) : (
        <>
          <div className="player-list">
            {board.data?.players.map((player) => (
              <PlayerCard
                key={player.espn_player_id}
                player={player}
                onOpen={setSelected}
              />
            ))}
          </div>

          {board.data && board.data.total > board.data.players.length && (
            <button
              className="btn block"
              style={{ marginTop: 12 }}
              onClick={() => setLimit((value) => value + PAGE_SIZE)}
            >
              Load more ({board.data.total - board.data.players.length} remaining)
            </button>
          )}

          {board.data?.players.length === 0 && (
            <Banner kind="info">No players match that filter.</Banner>
          )}
        </>
      )}

      <Card title="Legend">
        <ul style={{ margin: 0, paddingLeft: 18 }} className="small">
          {legend.map((item) => (
            <li key={item.grade} style={{ marginBottom: 3 }}>
              <span className={`grade ${item.grade}`}>■</span> {item.text}
            </li>
          ))}
          <li style={{ marginBottom: 3 }}>🔥 Likely gone before your next pick</li>
          <li>⚕ Carrying an injury designation</li>
        </ul>
      </Card>

      {selected && (
        <Sheet
          onClose={() => {
            setSelected(null)
            setDetail(null)
          }}
        >
          {detail ? (
            <PlayerDetail
              player={detail}
              onDraft={draftPlayer}
              onClose={() => {
                setSelected(null)
                setDetail(null)
              }}
            />
          ) : (
            <Loading what={selected.name} />
          )}
        </Sheet>
      )}
    </>
  )
}
