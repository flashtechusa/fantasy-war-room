# Gap analysis: STACKED (stackedfantasy.com)

Written 19 Aug 2026 against a teardown captured 18 Aug 2026. Nothing here was
scraped; the reference is the brief we were given, and their branded names
(their composite player score, their auction metric) are theirs — the
underlying ideas are standard and named generically below.

**Read the strategic note first, because it changes how you should read the
table.**

---

## Strategic note: we are not in the same race

STACKED's stated positioning is draft-first — the draft room is the hook, the
season is retention. `docs/product-direction.md` says we are the exact
opposite: a **season-management add-on sold alongside a human drafter**. The
drafter drafts; we cover the seventeen weeks he is not paid for.

That matters because most of the table below is draft-room capability, and
competing there means fighting a venture-funded team with a data budget on
their strongest ground with a fraction of the headcount. The rows where we
should be unwilling to lose are the in-season ones — and those are the rows
where their product is thinnest (weekly emails and a lineup tool) and ours is
furthest along.

A second, blunter point: their observable weakness is that marketing shipped
ahead of product — placeholder counters, a war room labelled in progress,
invented content in a live feed. The counter to that is not more features. It
is being *correct*. We spent 18 Aug discovering that one client account
pressing Import put 330 fabricated players into a live league and moved every
team's grade by twenty points. Until that class of failure is impossible, no
feature on this list matters.

---

## Capability table

Effort is rough working days: **S** ≤ 1, **M** 2–5, **L** > 5.

### Pre-draft

| Capability | Have it? | Where in our code | Effort | Verdict |
|---|---|---|---|---|
| Rankings, tiers, projections | **yes** | `engine/valuation.py`, `engine/scarcity.py` (`assign_tiers`, `picks_until_tier_drains`) | — | **match** |
| Per-player reports with reasoning | **yes** | `valuation.py` `_explain` → `reasons`, `why_now`, `why_wait`, `risk` | — | **beat** — see "What we do that they do not" |
| Position pages with ADP | partial | `/api/players`, `DraftBoard.tsx`; no per-position landing pages | S | match |
| Head-to-head player comparison | **no** | — | S | match — trivial on top of the existing board |
| ADP movement (risers/fallers) | **no** | ADP is stored per import; no history table | M | match |
| ADP-vs-our-rankings gaps | partial | The data exists (`adp`, `draft_score`); nothing surfaces the delta | S | **beat** — cheapest real feature on this list |
| Multi-platform ADP (Sleeper/Yahoo/Underdog) | **no** | ESPN only — `espn/client.py:609` reads `averageDraftPosition` | M | **match — highest value of the pre-draft rows** |
| Best-ball pricing tool | **no** | — | M | **deliberately skip** — not our user |
| Opponent manager profiling | **no** | `HistoricalDraftPick` exists; roadmap D1–D2 | M | match — validated by them, but not a lead |
| Custom rankings / targets / fades | **no** | No user-override model at all | M | match |
| Prior-season outcome analysis | **no** | `historical_draft_picks` only | L | deliberately skip |

### Live snake draft

| Capability | Have it? | Where in our code | Effort | Verdict |
|---|---|---|---|---|
| Live pick feed | partial | `espn/draft_feed.py` (direct `mDraftDetail`) + `espn-api`, arbitrated in `espn/client.py` `live_draft_picks` | — | **unproven — see open questions** |
| Composite 0–100 player score | **yes** | `valuation.py` — `draft_score` with exposed component contributions | — | **beat** |
| VOR under real league scoring and slots | **yes** | `engine/scoring.py` (raw stat ids re-scored), `engine/replacement.py` | — | **match** |
| Superflex re-pricing | **yes** | `engine/league_shape.py` `is_superflex`, flex demand in `replacement.py` | — | match |
| Availability % at your next pick | **yes** | `engine/availability.py` — analytic survival on ADP + measured market drift | — | **beat on method** |
| Board overlays on future picks | **no** | We answer for *our* next pick only | M | match |
| Stack / playoff-schedule correlation | **no** | — | M | deliberately skip for now |
| Opponent roster-need overlay | partial | `roster.py` `positional_needs` runs for our roster; not others' | S | match |
| Queue, watchlist, cheat sheet | **no** | — | S | match |
| Auto-pick, in-room chat | **no** | — | M | **deliberately skip** — auto-pick is a write |
| Zero-install draft entry (URL convention) | **no** | — | M | **borrow the mechanic** — see move #4 |

### Live auction draft

