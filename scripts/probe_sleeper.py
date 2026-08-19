#!/usr/bin/env python3
"""Read-only probe of Sleeper's API -- run it where the network can reach it.

Fantasy War Room's sandbox cannot reach `api.sleeper.com` (egress-blocked), so
this exists to be run from the VPS (or any machine with plain internet) to
confirm, against the live API, two things we care about before building a
Sleeper lane:

  1. The *documented, open* endpoints answer with no credentials at all
     (state, a user's leagues, a league's settings/rosters).
  2. Whether the *undocumented* projections/stats endpoints still return
     projected fantasy points, and in what shape.

It only reads. It sends no cookies, no keys, and writes nothing back. It prints
structure and a few sample values, never large payloads.

    python scripts/probe_sleeper.py
    python scripts/probe_sleeper.py --username YOUR_SLEEPER_NAME
    python scripts/probe_sleeper.py --league-id 123456789012345678

Nothing here is imported by the app; it is a standalone diagnostic using only
the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.sleeper.com"
TIMEOUT = 25


def get(path: str, params: dict | None = None):
    """GET path (+ optional query) and return (status, parsed-json-or-None)."""
    url = BASE + path
    if params:
        # position[]=QB&position[]=RB needs repeated keys; urlencode(doseq) does that.
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "fwr-sleeper-probe"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:  # noqa: BLE001 - a probe should report, not raise
        print(f"    request failed: {exc}")
        return 0, None


def point_keys(stats: dict) -> list[str]:
    """The fantasy-point fields we would actually consume, if present."""
    return [k for k in ("pts_ppr", "pts_half_ppr", "pts_std") if k in (stats or {})]


def show_projection_sample(rows) -> None:
    if isinstance(rows, dict):  # some weeks come back keyed by player id
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows:
        print("    (no rows)")
        return
    print(f"    rows: {len(rows)}")
    for row in rows[:3]:
        player = row.get("player") or {}
        name = " ".join(
            p for p in [player.get("first_name"), player.get("last_name")] if p
        ) or row.get("player_id", "?")
        stats = row.get("stats") or {}
        pk = point_keys(stats)
        pts = {k: stats.get(k) for k in pk}
        component = [k for k in ("pass_yd", "pass_td", "rush_yd", "rec", "rec_yd") if k in stats]
        print(
            f"      {name:<22} {player.get('position','?'):<3} "
            f"points={pts or 'NONE'}  components={component[:5]}"
        )
    # Verdict: does this endpoint actually give us projected points?
    any_points = any(point_keys((r.get('stats') or {})) for r in rows[:25])
    print(f"    => projected fantasy points present: {'YES' if any_points else 'NO'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default="", help="A Sleeper username (for league discovery).")
    ap.add_argument("--league-id", default="", help="A Sleeper league id (for settings/rosters).")
    ap.add_argument("--week", type=int, default=1)
    args = ap.parse_args()

    # --- 1. Documented, open: current NFL state (no auth) -------------------
    print("[1] GET /state/nfl  (documented, open)")
    status, state = get("/state/nfl")
    print(f"    HTTP {status}")
    if not state:
        print("    Could not reach Sleeper. Nothing else will work; stop here.")
        return 1
    season = str(state.get("season") or state.get("league_season") or "2024")
    week = args.week or int(state.get("week") or 1)
    print(f"    season={season}  week={state.get('week')}  season_type={state.get('season_type')}")

    # --- 2. Undocumented: weekly projections -------------------------------
    print(f"\n[2] GET /projections/nfl/{season}/{week}  (UNDOCUMENTED)")
    status, proj = get(
        f"/projections/nfl/{season}/{week}",
        {"season_type": "regular", "position[]": ["QB", "RB", "WR", "TE"], "order_by": "pts_ppr"},
    )
    print(f"    HTTP {status}")
    show_projection_sample(proj)

    # --- 3. Undocumented: weekly actual stats (same shape) -----------------
    print(f"\n[3] GET /stats/nfl/{season}/{week}  (UNDOCUMENTED)")
    status, stats = get(
        f"/stats/nfl/{season}/{week}",
        {"season_type": "regular", "position[]": ["QB", "RB", "WR", "TE"]},
    )
    print(f"    HTTP {status}")
    if isinstance(stats, (list, dict)):
        n = len(stats)
        print(f"    rows: {n} (actual results, same stat shape as projections)")

    # --- 4. Documented: league discovery from a username -------------------
    if args.username:
        print(f"\n[4] GET /user/{args.username} then /user/<id>/leagues/nfl/{season}  (documented)")
        status, user = get(f"/user/{urllib.parse.quote(args.username)}")
        print(f"    user lookup HTTP {status}; user_id={user.get('user_id') if user else None}")
        if user and user.get("user_id"):
            status, leagues = get(f"/user/{user['user_id']}/leagues/nfl/{season}")
            print(f"    leagues HTTP {status}; count={len(leagues) if isinstance(leagues, list) else 0}")
            for lg in (leagues or [])[:5]:
                print(f"      {lg.get('league_id')}  {lg.get('name')}  "
                      f"teams={lg.get('total_rosters')}  status={lg.get('status')}")

    # --- 5. Documented: one league's settings + rosters --------------------
    if args.league_id:
        print(f"\n[5] GET /league/{args.league_id} (+ /rosters, /users)  (documented)")
        status, lg = get(f"/league/{args.league_id}")
        print(f"    league HTTP {status}")
        if lg:
            scoring = lg.get("scoring_settings") or {}
            print(f"      name={lg.get('name')}  teams={lg.get('total_rosters')}")
            print(f"      roster_positions={lg.get('roster_positions')}")
            print(f"      scoring keys: {len(scoring)} (e.g. "
                  f"{ {k: scoring[k] for k in list(scoring)[:6]} })")
        status, rosters = get(f"/league/{args.league_id}/rosters")
        if isinstance(rosters, list) and rosters:
            r = rosters[0]
            print(f"      rosters: {len(rosters)}; sample owner_id={r.get('owner_id')} "
                  f"players={len((r.get('players') or []))}")
            print("      (owner_id maps to the connecting user_id -> verifiable team "
                  "ownership, no cookies)")

    print("\nDone. Sections [2]/[3] are the undocumented ones; [1]/[4]/[5] are the "
          "open documented API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
