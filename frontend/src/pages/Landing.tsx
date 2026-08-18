/**
 * Public landing page.
 *
 * Two jobs, in this order: get a returning user signed in without scrolling,
 * and explain the product to someone who has never seen it. Everything above
 * the fold serves one of those.
 *
 * There is no registration form anywhere on this page, on purpose -- access is
 * granted by the owner, so a stranger can ask and nothing more.
 */

import { useState } from 'react'
import { api } from '../api'
import '../landing.css'

/** The same player, scored under two different rule sets.
 *
 * This is the entire argument for the product, so it is shown as a worked
 * calculation rather than asserted in a sentence. The numbers are a real
 * half-PPR vs standard comparison for a pass-catching back.
 */
const PROOF = {
  player: 'Jahmyr Gibbs · RB · DET',
  left: {
    label: 'Standard scoring',
    lines: [
      ['1,382 rush yds × 0.1', '138.2'],
      ['13.8 rush TD × 6', '83.0'],
      ['581 rec yds × 0.1', '58.1'],
      ['71 receptions × 0', '0.0'],
      ['4.1 rec TD × 6', '24.8'],
    ],
    total: '301.8',
  },
  right: {
    label: 'Half PPR, your league',
    lines: [
      ['1,382 rush yds × 0.1', '138.2'],
      ['13.8 rush TD × 6', '83.0'],
      ['581 rec yds × 0.1', '58.1'],
      ['71 receptions × 0.5', '35.6'],
      ['4.1 rec TD × 6', '24.8'],
    ],
    total: '337.4',
  },
}

const CHAIN = [
  {
    n: '01',
    title: 'Read your league',
    body: 'Scoring rules, starting slots, flex eligibility, bench size, waiver day — imported from ESPN, not assumed.',
  },
  {
    n: '02',
    title: 'Re-score every player',
    body: 'Each projection is a raw stat line. Multiply it by your rules and you get a number that is true for your league only.',
  },
  {
    n: '03',
    title: 'Value against replacement',
    body: 'A player is worth what he adds over the free agent you would start instead — computed from your slots, not a generic chart.',
  },
  {
    n: '04',
    title: 'Show the reasoning',
    body: 'Every recommendation carries the number behind it. Open any player and read the calculation line by line.',
  },
]

const ANSWERS = [
  {
    tag: 'Week',
    title: 'Who should I start?',
    body: 'Your best lineup for the week, with the margin on every call — and a warning when a starter is out or on bye.',
  },
  {
    tag: 'Waivers',
    title: 'Who should I pick up?',
    body: 'Ranked adds with a suggested bid, who to drop, and how many points each one actually adds.',
  },
  {
    tag: 'Teams',
    title: 'How do I compare?',
    body: 'All twelve teams scored the same way and ranked, so a number means something instead of floating alone.',
  },
  {
    tag: 'Trade',
    title: 'Is this deal good?',
    body: 'Both sides valued for the week and the rest of the season, with a flag when it leaves you thin somewhere.',
  },
]

function ProofColumn({ side }: { side: typeof PROOF.left }) {
  return (
    <div className="lp-proof-col">
      <div className="lp-proof-label">{side.label}</div>
      {side.lines.map(([what, value]) => (
        <div className="lp-line" key={what}>
          <span>{what}</span>
          <span>{value}</span>
        </div>
      ))}
      <div className="lp-total">
        <span>Season</span>
        <span className="n">{side.total}</span>
      </div>
    </div>
  )
}

