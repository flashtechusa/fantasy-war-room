# Connecting ESPN

How Fantasy War Room gets at your ESPN league, what the credentials involved
can do, and how to take them back.

---

## The flow

**Private league** (the common case — ESPN defaults to private):

1. **Connect your ESPN account** — hand over the two cookies once, from a
   desktop browser. See the reality check below for why a phone cannot.
2. **Auto-discover leagues** — we ask ESPN what those cookies can reach.
3. **Which league?** — tap one. No league id typed.
4. **Your team** — detected from the cookie; correct it if we got it wrong.
5. **Verify the rules** — scoring, roster, waivers, playoffs, draft type,
   shown before anything is imported.
6. **Import.**

Steps 2–6 work on any device. Only step 1 needs a desktop, once, because the
credentials are then held server-side.

**Public league:** skip step 1 entirely. Enter a league id, confirm the rules,
import. Works on a phone.

There is one place to do all of this — `Connect ESPN`. The League screen shows
connection status and links here; it used to carry a second, identical
credential form, which was removed because two ways to enter the same cookies is
one too many.

### What is discovered automatically

Once valid cookies are stored, ESPN gives us all of this without further input:

| Item | Source |
| --- | --- |
| Account identity (SWID) | the cookie itself |
| Every football league for a season | `fan.api.espn.com/apis/v2/fans/{SWID}` |
| League id, name, season, size | fan profile, confirmed against `view=mSettings` |
| Team names and ids | `view=mTeam` |
| Which team is yours | `teams[].primaryOwner` / `owners[]` matched against SWID |
| Scoring rules | `view=mSettings` → `scoringSettings.scoringItems` |
| Roster settings | `view=mSettings` → `rosterSettings.lineupSlotCounts` |
| Draft type, order, date, keeper count | `view=mSettings` → `draftSettings` |
| Draft results / progress | `view=mDraftDetail` |
| Waiver type, FAAB budget, process days | `view=mSettings` → `acquisitionSettings` |
| Playoff teams, matchup length, tiebreak | `view=mSettings` → `scheduleSettings` |

Discovery is a two-step on purpose. ESPN's fan profile is a personalisation
surface, not a contract: it changes shape, and it mixes sports. It supplies
*candidates*; every candidate is then confirmed against the league endpoint,
which is the authority. If ESPN changes the fan payload, discovery degrades to
"we found fewer of your leagues" and manual entry still works — it never
produces wrong data.

If your fan profile does not list a league (this happens with some legacy
leagues), pass the id to `GET /api/espn/leagues?league_id=…` or type it on the
League screen. It is confirmed and imported through exactly the same code.

---

## Getting the cookies with less manual work

ESPN has no OAuth, no API key, and no developer programme for fantasy. The
session cookies are the only credential that exists. The question is only how
much manual work collecting them takes.

### The constraint everything else follows from

**`espn_s2` is an `HttpOnly` cookie.** The browser attaches it to requests to
ESPN and refuses to expose it to JavaScript running on the page. This is not an
obstacle to route around — it is the mechanism that stops a cross-site
scripting bug on espn.com from stealing your ESPN session.

Consequences, stated plainly:

- A **bookmarklet cannot read `espn_s2`.** Neither can a helper page, an
  injected script, or a `<script>` tag anywhere.
- `SWID` is normally *not* HttpOnly and so is usually readable from
  `document.cookie` — but ESPN has changed cookie flags before, so our
  bookmarklet reports what it actually finds rather than assuming.
- The **only** browser API permitted to read an HttpOnly cookie is
  `chrome.cookies`, which is available to extensions holding an explicit
  `cookies` permission plus a host permission for that domain.

Verify it yourself: DevTools → Application → Cookies → `espn.com`, and look at
the `HttpOnly` column.

### Reality check: none of this works on a phone

Worth stating plainly, because it shapes every option below. On a phone there is
**no** way to obtain `espn_s2` — not just no *convenient* way:

