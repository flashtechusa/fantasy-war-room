/**
 * Owner-only: who has access, and who is asking for it.
 *
 * A new account's password is shown exactly once, here, at the moment it is
 * created. It is stored hashed, so there is no way to look it up later -- if
 * it gets lost, reset it and hand over a new one.
 */

import { useState } from 'react'
import { api, type AccountRow, type BetaRequestRow } from '../api'
import { Banner, Card, Loading } from '../components'
import { useAsync } from '../useAsync'

const ROLE_NOTE: Record<string, string> = {
  owner: 'Everything, including this screen',
  partner: 'Can switch accounts on and off. Cannot see rosters.',
  client: 'Their own team only',
}

function Credentials({ username, password }: { username: string; password: string }) {
  const [copied, setCopied] = useState(false)
  const text = `Username: ${username}\nPassword: ${password}\nhttps://warroom.flashtechusa.com`

  return (
    <Banner kind="info">
      <div style={{ fontWeight: 700, marginBottom: 6 }}>
        Send these now — the password cannot be shown again
      </div>
      <div className="mono" style={{ fontSize: 13, whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
        {text}
      </div>
      <button
        className="btn sm"
        style={{ marginTop: 8 }}
        onClick={() => {
          navigator.clipboard?.writeText(text)
          setCopied(true)
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </Banner>
  )
}

export default function Access() {
  const requests = useAsync(() => api.betaRequests(), [])
  const users = useAsync(() => api.users(), [])

  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('client')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [issued, setIssued] = useState<{ username: string; password: string } | null>(null)

  async function createAccount(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setIssued(null)
    try {
      const result = await api.createUser({
        username,
        display_name: displayName,
        role,
      })
      setIssued({ username: result.user.username, password: result.password })
      setUsername('')
      setDisplayName('')
      users.reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function toggle(user: AccountRow) {
    try {
      await api.updateUser(user.id, { enabled: !user.enabled })
      users.reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function reset(user: AccountRow) {
    try {
      const result = await api.resetPassword(user.id)
      setIssued({ username: result.user.username, password: result.password })
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function toggleTrades(user: AccountRow) {
    try {
      await api.updateUser(user.id, { can_send_trades: !user.can_send_trades })
      users.reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function markHandled(row: BetaRequestRow) {
    await api.markBetaRequest(row.id, !row.handled)
    requests.reload()
  }

  function useRequest(row: BetaRequestRow) {
    // Seed the form from the request rather than making the owner retype it.
    setUsername(row.email.split('@')[0].toLowerCase())
    setDisplayName(row.name || '')
    setRole('client')
  }

  if (requests.loading || users.loading) return <Loading what="access" />

  const pending = (requests.data?.requests ?? []).filter((r) => !r.handled)
  const done = (requests.data?.requests ?? []).filter((r) => r.handled)

  return (
    <>
      {error && <Banner kind="error">{error}</Banner>}
      {issued && <Credentials username={issued.username} password={issued.password} />}

      <Card title={`Access requests (${pending.length} waiting)`}>
        {pending.length === 0 ? (
          <div className="small faint">Nothing waiting.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Name</th>
                  <th>Note</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {pending.map((row) => (
                  <tr key={row.id}>
                    <td>{row.email}</td>
                    <td>{row.name || '—'}</td>
                    <td className="small faint" style={{ whiteSpace: 'normal' }}>
                      {row.note || '—'}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button className="btn sm" onClick={() => useRequest(row)}>
                          Use
                        </button>
                        <button className="btn sm" onClick={() => markHandled(row)}>
                          Dismiss
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {done.length > 0 && (
          <div className="tiny faint" style={{ marginTop: 10 }}>
            {done.length} already handled.
          </div>
        )}
      </Card>

      <Card title="Create an account">
        <form onSubmit={createAccount}>
          <label className="tiny faint">USERNAME</label>
          <input
            type="text"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">DISPLAY NAME</label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <label className="tiny faint">ROLE</label>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            style={{ marginBottom: 6 }}
          >
            <option value="client">Client</option>
            <option value="partner">Partner</option>
            <option value="owner">Owner</option>
          </select>
          <div className="tiny faint" style={{ marginBottom: 10 }}>
            {ROLE_NOTE[role]}
          </div>
          <button className="btn primary block" type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Create account'}
          </button>
        </form>
        <div className="tiny faint" style={{ marginTop: 8 }}>
          A password is generated and shown once. It is stored hashed, so it
          cannot be looked up later — reset it instead.
        </div>
      </Card>

      <Card title={`Accounts (${users.data?.users.length ?? 0})`}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Send trades</th>
                <th>Last in</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(users.data?.users ?? []).map((user) => (
                <tr key={user.id} style={{ opacity: user.enabled ? 1 : 0.5 }}>
                  <td>
                    {user.display_name || user.username}
                    <div className="tiny faint">{user.username}</div>
                  </td>
                  <td>
                    <span className="pill">{user.role}</span>
                    {!user.enabled && (
                      <span className="pill" style={{ color: 'var(--danger)', marginLeft: 4 }}>
                        OFF
                      </span>
                    )}
                  </td>
                  <td>
                    <button
                      className={`btn sm ${user.can_send_trades ? 'primary' : ''}`}
                      onClick={() => toggleTrades(user)}
                      title="Allow this account to send trade proposals to ESPN"
                    >
                      {user.can_send_trades ? 'On' : 'Off'}
                    </button>
                  </td>
                  <td className="tiny faint">
                    {user.last_login_at
                      ? new Date(user.last_login_at).toLocaleDateString()
                      : 'never'}
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <button className="btn sm" onClick={() => toggle(user)}>
                        {user.enabled ? 'Switch off' : 'Switch on'}
                      </button>
                      <button className="btn sm" onClick={() => reset(user)}>
                        Reset
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tiny faint" style={{ marginTop: 8 }}>
          Switching an account off ends its session immediately — they are signed
          out of any browser they left open. “Send trades” lets an account build a
          trade proposal to ESPN from the Trade screen; it is off by default and
          currently preview-only (nothing is actually sent yet).
        </div>
      </Card>
    </>
  )
}
