import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { Loading } from './components'
import ConnectionSwitcher from './ConnectionSwitcher'
import { useAsync } from './useAsync'
import SignIn from './pages/SignIn'
import LeagueSettings from './pages/LeagueSettings'
import DraftBoard from './pages/DraftBoard'
import LiveDraft from './pages/LiveDraft'
import MyTeam from './pages/MyTeam'
import Simulator from './pages/Simulator'
import Week from './pages/Week'
import Waivers from './pages/Waivers'
import Trade from './pages/Trade'
import PowerRankings from './pages/PowerRankings'

// In-season nav. The draft tools (board, live draft, simulator) stay routable
// and are linked from League -- they matter one day a year, these matter every
// week.
const NAV = [
  { to: '/week', label: 'Week', icon: '📅' },
  { to: '/waivers', label: 'Waivers', icon: '🔍' },
  { to: '/team', label: 'My Team', icon: '🛡️' },
  { to: '/teams', label: 'Teams', icon: '🏈' },
  { to: '/trade', label: 'Trade', icon: '🔄' },
  { to: '/settings', label: 'League', icon: '⚙️' },
]

export default function App() {
  // Who is signed in, and which leagues they have connected. On a single-user
  // install this resolves to the implicit local account without a login
  // screen, so nothing about running it yourself changes.
  const auth = useAsync(() => api.me(), [])
  const health = useAsync(() => api.health(), [])

  function reloadAll() {
    auth.reload()
    health.reload()
  }

  if (!auth.data && auth.loading) {
    return (
      <div className="app">
        <main className="app-main">
          <Loading what="your account" />
        </main>
      </div>
    )
  }

  // 401 is the only error that means "sign in"; anything else is a real
  // failure and should not be papered over with a login form.
  if (!auth.data && auth.error) {
    if (/sign in/i.test(auth.error)) {
      return <SignIn onSignedIn={reloadAll} />
    }
    return (
      <div className="app">
        <main className="app-main">
          <div className="banner error">{auth.error}</div>
        </main>
      </div>
    )
  }

  const multiUser = auth.data?.multi_user ?? false
  const connections = auth.data?.connections ?? []

  return (
    <div className="app">
      <header className="app-header">
        <div style={{ minWidth: 0 }}>
          <h1>Fantasy War Room</h1>
          <div className="sub">
            {health.data?.league
              ? `${health.data.league.name} · ${health.data.league.season}`
              : connections.length
                ? 'No league imported'
                : 'No league connected'}
            {health.data?.league?.source === 'demo' && ' · DEMO DATA'}
          </div>
        </div>
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <ConnectionSwitcher
            connections={connections}
            activeId={auth.data?.active_connection_id ?? null}
            onSwitched={reloadAll}
          />
          {multiUser && (
            <button
              className="btn sm"
              onClick={async () => {
                await api.logout()
                reloadAll()
              }}
            >
              Sign out
            </button>
          )}
        </div>
      </header>

      <nav className="app-nav" aria-label="Primary">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'active' : '')}>
            <span className="icon" aria-hidden="true">
              {item.icon}
            </span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<Navigate to="/week" replace />} />
          <Route path="/week" element={<Week />} />
          <Route path="/waivers" element={<Waivers />} />
          <Route path="/trade" element={<Trade />} />
          <Route path="/live" element={<LiveDraft />} />
          <Route path="/board" element={<DraftBoard />} />
          <Route path="/team" element={<MyTeam />} />
          <Route path="/teams" element={<PowerRankings />} />
          <Route path="/simulate" element={<Simulator />} />
          <Route path="/settings" element={<LeagueSettings onChange={reloadAll} />} />
          <Route path="*" element={<Navigate to="/week" replace />} />
        </Routes>
      </main>
    </div>
  )
}
