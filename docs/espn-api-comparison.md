# ESPN API comparison: Fantasy War Room vs. `mkreiser/ESPN-Fantasy-Football-API`

**Purpose.** Decide whether another ESPN client knows something about live
draft synchronisation that we do not, and act on it.

**Licensing.** [`mkreiser/ESPN-Fantasy-Football-API`][ref] is **LGPL-3.0**. No
source from it has been copied into this repository. It was read to learn
*which ESPN endpoints exist and how ESPN's request conventions work* — facts
about a third-party HTTP API, not expression — and everything in
`backend/app/espn/http.py`, `draft_feed.py` and `discovery.py` was written from
scratch in Python against those facts.

**Versions reviewed.** Reference client at `main`, August 2026. Our stack:
`espn-api` 0.46.0 (Python, MIT) plus our own request layer.

[ref]: https://github.com/mkreiser/ESPN-Fantasy-Football-API

---

## 1. At a glance

| | Fantasy War Room (before this change) | Reference client |
| --- | --- | --- |
| Host | `lm-api-reads.fantasy.espn.com` | `lm-api-reads.fantasy.espn.com` |
| League route | `/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{id}` | identical |
| Pre-2018 route | `/apis/v3/games/ffl/leagueHistory/{id}?seasonId={season}` | identical |
| Auth | `Cookie: espn_s2=…; SWID=…` | identical |
| Draft source | `view=mDraftDetail`, via `espn-api` | `view=mDraftDetail`, direct |
| Live-draft method | none — `refresh_draft()` reused | none |
| League discovery | none | none |
| Player pool | `view=kona_player_info` + `x-fantasy-filter` | identical |

The two projects converged on the same endpoints independently, which is a
useful signal: there is no secret ESPN surface we were missing. The
differences that matter are in **how the draft payload is interpreted**.

---

## 2. Endpoints the reference client calls

Every call is `GET`, on the read host, with cookies in a `Cookie` header.

| Method | Route | `view=` parameters |
| --- | --- | --- |
| `getLeagueInfo` | `{season}/segments/0/leagues/{id}` | `mSettings` |
| `getTeamsAtWeek` | `{season}/segments/0/leagues/{id}` | `mRoster`, `mTeam` (+ `scoringPeriodId`) |
| `getBoxscoreForWeek` | `{season}/segments/0/leagues/{id}` | `mMatchup`, `mMatchupScore` (+ `scoringPeriodId`) |
| `getDraftInfo` | `{season}/segments/0/leagues/{id}` | `mDraftDetail`, `mMatchup`, `mMatchupScore` |
| …plus a second request | `{season}/segments/0/leagues/{id}` | `kona_player_info` with `x-fantasy-filter` `{players:{limit:3000, sortPercOwned:…}}` |
| `getFreeAgents` | `{season}/segments/0/leagues/{id}` | `kona_player_info` with `filterStatus: [FREEAGENT, WAIVERS]`, `limit: 2000` |
| `getHistoricalScoreboardForWeek` | `leagueHistory/{id}?seasonId=` | `mMatchupScore`, `mScoreboard`, `mSettings`, `mTopPerformers`, `mTeam` |
| `getHistoricalTeamsAtWeek` | `leagueHistory/{id}?seasonId=` | the same plus `mRoster` |
| `getNFLGamesForPeriod` | `site.api.espn.com/apis/fantasy/v2/games/ffl/games?dates=…&pbpOnly=true` | — |

### How it answers each question we asked

- **League settings** — one request, `view=mSettings`. It reads
  `response.settings` and merges `status.currentMatchupPeriod` and
  `status.latestScoringPeriod` in from the top level.
- **Teams** — `view=mRoster&view=mTeam`, then it joins `teams[].primaryOwner`
  against the top-level `members[]` array to attach owner identity. This is
  the same join we now use to auto-detect which team is yours.
- **Players** — `view=kona_player_info` with an `x-fantasy-filter` header
  carrying a JSON filter (limit, status, sort). Identical technique to ours.
- **Draft results** — `view=mDraftDetail`, reading `draftDetail.picks`, then a
  second `kona_player_info` request (`limit: 3000`) to attach player detail to
  each pick.
- **Draft-board / live-draft endpoint** — **none.** `getDraftInfo` is its only
  draft method and it is written for a finished draft: the paired 3000-player
  request makes it far too heavy to poll.
- **Authentication** — `espn_s2` and `SWID` in a `Cookie` header, both or
  neither. Same as ours.

---

## 3. What our implementation does today

`backend/app/espn/client.py` wraps `espn-api`, and adds its own requests where
the library loses information.

