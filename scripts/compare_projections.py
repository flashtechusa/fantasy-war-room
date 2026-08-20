#!/usr/bin/env python3
"""Read-only: ESPN vs Sleeper vs FantasyPros, re-scored under YOUR league rules.

The three-source version of compare_sleeper.py. For a league you have imported,
it re-scores each source's raw component projections under that league's own
scoring rules -- using the app's real scoring engine and player matcher -- and
prints them side by side with ratios to ESPN.

FantasyPros is optional and rate-limited: their free key allows 50 requests a
day and bills **one request per position**. This script fetches only the
positions you ask for (default QB/RB/WR/TE = 4 requests) and prints how many it
used, so you stay well inside the cap. It needs a key configured (League screen,
or FWR_FANTASYPROS_API_KEY); without one it simply prints the ESPN/Sleeper pair.

Writes nothing: no projections are stored, no toggle is changed.

    cd C:\\FantasyWarRoom
    .venv\\Scripts\\python.exe scripts\\compare_projections.py
    .venv\\Scripts\\python.exe scripts\\compare_projections.py --league-id 11507 --season 2026 --top 40
    .venv\\Scripts\\python.exe scripts\\compare_projections.py --positions QB,RB,WR,TE,K,DST   # 6 requests
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import get_session_factory  # noqa: E402
from app.models import League, Player  # noqa: E402
from app.projections.fantasypros import (  # noqa: E402
    FantasyProsClient,
    FantasyProsError,
    parse_projections,
)
from app.projections.matching import Candidate, PlayerMatcher  # noqa: E402
from app.services.board import league_scoring  # noqa: E402
from app.services.runtime_config import effective_settings  # noqa: E402

SLEEPER_BASE = "https://api.sleeper.com"
DEFAULT_POSITIONS = ("QB", "RB", "WR", "TE")

#: Sleeper stat name -> ESPN stat id (identical to the Sleeper adapter).
SLEEPER_STAT_MAP = {
    "pass_yd": 3, "pass_td": 4, "pass_int": 20, "pass_2pt": 19, "pass_att": 0, "pass_cmp": 1,
    "rush_yd": 24, "rush_td": 25, "rush_2pt": 26,
    "rec": 53, "rec_yd": 42, "rec_td": 43, "rec_2pt": 44,
    "fum_lost": 72,
}


def sleeper_projections(season, week):
    path = f"/projections/nfl/{season}" if week is None else f"/projections/nfl/{season}/{week}"
    params = [("season_type", "regular"), ("order_by", "pts_ppr")]
    params += [("position[]", p) for p in ("QB", "RB", "WR", "TE")]
    url = f"{SLEEPER_BASE}{path}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "fwr-compare"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except (urllib.error.HTTPError, Exception) as exc:  # noqa: BLE001
        print(f"  Sleeper fetch failed: {exc}")
        return []
    rows = list(data.values()) if isinstance(data, dict) else data
    out = []
    for entry in rows:
        pl = entry.get("player") or {}
        name = " ".join(x for x in [pl.get("first_name"), pl.get("last_name")] if x).strip()
        stats = entry.get("stats") or {}
        if not name or not isinstance(stats, dict):
            continue
        raw = {}
        for k, sid in SLEEPER_STAT_MAP.items():
            if k in stats:
                try:
                    v = float(stats[k])
                except (TypeError, ValueError):
                    v = 0.0
                if v:
                    raw[str(sid)] = raw.get(str(sid), 0.0) + v
        out.append((name, (pl.get("position") or "").upper(), pl.get("team") or "", raw))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", type=int, default=None)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--week", type=int, default=None, help="Omit for season totals.")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--positions", default=",".join(DEFAULT_POSITIONS),
                    help="FantasyPros positions to request (1 request each). Default QB,RB,WR,TE.")
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
    by_id = {p.id: p for p in players}
    matcher = PlayerMatcher(
        [Candidate(player_id=p.id, name=p.name, position=p.position, pro_team=p.pro_team or "")
         for p in players]
    )

    def rescore_and_match(entries):
        """entries: list of (name, pos, team, raw_stats) -> {player_id: points}."""
        out = {}
        for name, pos, team, raw in entries:
            if not raw:
                continue
            cand = matcher.match(name, pos, team)
            if cand is None:
                continue
            out[cand.player_id] = scoring.score(raw, by_id[cand.player_id].position)
        return out

    # --- Sleeper -----------------------------------------------------------
    print("Fetching Sleeper projections...")
    sleeper_pts = rescore_and_match(sleeper_projections(league.season, args.week))
    print(f"  Sleeper matched {len(sleeper_pts)} players.")

    # --- FantasyPros (optional, rate-limited) ------------------------------
    fp_pts: dict[int, float] = {}
    fp_requests = 0
    settings = effective_settings(session)
    key = settings.fantasypros_api_key
    positions = [p.strip().upper() for p in args.positions.split(",") if p.strip()]
    if not key:
        print("\nFantasyPros: no API key configured -- skipping (ESPN vs Sleeper only).")
        print("  Add a key on the League screen to include it.")
    else:
        print(f"\nFetching FantasyPros ({', '.join(positions)}) -- 1 request per position...")
        client = FantasyProsClient(api_key=key, season=league.season)
        fp_entries = []
        for pos in positions:
            try:
                payload = client._get("projections", {"position": pos, "week": "draft", "scoring": "STD"})  # noqa: SLF001
                fp_requests += 1
            except FantasyProsError as exc:
                print(f"  {pos}: {exc}")
                break
            for fpl in parse_projections(payload, pos):
                fp_entries.append((fpl.name, fpl.position, fpl.pro_team, fpl.raw_stats))
        fp_pts = rescore_and_match(fp_entries)
        print(f"  FantasyPros matched {len(fp_pts)} players. Used {fp_requests} of your 50/day.")
        print("  (Free tier truncates to the top of each position, so only the top players get an FP number.)")

    # --- table -------------------------------------------------------------
    rows = []
    for p in players:
        espn = float(p.espn_projected_points or 0.0)
        rows.append((p, espn, sleeper_pts.get(p.id), fp_pts.get(p.id)))
    rows.sort(key=lambda r: r[1], reverse=True)

    def cell(v):
        return f"{v:7.1f}" if v is not None else "      -"

    def ratio(v, espn):
        return f"{v / espn:5.2f}" if (v is not None and espn) else "    -"

    print(f"\n{'Player':<22}{'Pos':<4}{'ESPN':>7}{'SLP':>7}{'FP':>7}{'SLP%':>6}{'FP%':>6}")
    print("-" * 59)
    for p, espn, slp, fp in rows[: args.top]:
        print(f"{p.name[:21]:<22}{p.position:<4}{espn:7.1f}{cell(slp)}{cell(fp)}"
              f"{ratio(slp, espn):>6}{ratio(fp, espn):>6}")

    slp_ratios = [slp / espn for _p, espn, slp, _fp in rows if slp and espn]
    fp_ratios = [fp / espn for _p, espn, _slp, fp in rows if fp and espn]
    print("\nRe-scored under your rules, vs ESPN:")
    if slp_ratios:
        print(f"  Sleeper:     median {median(slp_ratios):.2f}  ({len(slp_ratios)} players)")
    if fp_ratios:
        print(f"  FantasyPros: median {median(fp_ratios):.2f}  ({len(fp_ratios)} players)")
    print("\nClose to 1.00 = the sources agree on magnitude; the gaps are projection opinion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
