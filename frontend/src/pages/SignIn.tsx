/**
 * Sign in, for installs that have accounts.
 *
 * A self-hosted install never sees this screen: it runs as one implicit local
 * account and the app opens straight onto the board. This is the front door
 * for the hosted deployment, where several people each connect their own
 * leagues.
 */

import { useState } from 'react'
import { api } from '../api'
import { Banner, Card } from '../components'

export default function SignIn({
  allowRegistration,
  onSignedIn,
}: {
  allowRegistration: boolean
  onSignedIn: () => void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const registering = mode === 'register'

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (registering) {
        await api.register(email.trim(), password, displayName.trim())
      } else {
        await api.login(email.trim(), password)
      }
      setPassword('')
      onSignedIn()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div style={{ minWidth: 0 }}>
          <h1>Fantasy War Room</h1>
          <div className="sub">Draft and season decisions, explained</div>
        </div>
      </header>

      <main className="app-main">
        <Card title={registering ? 'Create an account' : 'Sign in'}>
          <form onSubmit={submit}>
            <label className="tiny faint">EMAIL</label>
            <input
              type="email"
              autoComplete="username"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{ marginBottom: 8 }}
            />

            {registering && (
              <>
                <label className="tiny faint">NAME (OPTIONAL)</label>
                <input
                  type="text"
                  autoComplete="nickname"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  style={{ marginBottom: 8 }}
                />
              </>
            )}

            <label className="tiny faint">PASSWORD</label>
            <input
              type="password"
              autoComplete={registering ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ marginBottom: 10 }}
            />

            <button
              className="btn primary block"
              type="submit"
              disabled={busy || !email.trim() || password.length < 1}
            >
              {busy ? 'Working…' : registering ? 'Create account' : 'Sign in'}
            </button>
          </form>

          {registering && (
            <div className="tiny faint" style={{ marginTop: 8 }}>
              Passwords need at least 8 characters. Your league credentials are stored
              against your account and are never shown to anyone else.
            </div>
          )}

          {error && (
            <div style={{ marginTop: 10 }}>
              <Banner kind="error">{error}</Banner>
            </div>
          )}

          {allowRegistration && (
            <button
              className="btn block"
              style={{ marginTop: 10 }}
              onClick={() => {
                setMode(registering ? 'login' : 'register')
                setError(null)
              }}
            >
              {registering ? 'I already have an account' : 'Create an account'}
            </button>
          )}
        </Card>
      </main>
    </div>
  )
}
