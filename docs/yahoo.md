# Running Fantasy War Room on a Yahoo league

Everything the app does — the Draft Score, VOR, tiers, lineup optimiser, waiver
bids, trade analysis — works identically on a Yahoo league. What differs is
getting the data in, because Yahoo is not ESPN in two ways that matter:

1. **Yahoo needs OAuth, not cookies.** ESPN reads a private league from two
   browser cookies. Yahoo requires an app registered on its developer network
   and a three-legged OAuth handshake. It is more steps, but you do it once.
2. **Yahoo publishes no projections.** Not a partial set — none. Yahoo's API
   returns rosters, ownership, injury status and draft analysis (ADP), but no
   projected stat for anybody. So a Yahoo league takes its player *pool* from
   Yahoo and its *projections* from a projection source: ESPN's public
   projections by default (no credentials needed), plus FantasyPros if you have
   a key. Raw stat lines get re-scored under your Yahoo league's own rules, the
   same as everywhere else in the app.

---

## 1. Get Yahoo API access

Yahoo has two doors, and which one you get depends on the account:

- **`developer.yahoo.com/apps/create`** — the self-serve path. Create an app,
  get a Client ID and Client Secret immediately.
- **The "Apply for Yahoo Fantasy Sports API Access" form** — a review queue.
  Yahoo closes incomplete or vague submissions without replying, so answer as a
  real product with a real, narrow use case.

If you land on the application form, this is what each field is asking for, for
this app:

| Field | What to put |
| --- | --- |
| Name / Business Title / Email / Phone | Yours. Use an address you actually read — the approval arrives there. |
| Business Name & Address | Your company, or your own name and address for a personal project. |
| Consumer-Facing Product or App Name | `Fantasy War Room` |
| Brief Company Description | One or two sentences on who you are. A personal or internal tool is fine — say so rather than inflating it. |
| Website URL or App Store Details | The GitHub repository URL for this project. There is no hosted version; the app runs on the user's own machine. |
| Describe Your Intended Use Case | The field the review actually turns on. Be specific about *which* data and *why* — see the draft below. |
| Expected Users | The real number. For one league, that is a handful — pick the smallest bracket. |
| Client ID | Blank unless you already have a Yahoo Developer Network app; access is provisioned after approval otherwise. |
| Additional Notes | Confirm read-only is sufficient, and that use is limited to leagues the authenticating user is already a member of. |

A use-case description that says what the app reads and why:

> Fantasy War Room is a self-hosted fantasy football draft and season assistant.
> Each user runs it on their own machine and connects their own Yahoo account
> via OAuth; there is no hosted service and no shared data store. For the
> leagues that user is already a member of, it reads league settings and scoring
> rules, teams and rosters, draft results, and the league player pool with
> ownership and draft-analysis data. It uses those to compute value-over-
> replacement rankings under that league's exact scoring rules, recommend draft
> picks, set weekly lineups, and evaluate waiver claims and trades. Read access
> only — the app never writes to Yahoo, and every recommendation is executed by
> the user in Yahoo themselves. Expected use is personal, at the scale of a
> single league per installation.

Adjust it so it stays true of what you are actually doing; the specifics are the
point of the field.

**Redirect URI.** Yahoo asks for one when you create the app. Two workable
answers:

- `oob` — Yahoo shows you a code on screen and you paste it into the app. This
  is the default here and the only thing that works on a laptop with no public
  HTTPS address.
- `https://localhost:8000/api/yahoo/callback` — if your app registration
  requires a URL. Set `FWR_YAHOO_REDIRECT_URI` to exactly the same string; the
  app serves that callback. Yahoo insists on `https`, and browsers will warn
  about the certificate on localhost.

Whichever you choose, it must match character-for-character on both sides or
Yahoo rejects the token exchange.

---

## 2. Connect the app

In the running app, open the **League** tab:

1. **Platform** → choose **Yahoo**.
2. **Yahoo connection** → paste the Client ID and Client Secret, and the
   redirect URI if it is not `oob`. Save.
3. **Connect Yahoo** → approve access in the tab that opens, then paste the code
   Yahoo shows you back into the app and press **Finish**.
4. **Show my leagues** → pick your league from the list. No hunting for the id.
5. **Import league** (under Draft tools) → settings, teams, rosters, draft
   history, the player pool, and ESPN's public projections matched onto it.

The access token expires hourly and refreshes itself; the refresh token is
long-lived, so this is a one-time setup. Client secret and tokens live in your
local SQLite database and are never returned to the browser.

Prefer environment variables? `FWR_PLATFORM=yahoo`, `FWR_YAHOO_LEAGUE_ID`,
`FWR_YAHOO_CLIENT_ID`, `FWR_YAHOO_CLIENT_SECRET` — see `.env.example`. The
handshake still has to happen in the browser once.

---

## 3. What is different once it is running

**Projections come from ESPN.** The League screen shows the match rate. Players
ESPN's public feed did not cover have no projection and sort to the bottom;
names that differ between platforms are the usual cause, and adding a
FantasyPros key fills most of the gap.

**Scoring is translated, and the translation is shown.** The engine scores raw
stat lines by ESPN stat id, so Yahoo's categories are mapped onto them at import
(`backend/app/yahoo/constants.py`). Three things can happen, and the League
screen reports each rather than hiding it:

- *Combined* — Yahoo scores field goals in 10-yard bands (0–19, 20–29, 30–39)
  where ESPN's narrowest band is 0–39. Those collapse into one rule; if the
  bands score differently, the average is used and the collision is listed.
- *Approximate* — a few points-allowed bands have different edges (Yahoo's
  14–20 against ESPN's 14–17).
- *Unmapped* — categories with no equivalent at all, typically IDP. The rule is
  kept and listed so the scoring page is complete, but no projection source
  supplies those stats, so they contribute nothing.

For a standard offence-only league, none of this changes a ranking. For a deep
IDP league, read the translation notes before trusting the board.

**Some fields Yahoo simply does not publish**: start percentage, rookie status,
playoff round length, and a keeper flag on draft picks. They are left at their
neutral defaults rather than guessed at.

**Live draft sync** polls Yahoo's draft results as picks are made, the same as
the ESPN sync, and only ever adds picks — manual entries are never overwritten.
