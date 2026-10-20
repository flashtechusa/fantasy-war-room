import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { useAsync } from './useAsync'
import LeagueSettings from './pages/LeagueSettings'
import DraftBoard from './pages/DraftBoard'
import LiveDraft from './pages/LiveDraft'
import MyTeam from './pages/MyTeam'
import Simulator from './pages/Simulator'
import Week from './pages/Week'
import Waivers from './pages/Waivers'
import Trade from './pages/Trade'
import PowerRankings from './pages/PowerRankings'
import ConnectEspn from './pages/ConnectEspn'
import DraftDiagnostics from './pages/DraftDiagnostics'
import Landing from './pages/Landing'
import AdminConsole from './pages/AdminConsole'
import Guide from './pages/Guide'
import Auto from './pages/Auto'

// In-season nav. The draft tools (board, live draft, simulator) stay routable
// and are linked from League -- they matter one day a year, these matter every
// week.
const NAV = [
  { to: '/week', label: 'Week', icon: '📅' },
  { to: '/waivers', label: 'Waivers', icon: '🔍' },
  { to: '/team', label: 'My Team', icon: '🛡️' },
  { to: '/teams', label: 'Teams', icon: '🏈' },
  { to: '/trade', label: 'Trade', icon: '🔄' },
  { to: '/auto', label: 'Auto', icon: '🤖' },
  { to: '/settings', label: 'League', icon: '⚙️' },
  { to: '/help', label: 'Help', icon: '❓' },
]

/** The bundle THIS browser is running, read from the script tag that loaded it. */
function runningBundle(): string {
  try {
    const tag = document.querySelector<HTMLScriptElement>(
      'script[type="module"][src*="/assets/index-"]',
    )
    const match = /index-([A-Za-z0-9_-]+)\.js/.exec(tag?.src || import.meta.url)
    return match ? match[1] : ''
  } catch {
    return ''
  }
}

/**
 * Tell the user when their browser is running an old build.
 *
 * A cached index.html keeps pointing at the bundle it shipped with, so a phone
 * can sit on a version from days ago while the header -- which asks the SERVER
 * what it holds -- reports the current one. The deploy is fine; the HTML is
 * stale, and nothing said so. A shipped feature just was not there.
 *
 * index.html is served no-store now, so this should stop happening. It is still
 * worth saying when it does: the reload carries a cache-busting query because a
 * browser that has already cached the HTML will otherwise serve it right back.
 */
function StaleBundleNotice({ serverBundle }: { serverBundle?: string }) {
  const mine = runningBundle()
  if (!serverBundle || !mine) return null
  const theirs = /index-([A-Za-z0-9_-]+)\.js/.exec(serverBundle)?.[1] || ''
  if (!theirs || theirs === mine) return null

  return (
    <button
      className="btn sm primary"
      style={{ flexShrink: 0 }}
      title={`Running ${mine}, server has ${theirs}`}
      onClick={() => {
        window.location.href =
          window.location.pathname + '?v=' + Date.now() + window.location.hash
      }}
    >
      Update ready — reload
    </button>
  )
}

export default function App() {
  const auth = useAsync(() => api.me(), [])
  const health = useAsync(() => api.health(), [])

  // The admin console is its own entrance, before the app's auth gate: signing
  // out of a team must not sign you out of administration, and reaching /admin
  // must not bounce you to the marketing page.
  if (window.location.pathname.startsWith('/admin')) {
    return <AdminConsole />
  }

  // Nothing is rendered until we know who is asking. Flashing the app and then
  // replacing it with a landing page reads as a bug, and briefly shows the
  // shape of someone's team to a signed-out browser.
  if (auth.loading) return null

  if (!auth.data?.authenticated) {
    return (
      <Landing
        onSignedIn={() => {
          auth.reload()
          health.reload()
        }}
      />
    )
  }

  const hasLeague = Boolean(health.data?.league_imported)
  const landing = hasLeague ? '/week' : '/settings'

  async function signOut() {
    try {
      await api.logout()
    } finally {
      auth.reload()
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div style={{ minWidth: 0 }}>
          <h1>Fantasy War Room</h1>
          <div className="sub">
            {health.data?.league
              ? `${health.data.league.name} · ${health.data.league.season}`
              : 'No league imported'}
            {health.data?.league?.source === 'demo' && ' · DEMO DATA'}
            {/* Which build is actually serving you. The VPS installs from a
                zip, so there is no git checkout to ask, and "did the update
                land?" was otherwise unanswerable from a phone.

                Two values, not one: what the SERVER holds, and what THIS browser
                is running. They came apart in real use -- a cached index.html
                kept a phone on a bundle two versions old while the header
                faithfully reported the server's current one, so a shipped
                feature was simply absent with nothing to explain it. */}
            {health.data?.build?.bundle && (
              <span className="faint"> · {health.data.build.bundle.slice(6, 14)}</span>
            )}
            {/* And which COMMIT. The bundle hash only changes when the frontend
                does, so a backend-only deploy -- a scoring fix, a waiver rule --
                left the fingerprint identical and there was no way to tell
                whether it had landed. That happened: a user watched an unchanged
                fingerprint and reasonably concluded the fix had not shipped. */}
            {health.data?.build?.commit && (
              <span className="faint"> · {health.data.build.commit}</span>
            )}
          </div>
        </div>
        <StaleBundleNotice serverBundle={health.data?.build?.bundle} />
        <div className="row" style={{ gap: 10, flexShrink: 0, alignItems: 'center' }}>
          <div style={{ textAlign: 'right', minWidth: 0 }}>
            <div className="small" style={{ fontWeight: 650 }}>
              {auth.data.user?.display_name || auth.data.user?.username}
            </div>
            <div className="tiny faint">{auth.data.user?.role}</div>
          </div>
          <button className="btn sm" onClick={signOut} aria-label="Sign out">
            Sign out
          </button>
        </div>
      </header>

      <nav className="app-nav" aria-label="Primary">
        {/* The diagnostics screen is only reachable when it is switched on,
            and a route with no link is a route nobody finds. */}
        {(health.data?.debug_screens
          ? [...NAV, { to: '/diagnostics', label: 'Sync', icon: '📡' }]
          : NAV
        ).map((item) => (
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
          {/* Somebody with no league of their own has nothing to show on the
              weekly screens, so send them where they can connect one rather
              than to an error. */}
          <Route path="/" element={<Navigate to={landing} replace />} />
          <Route path="/week" element={<Week />} />
          <Route path="/waivers" element={<Waivers />} />
          <Route path="/trade" element={<Trade />} />
          <Route path="/live" element={<LiveDraft />} />
          <Route path="/board" element={<DraftBoard />} />
          <Route path="/team" element={<MyTeam />} />
          <Route path="/teams" element={<PowerRankings />} />
          <Route path="/connect" element={<ConnectEspn onChange={health.reload} />} />
          <Route path="/help" element={<Guide />} />
          <Route path="/auto" element={<Auto />} />
          {/* Debug screen. Routable only while FWR_DEBUG_SCREENS is on -- it is
              a testing tool, and one more thing to explain otherwise. */}
          {health.data?.debug_screens && (
            <Route path="/diagnostics" element={<DraftDiagnostics />} />
          )}
          <Route path="/simulate" element={<Simulator />} />
          <Route
            path="/settings"
            element={
              <LeagueSettings
                onChange={health.reload}
                role={auth.data?.user?.role ?? "client"}
              />
            }
          />
          <Route path="*" element={<Navigate to={landing} replace />} />
        </Routes>
      </main>
    </div>
  )
}
