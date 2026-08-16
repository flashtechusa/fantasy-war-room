# TODO

Known gaps, deferred work and honest caveats. Phases 1-7 are implemented and
tested; this is what is *not* finished.

---

## Verified against ESPN

**The single most important open item.** Every ESPN code path has been written
against ESPN's documented payload shapes and is unit-tested with realistic
fixtures, but it has **not been run against a live ESPN league** — this repo was
built without league credentials.

A live attempt was made with real credentials for league `11507` (2026). The
config layer resolved them correctly (SWID normalised, private auth detected),
but the request never left the machine: the development environment's egress
proxy denies `lm-api-reads.fantasy.espn.com` by policy (HTTP 403 on CONNECT).
Nothing about the credentials or the client code was disproven — the network
path simply wasn't available. **Run `python scripts/verify_espn.py` on your own
machine to close this out.**

Before drafting, run that script and then check the League Settings screen:

- [ ] Scoring rules match your league exactly (compare against ESPN's settings page)
- [ ] Starting slots, bench and IR counts are right
- [ ] Team names, owners and the draft order look correct
- [ ] Player pool is populated, ADP looks sane, bye weeks are filled in
- [ ] Previous draft results appear (if your league has history)

Fixture-level parsing already caught one real bug (ESPN's QB lineup slot id is
`0`, and a falsy-zero coercion was silently dropping it), so treat the first live
import as a verification step, not a formality.

Specific things most likely to need adjustment against real data:

- [ ] **Bye weeks** are derived by finding the mid-season week missing from a pro
      team's schedule. If ESPN's `proTeamSchedules_wl` view shifts, byes go blank
      (the app degrades gracefully — `bye_week` is nullable — but the bye-conflict
      panel goes quiet).
- [ ] **Projected games** is inferred from `appliedTotal / appliedAverage`. If
      ESPN omits `appliedAverage`, it falls back to 17.
- [ ] **Auction drafts** are not modelled. `draft_type` is read and stored, and
      snake/linear pick maths is correct, but there is no auction budget/nomination
      logic. An auction league will get a coherent value board and useless pick
      timing.
- [ ] **Keepers** are read (`keeper_count`, and keeper flags on historical picks)
      but not removed from the draftable pool. In a keeper league, mark kept
      players as drafted before you start.
- [ ] **IDP / non-standard positions** (LB, DB, DL, HC) are parsed into
      `all_slot_counts` and shown on the settings screen, but the valuation engine
      only reasons about QB/RB/WR/TE/K/DST. An IDP league will get a board that
      ignores the defensive half of its roster.

---

## Engine

- [ ] **Upside and floor are heuristic priors, not distributions.** They come from
      position volatility constants, injury status, projected games, ownership as a
      role-security proxy, and market mispricing. They are labelled as bands in the
      UI, but they are the least rigorous numbers in the app. Better: per-player
      outcome distributions from historical rank-to-rank transitions, or a
      projection source that publishes ceiling/floor directly.
- [ ] **Position volatility constants** (`POSITION_VOLATILITY` in `valuation.py`)
      are reasonable priors, not fitted values. Fit them from historical
      season-over-season variance.
- [ ] **ADP sigma** (`SIGMA_BASE`, `SIGMA_SLOPE` in `availability.py`) is likewise a
      sensible shape rather than a fitted one. ESPN does not publish ADP standard
      deviation; fitting from a few seasons of real draft results would.
- [ ] **Bench carry caps** (`BENCH_CARRY_CAP` in `replacement.py`) model "realistic
      bench ownership" with constants. Deriving them from your league's own draft
      history would make replacement levels genuinely league-specific rather than
      format-specific.
- [ ] **Score weights** are fixed. Making `SCORE_WEIGHTS` configurable from the UI
      (with a live preview of how the board reorders) would suit managers who
      weight upside or floor differently.
- [ ] **Opportunity cost is a two-pick lookahead**, not a full dynamic program.
      That is deliberate — it stays explainable — but a deeper search would value
      positional runs more accurately in the middle rounds.
- [ ] **Tier detection** uses a gap threshold. It's stable and legible, but a
      proper clustering pass (or a gap statistic) would handle flat positions
      better.

---

## Simulator

- [ ] Opponents have no *team identity*: they don't have tendencies, favourite
      positions or the "always drafts a QB early" guy your league definitely has.
      League transaction history is imported and could be mined for this.
- [ ] The simulator's in-sim strategy for our own team is a fast approximation of
      the live engine (VOR with a need bonus), not the full Draft Score. They agree
      on priorities but can diverge on specific picks.
- [ ] No support for simulating *from a partially completed draft* — it always
      starts from an empty board. Mid-draft "what happens if I take X" would be
      genuinely useful.
- [ ] Simulations run synchronously in the request. 5,000 sims is ~10s; anything
      larger should move to a background task with progress reporting.

---

## Phase 8 — season tools (architected, not built)

The data model and service layer were built with these in mind — `LeagueTeam.roster`,
`HistoricalDraftPick`, the projection-source registry and the lineup optimiser are
all reusable — but none of the following is implemented:

- [ ] Start/sit optimizer (the lineup optimiser in `roster.py` is the core of it;
      needs weekly rather than season projections)
- [ ] Waiver wire recommendations and free-agent rankings (`League.free_agents()`
      in espn-api is the data source; VOR machinery already applies)
- [ ] Trade analyzer (needs a two-sided lineup-delta comparison)
- [ ] Weekly opponent analysis
- [ ] Power rankings (espn-api exposes a two-step dominance implementation)
- [ ] Playoff probability (Monte Carlo over the remaining schedule)
- [ ] Roster-strength comparison across the league
- [ ] League transaction history browser
- [ ] Owner tendencies (from draft history + transactions)

Weekly projections are the main missing input for most of these — the current
importer stores season totals only.

---

## Application

- [ ] **No authentication.** Intentional for a personal tool, but it means the app
      must not be exposed publicly without a proxy in front of it. Anyone who can
      reach it can trigger ESPN requests using your cookies.
- [ ] **Single active league.** The schema supports several (`League` is keyed on
      league id + season) but the UI and `get_active_league` assume one.
- [ ] **Response models are serializer functions, not Pydantic models.** Request
      bodies are validated; responses are hand-built dicts. The OpenAPI schema at
      `/docs` is therefore thinner than it could be.
- [ ] **ESPN draft sync is polling.** Rate-limited server-side to one request per
      `FWR_DRAFT_POLL_INTERVAL` seconds and safe to leave on, but there is no
      websocket/push path. Manual entry remains the reliable path during a draft.
- [ ] **No migrations.** Schema changes need the SQLite file deleted and a
      re-import. Add Alembic before the schema stabilises around season tools.
- [ ] The board rebuilds fully on every request. Fast enough at ~330 players
      (tens of milliseconds), but a 1,000+ player pool during a live draft would
      benefit from caching the per-player static components.

---

## Build & deployment

- [ ] **The Docker image has not been built.** No Docker daemon was available in
      the environment this was developed in. The frontend build stage was verified
      in isolation (a clean `npm ci && npm run build` in the same directory layout
      lands the bundle where stage 2 copies it from), and the runtime stage is a
      standard `pip install .` + uvicorn, but `docker compose up --build` is
      unproven. Expect to iterate on it the first time.
- [ ] No CI. A GitHub Actions workflow running `pytest` and
      `npm run typecheck` on push would be a cheap win.

---

## Testing

- [ ] No end-to-end browser tests. The UI was verified manually at an iPhone
      viewport; there is no Playwright suite guarding against regressions.
- [ ] No test exercises a real ESPN response captured from a live league. Adding
      one recorded fixture (with credentials scrubbed) would close the biggest
      remaining gap in coverage.
- [ ] Frontend has type-checking (`npm run typecheck`) but no unit tests.