| Concern | Path | Endpoint / view |
| --- | --- | --- |
| League bootstrap | `espn-api` `get_league()` | `mTeam`, `mRoster`, `mMatchup`, `mSettings`, `mStandings` (one request) |
| Raw settings | ours, via the library's session | `mSettings` |
| Draft results | `espn-api` `get_league_draft()` | `mDraftDetail` |
| Player pool | ours | `kona_player_info` + `x-fantasy-filter` |
| Pro schedule (bye weeks) | `espn-api` | `proTeamSchedules_wl` on the *seasons* route |
| Player id → name | `espn-api` | `/players?view=players_wl` |

We already do two things the reference client does not:

- **Raw stat ids.** We keep ESPN's integer stat ids from `kona_player_info`
  rather than mapping them to names, so projections can be re-scored under any
  league's rules. The reference client maps to names, which is lossy — several
  stat ids collide on one name.
- **Weekly splits for free.** We extract `statSplitTypeId == 1` entries from
  the season payload, so per-week projections cost no extra request.

---

## 4. Payload differences that matter for the draft

Both projects read `draftDetail.picks`. They disagree about what is in it.

| Field | Reference client | Us (before) | Us (now) |
| --- | --- | --- | --- |
| `overallPickNumber` | read directly | **ignored**, derived from `(round-1)×teams + pick` | read directly, derivation only as fallback |
| `roundId` / `roundPickNumber` | read | read | read |
| `teamId` | read | read (via library) | read |
| `bidAmount` (auction) | read | read | read |
| `keeper` | read | read | read |
| `nominatingTeamId` (auction) | read | dropped | read |
| `autoDraftTypeId` | dropped | dropped | read (`auto_pick`) |
| `draftDetail.drafted` | **not** used as a gate | **used as a gate by the library** | reported, never a gate |
| `draftDetail.inProgress` | dropped | dropped | reported |

Deriving `overallPickNumber` is wrong in three real cases: traded draft picks,
auction drafts (where there is no round order), and any league whose pick order
is not a plain snake. ESPN publishes the number; we now use it.

---

## 5. Reliability concerns

### 5.1 `espn-api` returns **zero picks during a live draft** — the headline finding

`espn_api/base_league.py`:

```python
def _fetch_draft(self):
    data = self.espn_request.get_league_draft()
    # League has not drafted yet
    if not data.get('draftDetail', {}).get('drafted'):
        return
```

ESPN sets `draftDetail.drafted` when a draft **completes**. While a draft is
running, `drafted` is false and `inProgress` is true — and `picks` is populated
incrementally, pick by pick. The library discards all of it.

Every pick made during a live draft was therefore invisible to us, and the
picks all appeared at once when the draft ended. The reference client does not
have this bug: it maps `draftDetail.picks` unconditionally. That is the one
concrete thing reading it taught us.

### 5.2 `refresh_draft()` duplicates every pick on every poll

`_fetch_draft()` appends to `league.draft` and never clears it, while
`refresh_draft()` calls it again. Poll ten times and pick 1 appears ten times.
Our reconciliation deduplicates on player and pick number, so nothing corrupt
reached the database — but the list grew without bound for the whole draft and
the "picks seen" count was meaningless. Fixed in `_library_draft_picks()` by
resetting the list before refreshing.

### 5.3 Cost of the reference client's draft method

`getDraftInfo` pairs `mDraftDetail` with a `kona_player_info` request for 3000
players. That is megabytes per call — fine once, unusable as a poll. Our
`mDraftDetail`-only read is a few tens of kilobytes for a full board, because
picks carry ids rather than player objects. Names are filled in from the pool
we have already imported.

### 5.4 Shared risks

- **Cookie expiry.** `espn_s2` dies when the user signs out of ESPN. Both
  clients get a 401 and can only ask for new cookies.
- **Rate limiting.** ESPN returns 429 under aggressive polling. Our poll
  interval is server-enforced (`FWR_DRAFT_POLL_INTERVAL`, default 10s), not a
  client-side suggestion.
- **Unannounced payload changes.** Every field access on both sides is
  defensive; ours degrades a number rather than raising.

---

## 6. Does the reference client expose data we do not?

