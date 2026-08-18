# Fantasy War Room

An AI-assisted draft and season-management tool for a single ESPN Fantasy Football
league. It imports **your** league's actual scoring and roster rules, builds its own
player valuations from those rules, and explains every recommendation in plain
English.

The design rule throughout: **no black boxes**. Every number on the board — the
0-100 Draft Score, the VOR, the replacement level, the probability a player is gone
by your next pick — can be traced back to a component you can read.

```
Recommended: Tobias Ibarra                       Draft Score 91.8

  1st remaining player by VOR (+230 vs RB replacement)
  Last player in the top RB tier -- a 26-pt cliff sits right behind him
  100% probability he is unavailable at our next selection (pick 24)
  QB/TE alternatives are substantially deeper (97 pts lost waiting on RB vs 2 at QB)
  Fills RB1 without creating major opportunity cost
```

---

## Get it running

### From a browser (no install) — GitHub Codespaces

Runs on GitHub's machines, gets a private HTTPS URL you can open on your phone,
and unlike most sandboxes it can reach ESPN.

**[▶ Open in a Codespace](https://github.com/codespaces/new?repo=flashtechusa/fantasy-war-room&ref=claude/fantasy-war-room-app-n6edzt)**

1. Click the link, choose **Create codespace**. It installs and starts the app
   automatically (~2 minutes the first time).
2. When the **Ports** tab shows port 8000, open its URL — that's your link.
   It's private to your GitHub account.
3. In the app, open the **League** tab, enter your league id and cookies, tap
   **Save & test connection**, then **Import league**.

The forwarded URL works from your phone as long as you're signed in to GitHub.

### On your own machine

**Requirements:** Python 3.10+. A built UI is committed, so Node is optional.

```bash
git clone https://github.com/flashtechusa/fantasy-war-room.git
cd fantasy-war-room
git checkout claude/fantasy-war-room-app-n6edzt
./scripts/start.sh
```

The script creates the virtualenv, installs dependencies, prompts for your ESPN
credentials, verifies the connection and starts the server. It prints
`http://localhost:8000` and `http://<your-lan-ip>:8000` — the second works from
your phone on the same Wi-Fi.

<details>
<summary>Windows (PowerShell)</summary>

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```
Then enter your league on the **League** tab.
</details>

### Credentials

You never have to edit a file. The **League** tab has a form for your league id,
season, SWID and espn_s2; it saves them locally, tests the connection
immediately, and never sends them back to the browser. Environment variables
(`.env`, Codespaces secrets, Docker) still work and are used as the fallback.

There is no public hosted version — the app runs on infrastructure you control,
which is what keeps your ESPN session cookies under your control.

---

## Contents

1. [Installation](#1-installation)
2. [ESPN credentials](#2-espn-credentials)
3. [Starting the application](#3-starting-the-application)
4. [Importing a league](#4-importing-a-league)
5. [Running draft simulations](#5-running-draft-simulations)
6. [Using Live Draft Mode](#6-using-live-draft-mode)
7. [Season tools](#7-season-tools)
8. [Deployment](#8-deployment)
9. [How the valuation engine works](#how-the-valuation-engine-works)
10. [Project layout](#project-layout)
11. [Testing](#testing)

---

## 1. Installation

**Requirements:** Python 3.10+ and Node 18+ (Node only for building the UI).

```bash
git clone <your-fork> fantasy-war-room
cd fantasy-war-room

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Build the frontend into the backend's static directory.
npm --prefix frontend install
npm --prefix frontend run build
```

Then copy the configuration template:

```bash
cp .env.example .env
```

`.env` is git-ignored. **Never commit it** — it holds your ESPN session cookies.

### Try it with no setup at all

The app ships with a synthetic demo league so you can explore before wiring up
ESPN:

```bash
FWR_DEMO_MODE=true uvicorn app.main:app --app-dir backend
```

Open <http://localhost:8000>, go to **League** and tap **Import league**. The demo
data is clearly labelled in the UI. The player names are invented and the
projections are generated from position-shaped curves — it is there to exercise
the app, **not** to advise a real draft.

---

## 2. ESPN credentials

### The short way: Connect ESPN

Open **League → Connect ESPN**. Hand over the two ESPN cookies once and the app
does the rest:

1. discovers every fantasy football league your ESPN account can reach —
   *"Found 3 ESPN leagues"* — so you never type a league id;
2. works out which team is yours from the cookie itself;
3. shows you the scoring, roster, waiver, playoff and draft rules it read, to
   confirm before anything is imported;
4. imports.

Credentials are encrypted before storage, never logged, never returned by any
endpoint, and deletable with **Disconnect ESPN**. Each account holds its own, so
two people on one install never see each other's league.

There is also a Manifest V3 [browser extension](browser-extension/README.md)
that collects the cookies for you: `espn_s2` is `HttpOnly`, so an extension is
the only thing in a browser permitted to read it — no bookmarklet can, and ours
says so rather than pretending. Full detail, including what these cookies can
do and how to revoke them: [docs/espn-connection.md](docs/espn-connection.md).

Manual entry stays available on the League screen and always will — it needs
nothing but a league id, so it is the path that still works if ESPN's account
endpoint changes.

### The environment-variable way

All ESPN configuration can also live in environment variables. Nothing is ever
hard-coded.

| Variable | Required | Description |
| --- | --- | --- |
| `FWR_ESPN_LEAGUE_ID` | yes | From your league URL: `…/league?leagueId=**123456**` |
| `FWR_ESPN_SEASON` | yes | e.g. `2026` |
| `FWR_ESPN_SWID` | private leagues only | The `SWID` cookie, braces included |
| `FWR_ESPN_S2` | private leagues only | The `espn_s2` cookie |
| `FWR_SECRET_KEY` | recommended | Encrypts per-account credentials. Generated beside the database if unset — back it up |
| `FWR_ESPN_DRAFT_SOURCE` | no | `auto` (default), `espn_api`, or `direct` — which path supplies live draft picks |
| `FWR_DEBUG_SCREENS` | no | Shows the ESPN Draft Sync Diagnostics screen |

The bare names are accepted too — `ESPN_LEAGUE_ID`, `ESPN_YEAR` / `ESPN_SEASON`,
`ESPN_SWID` / `SWID`, `ESPN_S2` — so credentials copied from anywhere else can be
pasted straight in. The `FWR_`-prefixed name wins if both are set. Point
`FWR_ENV_FILE` at a different file to keep a second league's config side by side.

**Public leagues** need only the league id and season.

### Verify before you trust it

```bash
python scripts/verify_espn.py
```

This connects, then prints your league's roster slots, the full scoring table,
every team and owner, player-pool coverage, and the top 15 players with **our**
projection next to ESPN's. Compare the scoring table against your league's
settings page — those rules drive every ranking in the app. Cookies are redacted
in the output, and it exits non-zero on failure, so it doubles as a smoke test.

### Finding SWID and espn_s2 (private leagues)

On a desktop browser, logged in to `fantasy.espn.com`:

1. Open DevTools → **Application** → **Cookies** → `https://fantasy.espn.com`
2. Copy the value of `SWID` — it looks like `{AABBCCDD-1122-3344-5566-AABBCCDDEEFF}`.
   Braces are added automatically if you leave them off.
3. Copy the value of `espn_s2` — a long URL-encoded string.
4. Paste both into `.env`.

These cookies expire periodically. If an import starts failing with an
authorisation message, re-copy them; the app tells you exactly that rather than
failing silently.

### Identifying your team

So the app knows which roster is yours:

```bash
FWR_MY_TEAM_ID=3            # preferred; shown on the League Settings screen
FWR_MY_TEAM_NAME="My Team"  # alternative: matches team name or owner name
FWR_MY_DRAFT_SLOT=5         # your draft position; auto-detected if ESPN publishes the order
```

**Which team is yours is usually detected automatically**: ESPN's owner ids are
the same brace-wrapped GUID as your `SWID` cookie, so a solo-owned team
identifies itself. Co-owned teams and shared logins need telling — use the
**My team** picker on the League screen. Until a team is identified, My Team,
Week, Waivers and Trade are all empty, and the League screen says so.

You can also change your draft slot from the Simulator screen at any time.

---

## 3. Starting the application

### Production-style (one process serves API + UI)

```bash
source .venv/bin/activate
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

<http://localhost:8000> — API docs at `/docs`, health at `/api/health`.

### Development (hot-reloading frontend)

Two terminals:

```bash
# Terminal 1 — API
uvicorn app.main:app --app-dir backend --reload

# Terminal 2 — UI with hot reload, proxies /api to :8000
npm --prefix frontend run dev
```

<http://localhost:5173>

### Using it from your phone

Start the server bound to `0.0.0.0`, find your machine's LAN address
(`ipconfig getifaddr en0` on macOS, `hostname -I` on Linux) and open
`http://<that-address>:8000` on the phone. Add it to your home screen — the app
is a mobile-first PWA-style layout with a sticky bottom nav.

---

## 4. Importing a league

Open **League** (the ⚙️ tab) and tap **Import league**. This pulls, in one pass:

- league name, season, team count
- every team, its owners and its current roster
- the complete scoring rule set
- starting slots, bench slots, IR slots, flex configuration
- draft type, draft order, keeper count, seconds per pick
- waiver type, FAAB budget, process days
- playoff team count, matchup length, seeding tiebreak
- this season's draft results (if drafted) and previous seasons' when ESPN has them
- the draftable player pool with raw projected stats, ADP, ownership and injuries

Everything is stored in SQLite (`data/fantasy_war_room.db` by default), so the app
keeps working if ESPN is slow or unreachable later — which matters on draft night.

**Verify the import.** The League Settings screen exists so you can confirm the
rules the engine is using. Check the scoring table and the roster slots in
particular: those two drive every ranking. If they're wrong, the board is wrong.

To refresh only ADP and injury data (rules rarely change, ADP changes daily), use
**Refresh players only**.

Equivalent API calls:

```bash
curl -X POST localhost:8000/api/league/import   # full import
curl -X POST localhost:8000/api/league/players/refresh
curl localhost:8000/api/league | jq .scoring    # verify the rules
```

---

## 5. Running draft simulations

Open **Sim** (🎲), pick your draft slot and a number of simulations, and run.

Opponents do **not** take the next name off a list. Each simulated manager samples
from the players near the current pick, weighted by distance from each player's
ADP, filtered by what that team still needs. That reproduces the two things that
make real drafts hard: players go earlier or later than ADP, and position runs
happen.

The output gives you:

- **A round-by-round strategy** derived from your league, not a preset. Something
  like `Round 1: prioritise RB/WR (72% / 28%)`, `QB sweet spot: round 5`.
- **Where waiting is safe and where it isn't** — a per-position curve of the
  expected best-available VOR at each of your picks, with the round the position
  falls off a cliff.
- **Expected final roster strength** — mean, 10th and 90th percentile projected
  starter points.
- **Realistically available players at each of your picks** — anyone on the board
  in at least half of simulations.

1,000 simulations of a 12-team, 15-round draft takes roughly two seconds.

```bash
curl -X POST localhost:8000/api/simulate \
  -H 'Content-Type: application/json' \
  -d '{"my_slot": 5, "simulations": 2000}' | jq .strategy
```

---

## 6. Using Live Draft Mode

Open **Live** (🎯). The screen is built for one-handed phone use with a pick clock
running.

**Top of screen — the clock.** Current pick, round, who is on the board, your next
pick and how many picks away it is.

**BEST PICK NOW.** The recommended player with his Draft Score, projection, VOR,
ADP, the probability he is gone by your next pick, and the reasons. One tap on
**Draft <name>** records him.

**Mark a player drafted.** Type two letters of a name, tap **Drafted**. This is the
primary path: it always works, needs no cookies, and cannot be broken by ESPN
changing an endpoint mid-draft. **Undo last** reverses a mistake.

**ESPN sync (optional).** Toggle it on and the app polls ESPN's draft board and
adds picks you haven't recorded. It never overwrites a manual entry, and the
backend rate-limits polling to one request per `FWR_DRAFT_POLL_INTERVAL` seconds
(default 10) regardless of how often the UI asks.

Two ESPN paths are consulted and whichever reports more picks wins. This matters:
the `espn-api` library suppresses picks until ESPN flags the draft as *complete*,
so on its own it reports nothing for the entire duration of a live draft. A
direct `view=mDraftDetail` read does not, and it also gives us ESPN's own
`overallPickNumber` rather than one derived from round arithmetic — which is
wrong whenever picks have been traded, and meaningless in an auction. The
evidence and the decision are in
[docs/espn-api-comparison.md](docs/espn-api-comparison.md).

**ESPN Draft Sync Diagnostics.** Set `FWR_DEBUG_SCREENS=true` and `/diagnostics`
shows which endpoint answered, ESPN's latest pick number against ours, response
latency, whether new picks were detected, and the last error. It is built from
counts and timings only — no cookies, no headers, no ESPN payloads — so it is
safe to leave open on a screen at a draft.

**Top picks right now.** The next nine players, each expandable into *why take him
now*, *why wait*, and *principal risk*.

**Position pressure.** How many startable players remain at each position, how much
value you lose by waiting, and how many picks until the current tier empties.

Every recorded pick recalculates the whole model — replacement levels stay fixed
(they're a property of the league), but scarcity, availability, opportunity cost
and roster need all move.

**My Team** (🛡️) shows your optimal starting lineup, bench, positional strength
against a typical team in your league, bye-week conflicts, remaining needs ranked
by marginal value, and a roster construction score with the reasons behind it.

---

## 7. Season tools

Once the draft is done the app's centre of gravity moves to three screens. All
three answer the same question in the same currency -- **marginal lineup
points** -- because "does this help me win?" is the only thing that matters.

### Week (start/sit)

Your optimal lineup for a given week, with the reasoning for every slot:

```
QB   Trevon Calloway    21.9   +4.2 pts over Jalen Fontaine (17.7)
RB   Marcus Boone       20.6   +20.6 pts over Tobias Jennings (0.0)
FLEX Dax Yearwood       14.2   Essentially a coin flip -- +0.5 pts over Marcus Carrington
```

It reports what the optimal lineup gains over simply starting your best
season-long players, flags bye weeks and injury designations, and separates out
**coin flips** -- calls inside the projections' margin of error, where your read
on the matchup is worth more than the number.

A player ruled `OUT`, on IR or suspended is projected at zero and will never be
started while an alternative exists.

### Waivers

Every free agent is valued by rebuilding your optimal lineup with him on the
roster and the worst droppable player gone. Two horizons, because they disagree:
a streaming defense can win you Sunday while being worthless in November.

Each target names the player to drop, a FAAB bid derived from your league's
actual budget, and a verdict: `must-add`, `starter`, `streamer`, `stash`, `depth`.
Players who improve nothing are omitted rather than padded into a top-20.

Starters and high-VOR bench players are never suggested as drops.

**Bids are scaled to what you have left, not the season budget.** ESPN doesn't
report remaining FAAB reliably, so enter yours on the **League** screen and the
suggestions adjust.

**The app never places a claim for you.** It cannot add, drop or bid — `espn-api`
is read-only and ESPN publishes no write API. You execute the moves in ESPN.

### Trade

Both sides are evaluated by lineup delta, not raw points -- trading your RB3 for
someone's WR2 can be a clear win even though you gave up more total projection,
because the RB3 was never starting. Reports this week and rest-of-season for
each side, positional changes, and warnings when a trade leaves you one injury
away from an unfillable slot.

Verdicts respect a noise floor: gaps under 3 points are reported as `neutral`
rather than dressed up as insight.

---

## 8. Deployment

### Docker

> Note: the image has not been built end-to-end yet (no Docker daemon was
> available during development) — see `TODO.md`. The frontend build stage is
> verified; expect to iterate on the runtime stage the first time.

```bash
docker compose up --build
```

The image builds the frontend, installs the backend and serves both from one
container on port 8000. Configuration comes from your `.env`; the SQLite database
is bind-mounted to `./data` so it survives rebuilds.

```bash
docker build -t fantasy-war-room .
docker run -p 8000:8000 --env-file .env -v "$PWD/data:/app/data" fantasy-war-room
```

### A small VPS or home server

The app is a single uvicorn process with a SQLite file. Put it behind nginx or
Caddy for TLS and run it under systemd:

```ini
[Unit]
Description=Fantasy War Room
After=network.target

[Service]
WorkingDirectory=/opt/fantasy-war-room
EnvironmentFile=/opt/fantasy-war-room/.env
ExecStart=/opt/fantasy-war-room/.venv/bin/uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8000
Restart=on-failure
User=fwr

[Install]
WantedBy=multi-user.target
```

### Notes before exposing it publicly

There is **no authentication** — it is built as a personal, single-league tool. If
you put it on the open internet, front it with basic auth or a private network
(Tailscale, a VPN, an SSH tunnel). Anyone who can reach it can read your league
data and trigger ESPN requests using your cookies.

If you outgrow SQLite, point `FWR_DATABASE_URL` at Postgres — nothing else changes.

---

## How the valuation engine works

Read this before trusting a recommendation. Every step is in
`backend/app/engine/`.

### Projected points (`scoring.py`)

We store the **raw projected stat line** (passing yards, receptions, sacks…) rather
than a pre-computed point total, then apply your league's scoring rules ourselves.
That is what makes the board league-specific: the same projections produce a
different board in a 0.5-PPR league than in a TD-heavy one. ESPN's own applied
total is kept alongside as a cross-check, and used as a fallback if a source gives
us no raw stats.

Tap any player and open *How the projection was scored* to see the arithmetic.

### Replacement level and VOR (`replacement.py`)

A player is worth what he produces above the guy you could have had for free at the
same position. That baseline falls out of your league's shape:

1. **Dedicated starters** — `team_count × starting slots` per position.
2. **FLEX** — the flex slots go to whoever is actually best among the flex-eligible
   leftovers, so we pool them and take the top `team_count × flex slots`. In a PPR
   league that pulls mostly WRs; in a TD-heavy league, more RBs. The projections
   decide, not a constant.
3. **Realistic bench ownership** — benches aren't filled evenly, and nobody carries
   four kickers. Bench spots are allocated greedily by provisional VOR under
   per-position carry caps.
4. **Fixed point** — steps 2-3 need VOR and VOR needs the baseline, so it iterates
   until the baseline stops moving.

Replacement values are a 3-player smoothed average around the baseline rank, so one
noisy projection can't move a whole position.

Baselines are computed for QB, RB, WR, TE, K, DST plus two FLEX numbers: the
**starter bar** (what it takes to start in the flex) and the **flex replacement**
(the best flex body who goes undrafted). They are reported separately because they
answer different questions.

### Positional scarcity (`scarcity.py`)

Tiers are found by looking for unusually large gaps in projected points within a
position — the same thing you do staring at a ranked list, done consistently. Then,
using the availability model, we compute the expected number of picks until the
current tier is ~75% gone. A cliff only matters if you're about to fall off it.

### Expected availability (`availability.py`)

The pick at which a player comes off the board is modelled as
`T ~ Normal(ADP, σ)` with `σ = 4.0 + 0.18 × ADP` — consensus on the 1.01 is tight,
consensus on the 9th-round WR is not.

Mid-draft we know more than ADP: we know he's *still on the board now*. So the
quantity used is the conditional survival probability
`P(T ≥ next_pick | T ≥ current_pick)`, which correctly says "he's lasted this long,
the odds he lasts a bit longer are better than raw ADP implies".

We also measure **market drift**: if the room is reaching relative to ADP, every
remaining player's effective ADP shifts earlier. Drift is measured from the picks
already made, damped and capped.

### Draft opportunity cost (`valuation.py`)

For each position we compute the expected best-available value at your *next* pick
(`E[best] = Σ vᵢ pᵢ Π(1-pⱼ)` over better players), and compare it to the best
available now. The difference is what you lose by waiting on that position, weighted
by whether you still need it.

Two numbers come out of that, both shown on every player:

- **Cost of waiting here** — value bled at *his* position by your next pick.
- **Opportunity cost elsewhere** — the most you'd give up at another position by
  spending this pick here. Kickers and defenses are excluded; passing on them
  costs nothing.

### Roster need (`roster.py`)

Need is **not** a checklist. Counting empty slots and reaching for the best body at
an empty position is how you end up with the QB12 in round 6. Instead we measure
*marginal value*: how much would your optimal starting lineup improve if you added
the best available player at each position — **measured against a replacement-level
player at that same position**, not against an empty slot.

That's self-limiting by construction. If you have no TE but the best available TE is
barely above replacement, the marginal value is small and the engine keeps taking
better players elsewhere. When it's genuinely your last chance to fill a starting
slot, the number spikes on its own.

Roster need is also capped at 11% of the Draft Score, so it can nudge but never
dominate.

### The Draft Score

A weighted 0-100 blend. Each sub-score is 0-100; every one is on the API response
and rendered as a bar in the player sheet:

| Component | Weight | What it measures |
| --- | --- | --- |
| `vor` | 24% | Value over this league's replacement level |
| `scarcity` | 14% | How fast usable starters are disappearing here |
| `availability` | 13% | Probability he's gone by your next pick |
| `production` | 12% | Raw projected points under your scoring |
| `roster_fit` | 11% | Marginal value to your starting lineup |
| `upside` | 9% | Realistic ceiling above the projection |
| `floor` | 9% | Downside protection |
| `adp_value` | 8% | How far he's falling relative to our own ranking |

Three documented multipliers then apply: kickers and defenses are suppressed to 32%
until the last rounds; `OUT`/IR/suspended players are cut to 75%; `DOUBTFUL` to 90%.
Anything with a multiplier carries a visible flag.

**On upside and floor:** these are *heuristic priors*, not a simulated distribution
— position volatility, injury status, projected games, role security (ownership),
and market mispricing. They're labelled as bands in the UI so they aren't mistaken
for a projection source. See `TODO.md` for what would make them better.

### Adding another projection source

The app is deliberately not hostage to ESPN's projections. `PlayerProjection` rows
are keyed by `(player, source_key)` and store raw stats; `ProjectionSource` rows
carry a blend weight. Adding FantasyPros, Sleeper or a CSV means writing an adapter
that emits `PlayerProjection` rows and inserting a source row — the blending in
`services/board.py` and all downstream scoring already handle multiple sources.

---

## Project layout

```
backend/app/
  config.py            environment configuration (no hard-coded credentials)
  models.py            SQLAlchemy schema
  espn/
    client.py          defensive wrapper over cwendt94/espn-api
    http.py            direct v3 client: discovery + live draft, timed and redacted
    discovery.py       find a user's leagues; detect which team is theirs
    draft_feed.py      parse view=mDraftDetail, including a draft in progress
    redaction.py       keeps SWID / espn_s2 out of logs and error messages
    demo.py            synthetic league + player pool
    constants.py       ESPN id/label mappings and normalisation
  engine/
    scoring.py         apply league scoring rules to raw stat lines
    league_shape.py    normalised roster construction
    replacement.py     replacement level + VOR
    scarcity.py        tiers and positional cliffs
    availability.py    ADP survival model + market drift
    draft_math.py      snake/linear pick arithmetic
    roster.py          lineup optimiser, needs, byes, roster score
    valuation.py       the board: Draft Score + explanations
    simulate.py        Monte Carlo mock drafts
  services/            provider selection, import, board caching, draft state
  api/                 FastAPI routers and serializers
browser-extension/     Manifest V3 ESPN cookie connector (proof of concept)
frontend/src/
  pages/               ConnectEspn, LeagueSettings, DraftBoard, LiveDraft, MyTeam, Simulator
  components.tsx       player cards, score bars, bottom sheet
  styles.css           mobile-first design system
tests/                 702 tests, no network or credentials required
```

### Credits

Built on [cwendt94/espn-api](https://github.com/cwendt94/espn-api) for ESPN access.
ESPN's request conventions and draft payload were cross-checked against
[mkreiser/ESPN-Fantasy-Football-API](https://github.com/mkreiser/ESPN-Fantasy-Football-API)
(LGPL-3.0) — studied, not copied; see
[docs/espn-api-comparison.md](docs/espn-api-comparison.md).
Architecture ideas from [KBThree13/mcp_espn_ff](https://github.com/KBThree13/mcp_espn_ff);
valuation concepts informed by [jjti/ff](https://github.com/jjti/ff) (VOR as the
ranking backbone) and [elliott-imhoff/optimal-adp](https://github.com/elliott-imhoff/optimal-adp)
(iterative simulation and regret-style refinement).

---

## Testing

```bash
pytest                    # 283 tests, ~50s
pytest tests/test_replacement.py -v
npm --prefix frontend run typecheck
```

The suite runs entirely against synthetic data — no ESPN credentials, no network.
It covers ESPN payload parsing and connection-error handling, league settings
parsing, scoring under multiple formats, VOR and replacement level, the ADP
availability model, snake draft pick arithmetic, roster construction, live draft
state updates, the simulator, and the HTTP API end to end.

Known unfinished work is tracked in [TODO.md](TODO.md).