| Capability | Have it? | Where in our code | Effort | Verdict |
|---|---|---|---|---|
| Auction support at all | **no** | `engine/draft_math.py:20` treats everything but LINEAR as a snake | L | **deliberately skip** |
| Auction value-over-replacement-team | **no** | — | L | deliberately skip |
| Live price targets, proxy bidding, per-team budgets | **no** | — | L | deliberately skip |

Auction is a coherent, well-executed product wedge for them and a large build
for us with zero overlap with the season product. Skip the whole block unless
a paying league asks.

### In-season — the rows that matter

| Capability | Have it? | Where in our code | Effort | Verdict |
|---|---|---|---|---|
| Start/sit optimiser | **yes** | `engine/weekly.py`, `/api/season/lineup`, `Week.tsx` | — | **match** |
| Waiver recommendations | **yes** | `engine/waivers.py` — includes FAAB bid sizing from the league's real budget | — | **beat** |
| Trade evaluation | **yes** | `engine/trades.py` — scores by *starting lineup* change, both sides | — | **beat** |
| League sync, connected once | partial | ESPN only; `espn/discovery.py`, `services/espn_connect.py`, per-user encrypted cookies | — | match |
| Standings / matchups | **no** | Roadmap C1–C4 | M | match |
| Power rankings across the league | **yes** | `/api/team/league`, `PowerRankings.tsx` | — | match |
| Weekly team emails | **no** | `engine/alerts.py` + `engine/schedule.py` detect and schedule; **nothing delivers** | M | **match — this is move #1** |
| Portfolio / multi-league exposure | **no** | `UserEspnConfig.espn_league_id` is a single league per user | M | deliberately skip for now |
| Survivor pool optimiser | **no** | — | M | deliberately skip |

### Content, AI, and platform

| Capability | Have it? | Where in our code | Effort | Verdict |
|---|---|---|---|---|
| Podcast/video/article/tweet ingestion | **no** | — | L | **deliberately skip** |
| Trending players from sentiment | **no** | — | L | deliberately skip |
| Hosted MCP server | **no** | Nothing MCP-related exists in the repo | M | match — see move #5 |
| Decision-shaped tools (not row dumps) | **yes, as HTTP** | `/api/team`, `/api/season/lineup`, `/api/season/trade`, `/api/season/waivers` already return decisions with reasoning | S | **beat** — the hard half is built |
| Read-only guardrail | **yes** | `docs/product-direction.md`; no write path exists anywhere | — | **match, and ours is structural** |
| Film library | **no** | — | — | **deliberately skip — licensed NFL content, not reachable** |
| Mobile app | **no** | Mobile-first web, no PWA manifest | M | deliberately skip |

---

## What we do that they do not

Short list, honestly. Three of these are real; the rest is parity.

1. **Transparency as an architectural property, not a feature.** `valuation.py`
   opens with "nothing here is a black box" and means it: every contribution to
   the 0–100 score is a named component with its own weight and sub-score, and
   the API returns them. They surface two or three generated reasons. Ours can
   answer "why" for every number on every screen because the decomposition is
   the data structure. For a product sold *through* an expert to his clients,
   being able to show the working is the differentiator, not the score.

2. **Per-source projections with a coverage guard.** `services/projections.py`
   refuses to blend a source covering less than half the pool
   (`MIN_COVERAGE`), and `routes_players.py` shows each source separately
   rather than only a blend. A provider that truncates its response cannot
   silently distort our rankings. This is a correctness discipline, and after
   18 Aug it is one we should be loud about.

3. **A reseller-shaped account model.** owner / partner / client roles,
   invite-only, no public signup, per-user encrypted ESPN credentials
   (`services/secrets.py`, `services/runtime_config.py`). Built for a drafter
   to hand accounts to his clients. That is a business-model fit, not a
   feature, and it is not something a direct-to-consumer subscription product
   would build.

4. *(Marginal)* **Full-draft simulation with modelled opponent behaviour**
   (`engine/simulate.py`) — they simulate availability; we simulate whole
   drafts to benchmark an outcome. Different tool, narrower use.

Everything else on our side is parity or behind.

---

## Data dependencies

