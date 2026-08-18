/**
 * The admin entrance.
 *
 * Deliberately its own address with its own sign-in rather than a hidden route
 * inside the app. Managing who has access is a different job from managing a
 * fantasy team, and it should be done from a different account -- so that a
 * browser left signed in to a team cannot also hand out accounts.
 *
 * Same session mechanism underneath. Two parallel auth systems would be twice
 * the code and twice the places to get it wrong.
 */

import { useState } from 'react'
import { api } from '../api'
import { useAsync } from '../useAsync'
import Access from './Access'
import '../landing.css'

function AdminSignIn({
  onSignedIn,
  notice,
}: {
  onSignedIn: () => void
  notice?: string | null
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(username, password)
      onSignedIn()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="lp" style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
      <div className="lp-wrap" style={{ maxWidth: 420, width: '100%', padding: '32px 24px' }}>
        <div className="lp-mark" style={{ marginBottom: 6 }}>
          Fantasy War Room
        </div>
        <p className="lp-eyebrow" style={{ marginBottom: 22 }}>
          Administration
        </p>

        <div className="lp-card">
          <h2>Admin sign in</h2>
          <p className="sub">Owner accounts only.</p>
          <form onSubmit={submit} method="post">
            <label className="lp-field">
              <span>Username</span>
              <input
                className="lp-input"
                type="text"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </label>
            <label className="lp-field">
              <span>Password</span>
              <input
                className="lp-input"
                type="password"
                name="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            <button className="lp-btn" type="submit" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
          {(error || notice) && <div className="lp-msg bad">{error ?? notice}</div>}
        </div>

        <p className="lp-sub" style={{ marginTop: 18, fontSize: 13 }}>
          This is not the app. To manage a fantasy team, sign in at the main address.
        </p>
      </div>
    </div>
  )
}

export default function AdminConsole() {
  const auth = useAsync(() => api.me(), [])

  if (auth.loading) return null

  const user = auth.data?.authenticated ? auth.data.user : null

  if (!user) {
    return <AdminSignIn onSignedIn={() => auth.reload()} />
  }

  // Signed in, but as somebody who cannot manage access. Say so rather than
  // showing an empty console, and offer the way out.
  if (user.role !== 'owner') {
    return (
      <AdminSignIn
        onSignedIn={() => auth.reload()}
        notice={`Signed in as ${user.username}, which is not an owner account. Sign in with an admin account.`}
      />
    )
  }

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
          <h1>Administration</h1>
          <div className="sub">Fantasy War Room</div>
        </div>
        <div className="row" style={{ gap: 10, flexShrink: 0, alignItems: 'center' }}>
          <div style={{ textAlign: 'right', minWidth: 0 }}>
            <div className="small" style={{ fontWeight: 650 }}>
              {user.display_name || user.username}
            </div>
            <div className="tiny faint">{user.role}</div>
          </div>
          <button className="btn sm" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      <main className="app-main">
        <Access />
      </main>
    </div>
  )
}
