# Projection source selector — per user

Which projections build the board is a **per-user** choice, so two managers in
the same league can view it under different numbers. The choice lives on
`UserEspnConfig.projection_mode` and is resolved in
`runtime_config.settings_for_user` → `deps.engine_dep` →
`board.build_engine(active_source=mode)`.

## The four modes

| Mode | Weights used | Behaviour |
|------|--------------|-----------|
| `espn` (default) | `{espn, demo}` | The native source. **Byte-identical to the board before this option existed.** |
| `sleeper` | `{sleeper}` | Sleeper's raw component projections, re-scored under the league's rules. |
| `fantasypros` | `{fantasypros}` | The user's own FantasyPros key, re-scored under the league's rules. |
| `consensus` | `{espn, demo, sleeper, fantasypros}` | An equal-weight blend, **per player**, of whichever sources have data. |

`_resolve_weights` in `board.py` maps a mode to these weights;
`_blend_raw_stats` then blends per player. Because it filters to the sources
that actually have a projection for each player, consensus is a true "average of
what we have", never an average against zero.

## Graceful fallback (the honesty rule)

A single-source mode does **not** zero out players the source misses. When a
source has no projection for a player, `_blend_raw_stats` returns nothing and the
engine falls back to that player's ESPN applied total — so a thin source degrades
to the native numbers instead of breaking the board.

That makes coverage the thing to watch: "FantasyPros" with 13% coverage is really
ESPN underneath for the other 87%. The API never hides this — `GET
/api/league/projections/status` returns per-source `coverage` and a `warnings`
list, and the League Settings card shows both. FantasyPros' free tier truncates
to the top of each position; a full-coverage key changes what the mode is worth.

## FantasyPros key — bring your own, stored encrypted

Each user supplies their own FantasyPros key (issued per person under their own
agreement). It is stored **Fernet-encrypted** on
`UserEspnConfig.fantasypros_api_key_encrypted`, exactly as the ESPN cookies are —
never in plaintext, never returned by the API. `settings_for_user` decrypts it
only into that user's own `Settings`. `POST /api/league/projections/fantasypros/key`
stores it, imports FantasyPros immediately, and returns the coverage so the user
sees what their key actually covers before relying on it.

## API

- `GET  /api/league/projections/status` — mode, per-source coverage, key set?, warnings.
- `POST /api/league/projections/mode` — `{ "mode": "espn|sleeper|fantasypros|consensus" }`.
  Selecting Sleeper/Consensus best-effort imports Sleeper first (no key needed).
- `POST /api/league/projections/fantasypros/key` — `{ "api_key": "...", "import_now": true }`.
  Clears the key when `api_key` is empty/null.
- `POST /api/league/projections/sleeper/import` — refresh Sleeper's numbers.
- Back-compat: `GET /api/league/projections/sleeper` and
  `POST /api/league/projections/sleeper/toggle` still work, mapped onto the mode.

## Reproducing the analysis from the CLI

`scripts/team_value.py --source {espn|sleeper|fantasypros}` ranks every team under
one source using the same starting-points / roster-VORP formula, with the same
coverage reporting — the read-only investigation this selector productises.