| Need | What we source today | What matching would take | Cost / legal shape |
|---|---|---|---|
| Season projections | ESPN raw stat lines by stat id (`espn/client.py`); FantasyPros optional, BYO key | A second commercial-safe source | **FantasyPros free tier is personal, non-commercial, and truncates to ~10 per position. It cannot ship inside anything the drafter sells.** Their paid tier is the fix, or drop it. |
| ADP | ESPN only, one number per player | Sleeper ADP is public JSON, no key, no auth. Yahoo needs OAuth. Underdog would need scraping. | **Sleeper is free and clean — the cheapest accuracy win available.** Underdog: don't scrape. |
| Expert consensus rankings | FantasyPros, same constraint as above | — | Same licensing problem. Treat ECR as blocked until there is a paid agreement. |
| Trade values | **None** — ours are model-derived from projections | FantasyCalc publishes a public API, synced daily | Free tier exists; check terms before resale. Model-derived values are defensible and arguably better for a league with custom scoring. |
| Betting markets | **None** | A paid odds API, or scraping | Paid, per-call. Real money for marginal projection lift in redraft. **Skip.** |
| Player headshots | **None** | ESPN or Sleeper CDN | Hotlinking a CDN is fragile and rude. Low value. |
| Film | **None** | Licensed NFL broadcast/sideline/end-zone footage | Not reachable at any price we would pay. **Permanent skip.** |
| Content signals | **None** | YouTube API, podcast transcription, X API | X API pricing alone makes this a five-figure annual line. **Permanent skip.** |

**The single most important line in this table** is ADP. Our availability
model, market-drift measurement, and a weighted component of every Draft Score
all run on one number from one provider — and ESPN's ADP reflects ESPN's own
drafts, which is a narrow and unrepresentative population. Adding Sleeper ADP
is small, free, unencumbered, and improves numbers on every screen.

---

## Platform integration: what is actually reachable

**Today.** ESPN only, read-only, via `espn-api` 0.46.0 plus our own direct HTTP
path (`espn/http.py`, `espn/draft_feed.py`). We have league discovery from the
fan profile (`espn/discovery.py`), a guided Connect flow
(`services/espn_connect.py`), pairing codes, and a Manifest V3 extension whose
only job is reading the two `HttpOnly` session cookies (`browser-extension/`).
Credentials are Fernet-encrypted per user. **No Sleeper. No Yahoo.**

**Is live bidirectional draft sync reachable from where we are? No — and half
of it we should not want.**

Split it in two:

- *Reading* a live draft: **built, unproven.** `live_draft_picks` consults
  `espn-api` and a direct `view=mDraftDetail` read and takes whichever reports
  more picks, because the library suppresses picks until ESPN flags the draft
  complete. Every test is offline. `docs/roadmap.md` still lists live draft
  sync under known debt, and the honest status is "rewritten, never run against
  a real live draft." Drafts happen once a year, so this cannot be
  opportunistically verified.

- *Writing* picks back: **not reachable, and refused by design.** `espn-api` is
  read-only — there is no POST helper in the library at all. Submitting a pick
  means hand-rolling ESPN's private transaction endpoint, and
  `docs/product-direction.md` rules out writes on liability grounds that have
  nothing to do with difficulty: irreversible actions taken with someone else's
  credentials.

So the honest answer is that the *URL-takeover mechanic* is reachable and worth
borrowing, but only the read half. A route like
`/draft/espn/{leagueId}` that opens our war room against a live draft with no
install is achievable with what we have. Picks flowing back to ESPN is not, and
should stay not.

Yahoo is a separate OAuth build (~3–5 days) and we already have one prospective
user on Yahoo. Sleeper's read API is public and unauthenticated — materially
easier than either.

---

## Roadmap items this teardown changes

Against `docs/roadmap.md`, which is now substantially stale:

- **Header status is wrong.** It says "gated by a single shared password in
  Caddy. One league, one user, no accounts." All three are false as of 18 Aug.
- **B1–B12 (accounts) — done**, including B9/B10, which the roadmap flagged as
  the sleeper risk. It was right to.
- **E4 (Windows update path) — done.** Zip-based, no git dependency.
- **D4 "a second projection source" — done, wrong target.** FantasyPros landed,
  but its free tier is non-commercial and truncated. The teardown reframes the
  need: **ADP diversity matters more than projection diversity**, and Sleeper
  ADP has none of the licensing problems. Re-scope D4.
- **D2 (owner draft tendencies) — validated, demoted.** They ship the same idea.
  It is no longer a differentiator to lead with, but it is confirmed as
  something a real product invests in. Keep it after the season work.
- **F "live draft sync does not work" — rewritten, still unverified.** Update
  the wording; do not mark it done.
- **Newly obsolete: nothing.** Nothing in our roadmap is made pointless by this
  teardown.
- **Newly permanent skips, so they never enter the roadmap:** film library,
  content ingestion, betting-market projections, auction drafting, best-ball
  pricing, survivor pools.

Not in the roadmap and should be: **CI**, **backups (E1 is listed but not
done)**, and an in-app data integrity check. The 18 Aug incident was found by a
script written after the fact.

---

## How the URL takeover actually works, and what it costs

