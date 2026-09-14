/**
 * Switching between connected leagues.
 *
 * Someone with a league on ESPN and another on Yahoo holds two connections,
 * and every screen in the app renders whichever one is active -- so this
 * control sits in the header rather than buried in settings. Switching drops
 * the cached board and reloads, because a board built for one league says
 * nothing true about another.
 */

import { api, type ConnectionInfo } from './api'

const PLATFORM_LABEL: Record<string, string> = {
  espn: 'ESPN',
  yahoo: 'Yahoo',
  demo: 'Demo',
}

export default function ConnectionSwitcher({
  connections,
  activeId,
  busy,
  onSwitched,
}: {
  connections: ConnectionInfo[]
  activeId: number | null
  busy?: boolean
  onSwitched: () => void
}) {
  // One league is the normal case, and a picker with a single option is just
  // noise -- the header already names the league.
  if (connections.length < 2) return null

  async function switchTo(id: number) {
    if (!id || id === activeId) return
    await api.activateConnection(id)
    onSwitched()
  }

  return (
    <select
      aria-label="Which league"
      className="connection-switcher"
      value={activeId ?? ''}
      disabled={busy}
      onChange={(event) => switchTo(Number(event.target.value))}
    >
      {connections.map((connection) => (
        <option key={connection.id} value={connection.id}>
          {PLATFORM_LABEL[connection.platform] ?? connection.platform} · {connection.label}
        </option>
      ))}
    </select>
  )
}