export default function Landing({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [signingIn, setSigningIn] = useState(false)
  const [loginError, setLoginError] = useState<string | null>(null)

  const [betaEmail, setBetaEmail] = useState('')
  const [betaName, setBetaName] = useState('')
  const [betaNote, setBetaNote] = useState('')
  const [betaBusy, setBetaBusy] = useState(false)
  const [betaMsg, setBetaMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function signIn(event: React.FormEvent) {
    event.preventDefault()
    setSigningIn(true)
    setLoginError(null)
    try {
      await api.login(username, password)
      onSignedIn()
    } catch (error) {
      setLoginError((error as Error).message)
    } finally {
      setSigningIn(false)
    }
  }

  async function requestBeta(event: React.FormEvent) {
    event.preventDefault()
    setBetaBusy(true)
    setBetaMsg(null)
    try {
      await api.requestBeta({ email: betaEmail, name: betaName, note: betaNote })
      setBetaEmail('')
      setBetaName('')
      setBetaNote('')
      setBetaMsg({ ok: true, text: 'Request received. You will hear back by email.' })
    } catch (error) {
      setBetaMsg({ ok: false, text: (error as Error).message })
    } finally {
      setBetaBusy(false)
    }
  }

  return (
    <div className="lp">
      <div className="lp-wrap">
        <header className="lp-head">
          <div className="lp-mark">Fantasy War Room</div>
          <div className="lp-head-note">ESPN leagues · invite only</div>
        </header>

        <section className="lp-hero">
          <div>
            <p className="lp-eyebrow">Season management for ESPN leagues</p>
            <h1 className="lp-h1">
              Most fantasy advice is written for <em>somebody else's league</em>
            </h1>
            <p className="lp-lede">
              Rankings you find online assume a scoring system you probably don't play.
              Fantasy War Room imports <strong>your</strong> league's actual rules and
              re-scores every player under them — then tells you who to start, who to
              pick up, and what a trade is really worth.
            </p>

            <div className="lp-proof">
              <div className="lp-proof-head">{PROOF.player} — same projection, two rule sets</div>
              <div className="lp-proof-grid">
                <ProofColumn side={PROOF.left} />
                <ProofColumn side={PROOF.right} />
              </div>
              <div className="lp-proof-foot">
                A <b>35.6 point</b> swing from one rule. Enough to move him several
                spots — which is why a generic ranking cannot be right for everyone.
              </div>
            </div>
          </div>

          <div className="lp-card">
            <h2>Sign in</h2>
            <p className="sub">For members with an existing account.</p>
            <form onSubmit={signIn} method="post">
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
              <button className="lp-btn" type="submit" disabled={signingIn}>
                {signingIn ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
            {loginError && <div className="lp-msg bad">{loginError}</div>}
            <p className="sub" style={{ marginTop: 16, marginBottom: 0 }}>
              Accounts are created by invitation. Ask for access below.
            </p>
          </div>
        </section>

        <section className="lp-section">
          <h2 className="lp-h2">How a number gets made</h2>
          <p className="lp-sub">
            Four steps, in order. Nothing is a black box — you can follow any
            recommendation back to the stat line it came from.
          </p>
          <div className="lp-chain">
            {CHAIN.map((step) => (
              <div className="lp-step" key={step.n}>
                <span className="n">{step.n}</span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section">
          <h2 className="lp-h2">What it answers every week</h2>
          <p className="lp-sub">
            The draft is one day. This is built for the seventeen weeks afterwards,
            where most leagues are actually won and lost.
          </p>
          <div className="lp-answers">
            {ANSWERS.map((answer) => (
              <div className="lp-answer" key={answer.tag}>
                <span className="tag">{answer.tag}</span>
                <h3>{answer.title}</h3>
                <p>{answer.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-beta">
            <div>
              <h2 className="lp-h2">Request access</h2>
              <p className="lp-sub" style={{ marginBottom: 18 }}>
                Fantasy War Room is in closed beta with a small group of ESPN leagues.
                There is no public sign-up. Leave an address and we will get back to
                you when a place opens.
              </p>
              <p className="lp-sub" style={{ fontSize: 14, marginBottom: 0 }}>
                It reads your league and tells you what to do. It never changes your
                lineup, never makes a claim, and never touches your team — every move
                stays yours.
              </p>
            </div>

            <div className="lp-card">
              <form onSubmit={requestBeta}>
                <div className="lp-row">
                  <label className="lp-field">
                    <span>Name</span>
                    <input
                      className="lp-input"
                      type="text"
                      name="name"
                      autoComplete="name"
                      value={betaName}
                      onChange={(e) => setBetaName(e.target.value)}
                    />
                  </label>
                  <label className="lp-field">
                    <span>Email</span>
                    <input
                      className="lp-input"
                      type="email"
                      name="email"
                      autoComplete="email"
                      value={betaEmail}
                      onChange={(e) => setBetaEmail(e.target.value)}
                      required
                    />
                  </label>
                </div>
                <label className="lp-field">
                  <span>Anything useful (optional)</span>
                  <input
                    className="lp-input"
                    type="text"
                    name="note"
                    placeholder="League size, scoring, how you heard about it"
                    value={betaNote}
                    onChange={(e) => setBetaNote(e.target.value)}
                  />
                </label>
                <button className="lp-btn ghost" type="submit" disabled={betaBusy}>
                  {betaBusy ? 'Sending…' : 'Request access'}
                </button>
              </form>
              {betaMsg && (
                <div className={`lp-msg ${betaMsg.ok ? 'good' : 'bad'}`}>{betaMsg.text}</div>
              )}
            </div>
          </div>
        </section>

        <footer className="lp-foot">
          <span>Fantasy War Room</span>
          <span>Not affiliated with ESPN. Your league data stays on your own server.</span>
        </footer>
      </div>
    </div>
  )
}