This was the question the teardown was commissioned to answer, so it gets its
own section. There is no mechanism here we do not already have.

### The claim, split into three platforms

"Type the site name in front of your draft URL and you are in our war room,
no install, picks syncing live" is one sentence describing three very
different engineering situations.

**Sleeper — genuinely zero-auth.** Sleeper's read API is public and
unauthenticated. Draft picks for any draft id are readable by anyone with no
key, no cookie and no OAuth. A URL convention is all that is needed, because
the draft id in the host URL is the only input. This is why their headline
example is a Sleeper URL, and it would be true of anybody's implementation.

**ESPN — depends entirely on whether the league is public.**

  * *Public league*: no credentials at all. A league id is sufficient. We can
    already do this — `EspnHttpClient._cookie_header()` returns an empty dict
    when no credentials are set, and anonymous requests are a first-class path
    through `espn/http.py`. `espn-api` behaves the same way; it only demands
    cookies after a 401 (`espn_requests.py`: "espn_s2 and swid are required"
    is raised on the access-denied branch, not up front).
  * *Private league*: `SWID` and `espn_s2` are required, and **`espn_s2` is an
    `HttpOnly` cookie**. No page script, bookmarklet, URL convention or
    same-site trick can read it — that is a browser security guarantee, not an
    ESPN policy, and it applies to them exactly as it applies to us.

**Yahoo — three-legged OAuth.** Registered app, consent screen, refresh
tokens. No cookie shortcut exists. This is the most work of the three and
nothing about a URL convention changes it.

### So what is the trick?

The URL takeover is **routing convenience, not an authentication method**. Note
that the teardown lists it *and* "League Sync, connected once, all season" as
separate capabilities. Those are two mechanisms: credentials are established
once in a normal connect flow, and the URL convention is a fast path into a
draft room for an account that is already connected — plus a genuinely
credential-free path for public leagues and for all of Sleeper.

Read that way, it is a good idea we should borrow, and it is cheap. What it is
not is a way around `HttpOnly`.

### Our browser extension is not the wrong call

It solves a different problem: capturing `espn_s2` for a **private** league
without asking a non-technical client to open developer tools. That is exactly
the drafter's clientele. `chrome.cookies` is the only browser interface that
can read an `HttpOnly` cookie, which is stated in `browser-extension/README.md`
and is still true. Keep it.

### What is reachable for us today

A route like `/draft/espn/{leagueId}` that tries anonymously first and falls
back to "this league is private, connect ESPN" is a **small** build on parts
that already exist:

| Piece | Status |
|---|---|
| Anonymous ESPN HTTP | `espn/http.py` — supported, **untested** |
| Read a league cold from an id | `espn/discovery.py` `league_preview` — takes any client, credentialed or not |
| Live draft board | `espn/draft_feed.py` `fetch_draft_snapshot` (`view=mDraftDetail`) |
| Valuation with no account | `services/board.py` `build_engine` needs only a `League` row |

The gap is not capability, it is that **no test covers the anonymous path** and
no route exposes it. That is the actual work.

This is also the best demo surface we could have: someone in a public league
pastes a league id and sees a working war room with no account, no cookies and
no extension — which is a far better first touch than the current
sign-in-then-connect sequence.

### One claim to treat sceptically

"Picks sync live in **both** directions" implies writing picks back to the host
platform. For ESPN that is not possible with `espn-api` — the library has two
request helpers, `league_get` and `get`, and no POST path of any kind. It would
mean hand-rolling ESPN's private transaction endpoint. Sleeper does not expose
public writes either. Given the same page carries placeholder social-proof
counters and a war room labelled in progress, treat the bidirectional claim as
unverified rather than as a capability to match. We should not match it in any
case: `docs/product-direction.md` refuses writes on liability grounds.

---

## Open questions

Things this analysis depends on that the repo cannot answer:

1. **Is the drafter actually reselling, and under what terms?** This decides
   whether the FantasyPros dependency is a licensing problem or a non-issue.
2. **What platforms are his clients on?** One prospective user is already on
   Yahoo. If a meaningful share are, Yahoo OAuth outranks everything in the
   table above and this analysis is premature.
3. **Has the rewritten live-draft path ever run against a real live ESPN
   draft?** Every test is offline. If not, treat every draft-room row as
   theoretical.
4. **Is there a paid tier inside the app at all?** Billing is on Fiverr and
   `product-direction.md` says the app only answers "is this account on for
   this season." Season entitlement (B7) is unbuilt.
5. **How many real users exist today?** The answer changes whether the next
   move is features or reliability.
6. **What did the drafter say to the partnership pitch?** The whole product
   direction rests on that channel existing.