- No mobile browser has DevTools, so the manual copy path is desktop-only too.
- Chrome on Android has no extensions; iOS Chrome and Firefox are Safari shells.
- iOS Safari Web Extensions **cannot** read HttpOnly cookies. Apple states this
  directly ([Developer Forums](https://developer.apple.com/forums/thread/657931)),
  and MDN corroborates it.
- Bookmarklets run on mobile but still cannot see an HttpOnly cookie.

The one exception: **Firefox for Android** (120+, Dec 2023) installs arbitrary
extensions from AMO, and Gecko's `browser.cookies` *does* read HttpOnly cookies.
So a published Firefox-Android extension is a genuine phone-native path. We have
not built one — it requires the user to switch browsers, which is a bigger ask
than connecting once on a laptop — but it exists, and it is the only non-native
mobile option that does.

This is not a gap in our thinking. FantasyPros — far better resourced — also
requires a desktop Chrome extension, and their live ESPN draft sync is
desktop-Chrome only, not available even inside their own native app.

**So: connecting a private league needs a desktop once.** Credentials are stored
server-side, so the phone works for the rest of the season afterwards. A public
league needs no cookies at all and connects from a phone directly.

### Option A — Browser extension ✅ implemented (desktop only)

`browser-extension/` (Manifest V3). It is the only approach that actually
removes the manual step, because `chrome.cookies` is the only interface that
can see `espn_s2`.

- Runs on `https://fantasy.espn.com/*` only — one host permission, no
  `<all_urls>`, no content script, no background worker.
- Reads the league id from the tab URL (`activeTab`, only after you click it).
- Shows **found / not found** for each cookie. The values never enter the DOM.
- Sends them once, on an explicit "Connect this ESPN account" click, to
  `POST /api/espn/extension/connect` on **your own** server.
- Nothing is persisted: `chrome.storage.local` holds the server address and
  nothing else. No `localStorage`. No console output. No third party.
- Refuses plain HTTP except to `localhost` / `127.0.0.1`.
- Authenticates with a **single-use pairing code** you generate in the app —
  the extension never holds a long-lived token for Fantasy War Room.

See [`browser-extension/README.md`](../browser-extension/README.md).

### Option B — Bookmarklet / helper page ⚠️ partially viable

`browser-extension/bookmarklet/`. It extracts the **league id, season and team
id** from the ESPN page URL and reports which cookies are readable from page
JavaScript. That removes the "find your league id" step, which is the part
people most often get wrong.

It **cannot** obtain `espn_s2`, and it says so in its own output rather than
pretending. A helper page you paste a URL into is no better: same origin rules,
same `HttpOnly` flag, same answer.

### Option C — Desktop helper ❌ investigated, deliberately not shipped

A small local script *could* read the browser's cookie database directly.
Feasible on all three platforms, and we chose not to.

Why not, concretely: Chrome and Edge encrypt their cookie store with a key held
in the OS keychain (macOS Keychain, Windows DPAPI, libsecret/kwallet on Linux).
Reading a single cookie means unlocking that key — and the same key decrypts
**every cookie the browser holds**: bank sessions, email, everything. A tool
that asks for it is asking for far more access than the job needs, and the user
has no way to verify it took only what it promised.

The extension gets the identical result with a permission scoped to one domain,
enforced by the browser rather than by our promise. Shipping a keychain-reading
helper alongside it would add real risk for no capability. If someone runs a
browser with no extension support, copying two values from DevTools once a
season is a better trade than handing a script the master key to their browser.

Should this ever be revisited, the bar is: read-only, single-cookie, explicit
per-run consent, no network access except to `localhost`, and source that fits
on one screen.

### Option D — Public league (no cookies at all)

A public ESPN league answers the v3 endpoint with no credentials, so **Connect
ESPN → Public league** takes a league id and nothing else. This is the only
route that works entirely on a phone.

Limits: ESPN leagues default to Private, and only the commissioner can change
that — league-wide, exposing League Office, Standings, Box Scores and Team Pages
to anyone with the URL. It is reversible at any time. We do not suggest flipping
a private league public just to import it; the option exists for leagues that
already are.

### Option E — Manual paste (always available)

DevTools → Application → Cookies → `espn.com` → copy `SWID` and `espn_s2` into
the League screen. Works everywhere, needs nothing installed.

---

## What these cookies give access to

Treat them as a password for your ESPN account. Anyone holding both can, as
you:

- read every fantasy league you are in, public or private, and every roster,
  transaction and message board in them;
- read your ESPN account profile and display name;
- **act as you** against ESPN's fantasy endpoints — set lineups, add and drop
  players, propose or accept trades, post to league chat.

Fantasy War Room only ever issues `GET` requests, but the credential itself is
not read-only. That is why it is encrypted at rest and never returned by any
endpoint.

### They expire

`espn_s2` is a session cookie tied to your ESPN sign-in. Signing out of ESPN
invalidates it immediately; it also expires on its own after roughly a year, or
sooner if ESPN rotates your session. When it stops working the app says so and
asks you to reconnect. There is nothing to "rotate" beyond signing out and
back in — that is the revocation mechanism.

---

## How we handle them

| Requirement | How |
| --- | --- |
| Never logged | `redact()` scrubs anything cookie-shaped; `RedactingFilter` is installed on the root logger at start-up, so third-party libraries cannot leak them either |
| Never in an API response | No serializer emits them. `/api/config`, `/api/config/mine`, `/api/espn/status` and `/api/health` report `swid_set: true/false` only, and a test pins the key set |
| Never committed | `.env` is git-ignored; nothing in the repo contains a real cookie |
| Encrypted at rest | Fernet (`services/secrets.py`), key held **outside** the database, so a copy of the SQLite file alone is not enough |
| Redacted from errors | `EspnHttpError` redacts on construction; the SWID sits in the fan-profile *path*, so URLs are redacted too, not just query strings |
| Not exposed to frontend JS | Write-only. Submitted once, never read back. The connect screen shows "stored", never a value |
| Deletable | **Disconnect ESPN** deletes the row outright and revokes outstanding pairing codes |
| Per-user | Each account has its own encrypted `UserEspnConfig`. Two people on one install never see each other's league |

### Disconnect

`DELETE /api/espn` (the **Disconnect ESPN** button) deletes the stored
credentials, the selected league and team, and any unredeemed pairing codes.

Imported league data — players, projections, draft picks — is deliberately left
alone. It contains no credentials, and wiping someone's draft board because
they rotated a cookie is not what a disconnect button should do. Sign out of
ESPN as well if you want the cookies dead at the source.

### Key management

The Fernet key lives beside the database (`data/secret.key`, mode `0600`) or in
`FWR_SECRET_KEY`. **Back it up.** Without it, stored credentials cannot be
decrypted — the app treats them as unset and asks you to reconnect, which is
recoverable but annoying. Set `FWR_SECRET_KEY` explicitly for any deployment
where the data directory is not durable.

---

## ESPN Draft Sync Diagnostics

`GET /api/draft/diagnostics`, plus a screen at `/diagnostics` when
`FWR_DEBUG_SCREENS=true`.

Reports the endpoint being polled, ESPN's latest pick number against ours,
response latency (last, average, max), whether new picks were detected, the
poll interval, and the last error. It is built from **counts and timings
only** — no cookies, no headers, no ESPN payloads, no player data — which is
what makes it safe to leave reachable during a live draft rather than behind a
restart. The screen is off by default because it is a testing tool, not a
feature.

Related settings:

| Variable | Default | Effect |
| --- | --- | --- |
| `FWR_DRAFT_POLL_INTERVAL` | `10` | Minimum seconds between ESPN draft polls (server-enforced) |
| `FWR_ESPN_DRAFT_SOURCE` | `auto` | `auto`, `espn_api`, or `direct` — see [the comparison doc](espn-api-comparison.md#8-decision) |
| `FWR_DEBUG_SCREENS` | `false` | Shows the diagnostics screen in the app |