| Capability | Them | Us | Verdict |
| --- | --- | --- | --- |
| Boxscores per week (`mMatchup`/`mMatchupScore`) | yes | via `espn-api` `box_scores()` | parity |
| Pre-2018 history (`leagueHistory`) | yes | `espn-api` switches automatically; our direct client handles it too | parity |
| NFL game schedule (`site.api.espn.com` `pbpOnly`) | yes | no | **not adopted** — we get bye weeks from `proTeamSchedules_wl` already, and nothing in the app needs play-by-play |
| Positional ratings (`mPositionalRatings`) | no | available via `espn-api` | we are ahead |
| Raw stat ids | no (name-mapped) | yes | we are ahead |
| Weekly projection splits | no | yes | we are ahead |
| League discovery | no | **yes, new** (`fan.api.espn.com`) | we are ahead |
| `overallPickNumber` | yes | **yes, new** | fixed |
| Picks during a live draft | yes | **yes, new** | fixed |

**Is their draft data source different from ours?** No — both are
`view=mDraftDetail` on the same host. The difference was interpretation, not
source.

---

## 7. Is any endpoint suitable for polling during a live draft?

Assessed by payload size, freshness and cost:

| Endpoint | Size | Fresh mid-draft? | Verdict |
| --- | --- | --- | --- |
| `view=mDraftDetail` | small (ids only) | **yes** — `picks` grows as picks are made | **Best available.** What we now poll. |
| `view=mTeam&view=mRoster` | medium | yes, but rosters update after a pick, not with it | Secondary signal; also confirms a completed draft |
| `view=kona_player_info` | very large | ownership lags | Unusable as a poll |
| `view=mMatchup` / `mMatchupScore` | large | irrelevant pre-season | No |
| `site.api.espn.com` fantasy games | small | NFL games, not fantasy picks | No |

There is **no push, websocket or long-poll surface** on ESPN's public fantasy
API. ESPN's own draft room uses an internal real-time channel that is not part
of the v3 API and is not documented or stable. Polling `mDraftDetail` is the
best that is available to any client, ours or theirs.

**Measured freshness is still an open question**, which is exactly why this
change also ships *ESPN Draft Sync Diagnostics* (`GET /api/draft/diagnostics`).
It records latency, ESPN's latest pick number against ours, and whether new
picks appeared — so the next live draft answers the question with data instead
of speculation.

---

## 8. Decision

**Keep `espn-api` as the primary path. Add a direct `mDraftDetail` read as a
live-draft fallback. Do not replace anything that works.**

`EspnClient.live_draft_picks()` now consults both and takes whichever reports
**more** picks:

- Draft complete → both agree; the library answers, exactly as before.
- Draft running → the library returns nothing (§5.1); the direct read answers.
- Direct read fails → the library answers; the failure is recorded, not raised.
- Both fail → the error surfaces, as it did before.

The new path can only ever *add* picks, so adopting it cannot regress a working
install. `FWR_ESPN_DRAFT_SOURCE` pins the behaviour if it is ever needed:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Both, most picks wins |
| `espn_api` | Library only — exactly the pre-change behaviour |
| `direct` | `mDraftDetail` only |

### Fallback options, in order

1. **`view=mDraftDetail` direct** — implemented, default.
2. **`espn-api` `refresh_draft()`** — implemented, still primary for a finished
   draft.
3. **Roster diffing** (`mTeam`+`mRoster`) — not implemented. It would detect a
   pick without pick numbers or order. Worth building only if ESPN ever breaks
   `mDraftDetail`; the diagnostics endpoint will show that clearly.
4. **Manual entry** — always available, needs no cookies, and remains the path
   that cannot be broken by anything ESPN does. ESPN sync only ever *adds*
   picks; it never overwrites a manual one.

### What changed in code

| File | Change |
| --- | --- |
| `backend/app/espn/http.py` | New. Direct v3 client: cookie auth, timing, redacted errors, `mDraftDetail`, the fan profile. |
| `backend/app/espn/draft_feed.py` | New. Parses `draftDetail.picks` without the `drafted` gate; reads `overallPickNumber`. |
| `backend/app/espn/discovery.py` | New. League discovery + team auto-detection. |
| `backend/app/espn/redaction.py` | New. Keeps `SWID`/`espn_s2` out of logs and errors. |
| `backend/app/espn/client.py` | `live_draft_picks()` consults both sources; library pick list reset before refresh (§5.2). |
| `backend/app/services/draft.py` | Records every sync attempt for diagnostics. |
| `backend/app/services/draft_diag.py` | New. Counts, timings, redacted errors. No payloads. |

### Tests

`tests/test_espn_draft_feed.py` covers the parsing: picks during an in-progress
draft, `overallPickNumber` preferred over derivation, placeholder picks
ignored, auction fields, both-source arbitration, and the duplicate-poll
regression. `tests/test_espn_discovery.py` covers discovery and team detection.
None of them touch the network.
