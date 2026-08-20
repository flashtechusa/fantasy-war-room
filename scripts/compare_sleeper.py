#!/usr/bin/env python3
"""Read-only: Sleeper projections vs ESPN, re-scored under YOUR league's rules.

Run this on the VPS, from the install directory, against a league you have
already imported. It:

  1. loads your imported league and its actual scoring rules from the app DB,
  2. fetches Sleeper's live season projections (public, key-less),
  3. re-scores Sleeper's raw component stats under those same rules -- using the
     app's own scoring engine and player matcher, not a reimplementation,
  4. prints Sleeper-rescored vs ESPN side by side for the top players, with a
     component breakdown and an overall ratio.

The point is to sanity-check magnitudes before trusting the feature: re-scored
under identical rules, Sleeper and ESPN should be in the same ballpark. A
systematic ~2x gap means a bug (a per-game vs season mix-up, a double-counted
stat, or a bad player match) -- which is exactly what this is here to catch.

It writes NOTHING: no projections are stored, no toggle is changed. Pure read.

    cd C:\\FantasyWarRoom
    .venv\\Scripts\\python.exe scripts\\compare_sleeper.py
    .venv\\Scripts\\python.exe scripts\\compare_sleeper.py --league-id 11507 --season 2026 --top 40
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import get_session_factory  # noqa: E402
from app.models import League, Player, PlayerProjection  # noqa: E402
from app.projections.matching import Candidate, PlayerMatcher  # noqa: E402
from app.services.board import league_scoring  # noqa: E402

SLEEPER_BASE = "https://api.sleeper.com"
POSITIONS = ("QB", "RB", "WR", "TE")

#: Sleeper stat name -> ESPN stat id. Kept identical to the Sleeper adapter;
#: offensive skill only, so K/DST are not compared here.
STAT_MAP = {
    "pass_yd": 3, "pass_td": 4, "pass_int": 20, "pass_2pt": 19, "pass_att": 0, "pass_cmp": 1,
    "rush_yd": 24, "rush_td": 25, "rush_2pt": 26,
    "rec": 53, "rec_yd": 42, "rec_td": 43, "rec_2pt": 44,
    "fum_lost": 72,
}
#: For a readable component breakdown.
STAT_LABEL = {
    "3": "passYd", "4": "passTD", "20": "int", "24": "rushYd", "25": "rushTD",
    "42": "recYd", "43": "recTD", "53": "rec", "19": "pass2pt", "26": "rush2pt",
    "44": "rec2pt", "72": "fumLost", "0": "passAtt", "1": "passCmp",
}


def sleeper_get(season: int, week: int | None) -> list[dict]:
    path = f"/projections/nfl/{season}" if week is None else f"/projections/nfl/{season}/{week}"
    params = [("season_type", "regular"), ("order_by", "pts_ppr")]
    params += [("position[]", p) for p in POSITIONS]
    url = f"{SLEEPER_BASE}{path}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "fwr-compare"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Sleeper returned HTTP {exc.code}. Cannot compare.")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Could not reach Sleeper: {exc}")
    return list(data.values()) if isinstance(data, dict) else data


def sleeper_raw(stats: dict) -> dict[str, float]:
    raw: dict[str, float] = {}
    for name, stat_id in STAT_MAP.items():
        if name in stats:
            try:
                v = float(stats[name])
            except (TypeError, ValueError):
                v = 0.0
            if v:
                raw[str(stat_id)] = raw.get(str(stat_id), 0.0) + v
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", type=int, default=None)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--week", type=int, default=None, help="Omit for season totals.")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    session = get_session_factory()()

    q = session.query(League)
    if args.league_id:
        q = q.filter(League.espn_league_id == args.league_id)
    if args.season:
        q = q.filter(League.season == args.season)
    league = q.order_by(League.imported_at.desc()).first()
    if league is None:
        print("No imported league found. Import a league first (or pass --league-id/--season).")
        return 1

    scoring = league_scoring(league)
    print(f"League: {league.name}  season={league.season}  source={league.source}")
    print(f"Scoring: {scoring.format_label}\n")

    players = (
        session.query(Player)
        .filter(Player.season == league.season, Player.source == league.source)
        .all()
    )
    if not players:
        print("No players imported for this league/season.")
        return 1

    # ESPN raw stat lines (for the component breakdown), keyed by player id.
    espn_raw = {
        pp.player_id: (pp.raw_stats or {})
        for pp in session.query(PlayerProjection).filter(
            PlayerProjection.source_key == league.source
        ).all()
    }

    matcher = PlayerMatcher(
        [Candidate(player_id=p.id, name=p.name, position=p.position, pro_team=p.pro_team or "")
         for p in players]
    )
    by_id = {p.id: p for p in players}

    print(f"Fetching Sleeper {'season' if args.week is None else f'week {args.week}'} "
          f"projections for {league.season}...")
    rows = sleeper_get(league.season, args.week)
    print(f"Sleeper returned {len(rows)} players.\n")

    compared: list[dict] = []
    for entry in rows:
        pl = entry.get("player") or {}
        name = " ".join(x for x in [pl.get("first_name"), pl.get("last_name")] if x).strip()
        pos = (pl.get("position") or "").upper()
        team = pl.get("team") or ""
        stats = entry.get("stats") or {}
        if not name or not isinstance(stats, dict):
            continue
        cand = matcher.match(name, pos, team)
        if cand is None:
            continue
        raw = sleeper_raw(stats)
        if not raw:
            continue
        player = by_id[cand.player_id]
        sleeper_pts = scoring.score(raw, player.position)
        espn_pts = float(player.espn_projected_points or 0.0)
        compared.append(
            {
                "name": player.name, "pos": player.position,
                "espn": espn_pts, "sleeper": round(sleeper_pts, 1),
                "ratio": (sleeper_pts / espn_pts) if espn_pts else None,
                "sleeper_raw": raw, "espn_raw": espn_raw.get(player.id, {}),
            }
        )

    if not compared:
        print("No players matched between Sleeper and your pool. Cannot compare.")
        return 1

    compared.sort(key=lambda r: r["espn"], reverse=True)

    print(f"{'Player':<24}{'Pos':<5}{'ESPN':>8}{'Sleeper':>9}{'ratio':>8}")
    print("-" * 54)
    for r in compared[: args.top]:
        ratio = f"{r['ratio']:.2f}" if r["ratio"] else "  -"
        print(f"{r['name'][:23]:<24}{r['pos']:<5}{r['espn']:>8.1f}{r['sleeper']:>9.1f}{ratio:>8}")

    ratios = sorted(r["ratio"] for r in compared if r["ratio"])
    if ratios:
        median = ratios[len(ratios) // 2]
        mean = sum(ratios) / len(ratios)
        print(f"\nMatched {len(compared)} players. Sleeper/ESPN ratio: "
              f"median {median:.2f}, mean {mean:.2f}")
        if 0.7 <= median <= 1.4:
            print("=> Magnitudes look consistent. Re-scored under the same rules, the two "
                  "sources are in the same ballpark (differences are projection opinion).")
        else:
            print("=> WARNING: systematic difference. Re-scored under identical rules these "
                  "should be close; a median far from 1.0 points to a bug (per-game vs "
                  "season, a double-counted stat, or bad matches). Investigate before merging.")

    # Component breakdown for the top few -- this is where a per-game vs season
    # mix-up or a double-counted stat shows itself immediately.
    print("\nComponent check (season stat totals) for the top 5:")
    for r in compared[:5]:
        keys = sorted(set(r["sleeper_raw"]) | set(r["espn_raw"]),
                      key=lambda k: -(r["sleeper_raw"].get(k, 0) + r["espn_raw"].get(k, 0)))
        parts = []
        for k in keys[:6]:
            label = STAT_LABEL.get(k, k)
            parts.append(f"{label} espn={r['espn_raw'].get(k, 0):g}/slp={r['sleeper_raw'].get(k, 0):g}")
        print(f"  {r['name'][:22]:<23} {r['pos']:<3} " + "  ".join(parts))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
