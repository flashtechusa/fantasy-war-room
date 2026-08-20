# Sleeper projections — an isolated comparison source

> **Update:** Sleeper is now one option in the per-user **projection source
> selector** (ESPN / Sleeper / FantasyPros / Consensus). See
> [`projection-source-selector.md`](./projection-source-selector.md). The
> engine-level guarantees below still hold; the old ON/OFF toggle is now the
> "Sleeper" choice in that selector, and its API path still works.

**Purpose: compare, before deciding whether to combine.** This adds Sleeper as a
second set of projections you can switch the whole board onto, so you can see how
its numbers rank your players against the existing source — *without* mixing the
two. It is deliberately not a blend.

## What it does

- **Off (default):** nothing changes. The board is exactly what it was — ESPN
  (and FantasyPros, if a key is configured), blended as before.
- **On:** the board is built from **Sleeper's raw component projections**
  (passing yards, receptions, …) **re-scored under your league's own rules**,
  used **exclusively**. Sleeper's own point totals (`pts_ppr` / `pts_std`) are
  discarded — the whole app scores raw stats under the connected league's rules,
  and this is no exception.

It never averages, merges, or blends Sleeper with any other source. On means
"show me the board on Sleeper alone"; off means "show me the board as before".

## Scope, on purpose

Sleeper mode re-scores **offensive skill positions (QB / RB / WR / TE)**. Kickers
and D/ST are **not** mapped: ESPN scores field goals by distance with stat ids
this integration will not guess at, and a half-mapped kicker scores worse than
one left alone. A player Sleeper doesn't map to (a kicker, a defense, or anyone
it didn't project) simply falls back to the existing projection. So the
comparison is honest — it never invents numbers it can't map correctly.

## How to use it

League screen → **Sleeper projections (optional)**:

1. **Use Sleeper Projections: OFF → ON.** The first time, it fetches Sleeper's
   projections for your season and stores them (no key needed — Sleeper's API is
   open). The board switches to Sleeper immediately.
2. The board header shows a **Sleeper projections** badge whenever it is active,
   so you always know which numbers you are looking at.
3. **Refresh Sleeper data** re-pulls the latest projections.
4. **ON → OFF** restores the default board exactly.

The toggle is **per user**, so two people in the same league can compare
independently without affecting each other.

## Guarantees (enforced by tests)

- **Off is byte-identical.** The Sleeper `ProjectionSource` is stored
  **disabled**, so it never enters the default weighted blend. With the toggle
  off, storing Sleeper data leaves every player's projected points unchanged.
  (`test_off_is_unchanged_when_sleeper_data_exists`)
- **On re-scores under your rules.** A player's projected points in Sleeper mode
  equal `LeagueScoring.score(sleeper_raw_stats)` for your league — no other
  source contributes. (`test_on_rescopes_to_sleeper_under_league_rules`)
- **Isolation.** Import stores the source disabled; only the explicit
  Sleeper-exclusive board build uses it. (`test_import_sleeper_stores_matched_rows_disabled`)

## For maintainers: where it plugs in

Every non-ESPN provider emits *raw stat lines keyed by ESPN stat id*, and the
valuation engine re-scores them under the league's `ScoringRule` rows. Sleeper is
just another such adapter — nothing in the scoring or board math changed.

| Piece | Where |
|---|---|
| Adapter (client + stat map + parser) | `backend/app/projections/sleeper.py` |
| Import into `PlayerProjection` (stored **disabled**) | `services/projections.py::import_sleeper` |
| Exclusive board build | `services/board.py::build_engine(active_source="sleeper")` |
| Re-scoring (reused, unchanged) | `engine/scoring.py::LeagueScoring.score` |
| Per-user toggle | `UserEspnConfig.use_sleeper_projections` → `runtime_config.settings_for_user` → `deps.engine_dep` |
| Source shown to the UI | `BoardResult.projection_source` → board meta `projection_source` |
| API | `GET/POST /api/league/projections/sleeper*` |

**This is the seam a future projection-source selector or weighted blend
extends.** `build_engine(active_source=…)` today picks exactly one source or the
default blend, never merges them. A selector would pass a different key; a blend
would pass weights instead of a single key. Blending is intentionally **not**
implemented yet — the point of this phase is to compare Sleeper against the
existing system first.

To confirm the live Sleeper shape from a machine with internet access, run
`python scripts/probe_sleeper.py` (read-only).
