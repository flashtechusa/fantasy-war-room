# Product direction

Decisions made in conversation, written down so they survive it. This is the
intended shape of the product *and* a snapshot of what backs it in code today.
Where the two differ, it says so — see `TODO.md` for the running gap.

Status in one line: the platform (accounts, per-user encrypted ESPN
connections, import, live-draft sync) is **built and multi-tenant**. The
season-long **alerts** — the thing being sold — are **not built yet**.

---

## What it is

A season-management add-on sold alongside a professional drafting service.

The drafter (currently found and paid through Fiverr) drafts a client's team.
That is his business and it stays his business. This tool covers the seventeen
weeks afterwards, which he is not paid for and does not do. It is positioned
as the middle tier: cheaper than paying him to manage a full season, for people
who want his draft but cannot justify the rest.

## What it does NOT do: write to ESPN

**The app never makes transactions.** It reads the league, works out what it
would do, and notifies the user. They execute it themselves in ESPN.

This is a deliberate constraint, not a missing feature:

- `espn-api` is read-only. Writes would mean hand-rolling calls to ESPN's
  private, undocumented transaction endpoint.
- Drops and FAAB bids are irreversible. A bug spends real money or discards a
  real player.
- "We hold your credentials and act on your behalf" is a materially different
  liability than "we read your league and alert you".

Worst case for a wrong alert is the user ignores it. That asymmetry is the
whole reason for the design.

## What's built today

A snapshot, so a future session doesn't re-derive it or contradict it.

| Area | State |
|---|---|
| Multi-user accounts, invite-only, roles (owner / partner / client) | **Built** — `User`, `AuthSession`, owner-created accounts, admin console |
| Per-user ESPN connection, isolated between accounts | **Built** — `UserEspnConfig`, resolved per signed-in user |
| ESPN cookies encrypted at rest (Fernet, key outside the DB) | **Built** — `services/secrets.py` |
| Cookie redaction from logs, errors, and API responses | **Built** — `espn/redaction.py`, filter on the root logger |
| Connect ESPN flow: discover leagues → confirm rules → import | **Built** — `services/espn_connect.py`, `ConnectEspn.tsx` |
| League discovery from the ESPN fan profile | **Built** — `espn/discovery.py` |
| Public-league import with no credentials | **Built** |
| ESPN Email Code (OTP) connect — the primary method | **Built and proven live** (real account → code → private league HTTP 200, 4 teams). One variable left: the server-side VPS origin — see below |
| Live-draft sync with a direct `mDraftDetail` fallback | **Built** — see `espn-api-comparison.md` |
| Draft-sync diagnostics (behind `FWR_DEBUG_SCREENS`) | **Built** |
| Desktop browser extension (cookie capture) | **Built, proof-of-concept, unpublished** |
| Verified vs unverified team ownership | **Built** — `select_league` binds a verified team to the account's SWID server-side and rejects overrides; see below |
| Season-long alerts | **Not built** — the design below is the plan |
| Billing | **Not built, and last on purpose** |

## Accounts and access

Payment happens on Fiverr, outside the app entirely. No Stripe, no card data,
no billing code. The app answers one question: *is this account on for this
season?*

Access is per **season**, which the schema already models — `League` is keyed
on `(espn_league_id, season)`. An entitlement is `(user_id, season, enabled)`.
When the season ends nothing has to expire: the next season simply has no row,
so it is off until someone turns it on.

Three roles:

| Role | Can do |
|---|---|
| Owner | Everything; full visibility across all accounts |
| Partner (the drafter) | Enable/disable accounts for a season |
| Client | Their own team only |

When an account is off, paywall the screens but **keep the data**. Their league
history is the reason they come back, and forcing a re-import is the friction
that stops them.

---

## ESPN connection model

The central design problem, and where most of the recent work went. ESPN has no
OAuth, no API key, and no read-only token. Private leagues require two session
cookies (`SWID`, `espn_s2`); `espn_s2` is `HttpOnly`, so it cannot be read by
any page script, on any device. Everything below follows from that one fact.

### Two trust levels

The public/private split is not just a capability split — it is a **trust**
split, and the product should treat it as one.

