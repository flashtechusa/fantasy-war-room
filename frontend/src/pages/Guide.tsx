/**
 * Getting-started guide for new users.
 *
 * Deliberately describes only what the app actually does, screen by screen, in
 * the order a new person meets them: connect a league first, then the weekly
 * tools. Role-specific notes (sending trades, admin) are shown only to the
 * accounts they apply to, so nobody is told about a button they can't see.
 */

import { api } from '../api'
import { Card } from '../components'
import { useAsync } from '../useAsync'

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
      <div
        aria-hidden
        style={{
          flexShrink: 0,
          width: 26,
          height: 26,
          borderRadius: 13,
          background: 'var(--accent, #4f7cff)',
          color: '#fff',
          fontWeight: 700,
          fontSize: 14,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {n}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 650, marginBottom: 2 }}>{title}</div>
        <div className="small muted">{children}</div>
      </div>
    </div>
  )
}

export default function Guide() {
  const me = useAsync(() => api.me(), [])
  const canSend = Boolean(me.data?.user?.can_send_trades)
  const isOwner = me.data?.user?.role === 'owner'

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <Card title="Welcome to Fantasy War Room">
        <div className="small muted">
          War Room reads your ESPN league and turns it into decisions: who to
          start, who to pick up, how your roster stacks up, and which trades help
          you. It never changes anything in ESPN unless you explicitly tell it to.
          Here's how to get going.
        </div>
      </Card>

      <Card title="1. Connect your ESPN league">
        <Step n={1} title="Open League → Connect ESPN">
          On the <strong>League</strong> tab (⚙️), use <strong>Connect ESPN</strong>.
          You'll sign in with an emailed code, or paste your league's two ESPN
          cookies. Do this once on a <strong>desktop/laptop</strong> browser —
          phones make the cookie step painful. After that, the app works
          everywhere, including your phone.
        </Step>
        <Step n={2} title="Pick your team">
          Choose which team is yours. If ESPN can confirm you own it, it's locked
          in; otherwise it's your say-so. Everything personalized — My Team,
          trades, start/sit — keys off this.
        </Step>
        <div className="tiny faint">
          No league yet? The app runs on a built-in demo league so you can look
          around first — it's labeled “DEMO DATA” in the header.
        </div>
      </Card>

      <Card title="2. Your weekly screens">
        <Step n={1} title="Week 📅 — start / sit">
          Your best legal lineup for the week, with the calls it would change.
          The place to set your lineup each week.
        </Step>
        <Step n={2} title="Waivers 🔍 — who to add">
          Free agents ranked by what they'd actually add to <em>your</em> roster,
          by position, with a short why for each.
        </Step>
        <Step n={3} title="My Team 🛡️ — strengths & holes">
          Your roster graded: strong spots, weak spots, bye-week pileups, and
          what to target next.
        </Step>
        <Step n={4} title="Teams 🏈 — league power rankings">
          Every team ranked two ways: projected starting points, and whole-roster
          value (VORP). A team can be strong on one and not the other.
        </Step>
        <Step n={5} title="Trade 🔄 — trades that help you">
          “Trades we found for you” scans every other team for swaps that lift
          your lineup — and flags the ones that help the other side too, since
          those actually get accepted. Toggle <strong>rest-of-season</strong> vs{' '}
          <strong>win this week</strong>. Nothing is sent to ESPN here; you
          propose it in ESPN yourself.
        </Step>
      </Card>

      <Card title="3. Choose your projection source">
        <div className="small muted" style={{ marginBottom: 8 }}>
          On the <strong>League</strong> tab, under <strong>Projection source</strong>,
          pick whose numbers build your board. This is per-user — it changes what{' '}
          <em>you</em> see, not the league.
        </div>
        <ul className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li><strong>ESPN</strong> — the default; ESPN's own projections.</li>
          <li><strong>Sleeper</strong> — Sleeper's numbers, re-scored under your league's rules. No key needed.</li>
          <li><strong>FantasyPros</strong> — your own FantasyPros key (added on the same screen).</li>
          <li><strong>Consensus</strong> — a blend of whatever sources you've imported.</li>
        </ul>
        <div className="tiny faint" style={{ marginTop: 8 }}>
          Each shows a coverage number so you always know how much of your roster
          a source actually covers.
        </div>
      </Card>

      <Card title="4. Auto Mode 🤖 (optional)">
        <div className="small muted">
          If your admin enables it for you, the <strong>Auto</strong> tab can run
          your team on autopilot — setting your optimal lineup, working the waiver
          wire, and surfacing trades. It's off by default. Lineup setting is{' '}
          <strong>live and runs on a schedule</strong>: once you turn it on, Auto
          Mode sets your optimal lineup on ESPN on its own, and{' '}
          <strong>Run Auto Mode now</strong> fires that same cycle immediately.
          Waiver pickups are a <strong>live</strong> write too, but you submit
          those yourself from the Waivers tab (add + drop or a FAAB claim, behind
          an explicit confirm) — the scheduler holds off on autonomous claims
          because they spend FAAB and drop players. Trades stay surfaced for your
          one-tap approval; Auto Mode never sends them on its own. You choose
          which parts to turn on.
        </div>
      </Card>

      <Card title="5. Draft tools (draft day)">
        <div className="small muted">
          When it's draft season, the <strong>League</strong> tab links to the{' '}
          <strong>Draft Board</strong> (live best-available with your needs),{' '}
          <strong>Live Draft</strong> (follows an in-progress ESPN draft pick by
          pick), and the <strong>Simulator</strong> (runs your draft slot many
          times to test a strategy). They matter one day a year; the weekly
          screens matter every week.
        </div>
      </Card>

      {canSend && (
        <Card title="Sending trades to ESPN (enabled for you)">
          <div className="small muted">
            Your account can submit a trade proposal to ESPN from the{' '}
            <strong>Trade</strong> screen. On any proposal, use{' '}
            <strong>Preview send to ESPN</strong> to see the exact offer, then{' '}
            <strong>Send → Confirm</strong>. It's a real, irreversible proposal to
            the other manager — the preview and confirmation are there so nothing
            goes out by accident. If a send is ever blocked, your league admin has
            the master switch turned off.
          </div>
        </Card>
      )}

      {isOwner && (
        <Card title="For the league admin">
          <div className="small muted">
            The <strong>Administration</strong> screen (go to <code>/admin</code>)
            is where you create accounts, reset passwords, grant the per-account{' '}
            <strong>Send trades</strong> permission, and hold the install-wide{' '}
            <strong>trade-sending kill switch</strong> (off by default — flip it on
            only when you want anyone to be able to send). Accounts you switch off
            are signed out immediately.
          </div>
        </Card>
      )}

      <Card title="Good to know">
        <ul className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
          <li>Your ESPN cookies are stored encrypted and never shown or shared.</li>
          <li>
            The app is read-only against ESPN except for a trade proposal you
            explicitly confirm.
          </li>
          <li>
            Connect once on a computer; after that use it anywhere, including your
            phone's home screen (add it like an app).
          </li>
          <li>
            The short code in the header (next to your league) is the app version —
            handy if you're told an update shipped.
          </li>
        </ul>
      </Card>
    </div>
  )
}