| Level | How connected | What it proves |
|---|---|---|
| **Unverified** | Public league, league id only, no credentials | We can read the league. We do **not** know the user is in it, or which team is theirs. Team choice is a self-assertion. |
| **Verified** | Private league, authenticated cookies | The `SWID` proves the ESPN account, and matching it to an owner id proves which team is theirs. |

This matters the moment two people share a league in the app: an unverified
connection must never be treated as proof of team ownership.

### Connection methods, in priority order

Offered in this order; all converge on the same stored `SWID` + `espn_s2`.

| # | Method | Works for | Device | Verified? |
|---|---|---|---|---|
| 1 | **ESPN Email Code (OTP)** — primary | public + private | **any, incl. iPhone** | Verified |
| 2 | Public League Link | public only | any | Unverified |
| 3 | Manual `SWID` + `espn_s2` | public + private | desktop (needs DevTools) | Verified |

OTP is preferred even for public leagues, because it authenticates and so
verifies team ownership, which the public link cannot. The rest are fallbacks in
that order, and manual is **permanent** — it depends on nothing but a desktop
browser, so it is the floor under everything else. The full user-facing
procedure for all three, and what to do when one breaks, is the runbook
`espn-connection-backup.md`.

The **browser extension is not offered as a UI option.** It exists only as a
developer-mode proof-of-concept (`browser-extension/`), not something a user can
install, so presenting it in the connect screen would be a promise the product
cannot keep. The backend pairing endpoint remains for maintainers who load it
unpacked; it is documented in the runbook as a developer backup, not a user
method.

**OTP changes the iPhone-private story.** That case previously had no browser
path and was marked "native app required". OTP connects a private league on an
iPhone with no app and no desktop — which is why it is now primary. The full
flow has been **run live against real Disney**: a real ESPN account, a real
emailed code, ending in `SWID` + `espn_s2` that read the private test league
(30039838) at HTTP 200 with its four teams. The contract in `oneid.py` matches
the observed responses exactly. Native drops from "required" to a reliability
fallback.

The one variable not yet exercised: that live run originated from a *desktop*
IP. Production makes these calls **server-side from the Windows VPS** (a
datacenter IP), and Disney's risk scoring *could* treat that differently. To
confirm, run `scripts/test_espn_otp.py` on the VPS itself; a green run there
closes it. Low-volume account recovery is not usually IP-gated, so this is a
check, not an expected failure. The **Share → Fantasy War Room** iOS share target is still worth
building, but now as a convenience on the #2 fallback rather than the mobile
answer.

### Server-side ownership enforcement (built)

Verified team ownership is **bound to the signed-in account on the backend**.
Changing a frontend `team_id` cannot let one user impersonate another manager.

**How it works now** (`espn_connect.select_league`):

- When the connection is **authenticated** (cookies present) and ESPN's owner
  ids match the account's `SWID` to a team, that team is assigned automatically
  and the connection is recorded `verified = True`. A submitted `team_id` that
  disagrees is **rejected** (HTTP 400) — the client cannot override it.
- When there is **no SWID match** — a public/anonymous connection, or an
  authenticated one whose `SWID` owns no team (someone else set the team up) —
  the team is a self-assertion: the submitted `team_id` is accepted but the
  connection is recorded `verified = False`.

The `verified` flag is persisted on `UserEspnConfig` and surfaced by
`GET /api/espn/status`, so every screen can label an unverified team as a
self-assertion rather than treating it as proof of ownership. The connect UI
locks the team selector once ESPN has confirmed ownership. The column is added
to existing databases automatically on startup (`db._ensure_added_columns`),
since `create_all` never alters a table that already exists.

### Dead ends — do not re-investigate

Each of these cost real time to rule out. They are closed; a future session
should not reopen them without new evidence.

- **Bookmarklet / page script reading `espn_s2`.** Impossible — the cookie is
  `HttpOnly`. Confirmed repeatedly.
- **`consentToken` for auth.** It is a cookie-consent token, not a session
  credential. Returns 401 against the league API. Useless.
- **Browser-side bearer-token experiments.** A OneID `access_token` is not kept
  in ESPN web-client storage, and cross-origin `Authorization` requests are
  blocked by CORS before ESPN sees them. The browser cannot settle whether the
  backend accepts bearer auth. (`scratch/espn_auth_matrix.py` can, from a
  desktop with a real token — but only pursue it if a working bearer flow would
  materially improve mobile onboarding, which it would not: even if the backend
  accepted bearer, obtaining the token on an iPhone is itself unsolved.)
- **iOS Safari Web Extension reading `espn_s2`.** Apple states `browser.cookies`
  cannot read HttpOnly cookies in a Safari Web Extension. Documented in
  `espn-connection.md`. **If that limitation is ever shown to be wrong, the
  iPhone-private lane reopens** — it is the single assumption that pins that
  case to "native required."

### Credentials handling

Each client connects their **own** ESPN account. The app is sold to clients,
not operated on their behalf — the drafter is the distribution channel. Routing
everything through the drafter's login was considered and rejected: he logs in
*as* the client rather than using co-manager, so it would mean storing many
people's account credentials for a workflow that violates ESPN's terms on
account sharing. Each user authorising their own account is both safer and
unremarkable.

The security posture, all **built**:

- Cookies are encrypted at rest with a key held outside the database.
- No endpoint returns a cookie value; status is reported as set/unset only.
- A redaction filter keeps cookies out of logs and error messages, including
  those emitted by third-party libraries.
- **Disconnect ESPN** deletes the stored credentials outright.

A cross-origin data-shipping bookmarklet — logic hosted on our domain, injected
into an ESPN page, POSTing league *data* (never the cookie) to the backend — is
a viable future option where the credential never leaves ESPN at all. Not built;
noted because it is the strongest privacy story of any path and the one most
"website-shaped."

---

## Live draft

Manual pick entry is the primary path: it needs no cookies and cannot be broken
by ESPN changing an endpoint mid-draft. ESPN sync is an optional overlay that
only ever *adds* picks.

The one real finding from studying other clients: `espn-api` returns **zero**
picks during a live draft, because it gates on a "draft complete" flag ESPN does
not set until the draft ends. So `live_draft_picks()` now reads `mDraftDetail`
directly as well and takes whichever source reports more picks — additive, so it
cannot regress the library path. Full reasoning and the endpoint comparison are
in `espn-api-comparison.md`. `FWR_ESPN_DRAFT_SOURCE` pins the behaviour if
needed.

## The alerts

**Not built yet — this is the design.** Timed off each league's real settings,
which are already imported (`waiver_process_days`, scoring, roster slots) rather
than a generic schedule.

| Alert | When | Why it earns a notification |
|---|---|---|
| Waiver targets | Night before the league processes waivers | Ranked adds with a suggested FAAB bid, under this league's scoring |
| Broken lineup | ~90 min before kickoff | A starter is OUT, on bye, or inactive — the most costly unforced error, and entirely preventable |
| Better start available | Sunday morning | Only when the margin clears the noise floor, so it does not cry wolf |
| Injury changes the week | As news lands | A starter's status changed since they last looked |

Delivery is **web push** to a home-screen PWA: works on iOS, no App Store, no
Apple developer account, no SMS bill. Email as the fallback.

## Known risks

- **ESPN's terms.** This runs on a private, undocumented API. Personal use is
  one thing; charging for access is a different posture and they can cut it off.
- **Single point of failure.** If ESPN changes that API mid-season, every
  customer breaks the same morning and the support call is ours.
- **Team impersonation on unverified connections.** Closed for verified
  (authenticated) connections — the server binds the team to the account's
  `SWID` and rejects overrides. A **public** connection is still a
  self-assertion by design; it is labelled Unverified and must never be used as
  an authorisation fact.
- **The free draft tier does not exist.** Letting a client watch their draft in
  real time is the one capability that failed in live use. If it is the hook,
  it has to be built and proven first.

## Order of work

1. Running reliably on always-on hosting (Windows VPS) — **in progress**
2. Accounts, roles, the on/off switch, credential encryption — **built**
3. ESPN connection: discovery, public/private, live-draft sync — **built**
4. Server-side team-ownership enforcement — **built**
5. Alerts working for a single user, proven over a few weeks — **the core
   unbuilt feature**
6. Anything sold to anyone — **last, on purpose**

Billing-shaped work is last on purpose. It is worthless until there is something
to sell and somewhere to sell it from.
