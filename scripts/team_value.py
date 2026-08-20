#!/usr/bin/env python3
"""Read-only: two team rankings side by side -- starting points vs roster VORP.

The app currently ranks teams by projected *starting-lineup* points ("how many
points can this team's best legal lineup score"). FantasyPros' Draft Score is a
different question: total value-over-replacement across the *whole* roster,
bench included ("how much scarce fantasy value does this team own vs what's on
waivers"). A team can rank very differently under the two.

This computes both for your imported league, using the app's real engine and
YOUR league's replacement levels (not a generic one):

    Roster VORP = sum over every rostered player of
                  max(0, projection - replacement level at that position)

and prints, per team: projected starting points (+rank) and roster VORP (+rank).

--source runs the SAME formula under a different projection source, so you can
ask the clean question -- "hold the methodology constant, how much does the
projection source alone move the rankings?" A source's projections and its
replacement levels are recomputed together, so VORP stays on one scale. Where a
source does not cover a rostered player (FantasyPros' free tier only returns the
top of each position) that player falls back to ESPN, and the coverage line
tells you how much of the ranking the source actually drove. Writes nothing.

    cd C:\\FantasyWarRoom
    .venv\\Scripts\\python.exe scripts\\team_value.py --league-id 11507 --season 2026
    .venv\\Scripts\\python.exe scripts\\team_value.py --league-id 11507 --season 2026 --source sleeper
    .venv\\Scripts\\python.exe scripts\\team_value.py --league-id 11507 --season 2026 --source fantasypros
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.engine.roster import build_optimal_lineup  # noqa: E402
from app.engine.valuation import ValuationEngine  # noqa: E402
from app.db import get_session_factory  # noqa: E402
from app.models import League, Player  # noqa: E402
from app.projections.matching import Candidate, PlayerMatcher  # noqa: E402
from app.services import season as season_service  # noqa: E402
from app.services.board import build_engine, league_scoring, league_shape  # noqa: E402

SLEEPER_BASE = "https://api.sleeper.com"

#: Sleeper stat name -> ESPN stat id (identical to the Sleeper adapter).
SLEEPER_STAT_MAP = {
    "pass_yd": 3, "pass_td": 4, "pass_int": 20, "pass_2pt": 19, "pass_att": 0, "pass_cmp": 1,
    "rush_yd": 24, "rush_td": 25, "rush_2pt": 26,
    "rec": 53, "rec_yd": 42, "rec_td": 43, "rec_2pt": 44,
    "fum_lost": 72,
}


def _sleeper_entries(season):
    """[(name, pos, team, raw_stats_by_espn_stat_id)] season totals from Sleeper."""
    params = [("season_type", "regular"), ("order_by", "pts_ppr")]
    params += [("position[]", p) for p in ("QB", "RB", "WR", "TE")]
    url = f"{SLEEPER_BASE}/projections/nfl/{season}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "fwr-team-value"})
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
        raw: dict[str, float] = {}
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


def _fantasypros_entries(session, league, positions):
    """[(name, pos, team, raw_stats)] and a note; needs a configured key."""
    from app.projections.fantasypros import (
        FantasyProsClient,
        FantasyProsError,
        parse_projections,
    )
    from app.services.runtime_config import effective_settings

    key = effective_settings(session).fantasypros_api_key
    if not key:
        return [], "no FantasyPros API key configured (add one on the League screen)."
    client = FantasyProsClient(api_key=key, season=league.season)
    entries, used = [], 0
    for pos in positions:
        try:
            payload = client._get("projections", {"position": pos, "week": "draft", "scoring": "STD"})  # noqa: SLF001
            used += 1
        except FantasyProsError as exc:
            return entries, f"stopped at {pos}: {exc} (used {used} requests)."
        for fpl in parse_projections(payload, pos):
            entries.append((fpl.name, fpl.position, fpl.pro_team, fpl.raw_stats))
    return entries, f"used {used} of your 50/day (top of each of {', '.join(positions)})."


def _raw_by_espn_id(session, league, entries):
    """Match source entries to imported players -> {espn_player_id: raw_stats}."""
    players = (
        session.query(Player)
        .filter(Player.season == league.season, Player.source == league.source)
        .all()
    )
    id_to_espn = {p.id: p.espn_player_id for p in players}
    matcher = PlayerMatcher(
        [Candidate(player_id=p.id, name=p.name, position=p.position, pro_team=p.pro_team or "")
         for p in players]
    )
    out: dict[int, dict[str, float]] = {}
    for name, pos, team, raw in entries:
        if not raw:
            continue
        cand = matcher.match(name, pos, team)
        if cand is None:
            continue
        espn_id = id_to_espn.get(cand.player_id)
        if espn_id is not None:
            out[espn_id] = {str(k): float(v) for k, v in raw.items()}
    return out


def _engine_for_source(session, league, source, positions):
    """(engine, covered_ids, note). ESPN is the app's real engine untouched.

    For Sleeper/FantasyPros we overlay the source's raw stat lines onto the ESPN
    player pool and rebuild a fresh engine, so projections AND replacement levels
    are recomputed together under the same league scoring rules. Players the
    source does not cover keep their ESPN projection (an honest overlay, not a
    silent zero) and are reported in the coverage line.
    """
    base = build_engine(session, league)
    if source == "espn":
        covered = {
            p.espn_player_id
            for p in session.query(Player).filter(
                Player.season == league.season, Player.source == league.source
            ).all()
            if (p.espn_projected_points or 0) > 0
        }
        return base, covered, "the app's current source."

    if source == "sleeper":
        entries = _sleeper_entries(league.season)
        note = f"{len(entries)} raw projections from Sleeper."
    else:  # fantasypros
        entries, note = _fantasypros_entries(session, league, positions)

    raw_by_id = _raw_by_espn_id(session, league, entries)
    covered = set(raw_by_id)
    overlaid = [
        dataclasses.replace(p, raw_stats=raw_by_id[p.espn_player_id])
        if p.espn_player_id in raw_by_id else p
        for p in base.players
    ]
    engine = ValuationEngine(
        scoring=league_scoring(league),
        shape=league_shape(league),
        players=overlaid,
        source=league.source,
    )
    return engine, covered, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", type=int, default=None)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--source", choices=("espn", "sleeper", "fantasypros"), default="espn",
                    help="Projection source to rank under. Default espn (the app's current source).")
    ap.add_argument("--positions", default="QB,RB,WR,TE",
                    help="FantasyPros positions to request (1 request each). Default QB,RB,WR,TE.")
    args = ap.parse_args()
    positions = [p.strip().upper() for p in args.positions.split(",") if p.strip()]

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

    engine, covered_ids, source_note = _engine_for_source(session, league, args.source, positions)
    rosters = season_service.rosters_by_team(session, league)
    print(f"League: {league.name}  season={league.season}  source={league.source}")
    print(f"Ranking under projection source: {args.source.upper()}  -- {source_note}")
    print("Roster VORP = value over this source's replacement level, summed over the whole roster.")
    if args.source != "espn" and not covered_ids:
        print(f"\n!! {args.source.upper()} returned no usable projections, so EVERY number below is\n"
              f"   the ESPN fallback. This is NOT a {args.source.upper()} ranking -- do not read it as\n"
              "   'the source agrees with ESPN'. Fix the fetch/key and re-run before comparing.")
    print()

    expected_size = (
        sum((league.roster_slots or {}).values())
        + (league.bench_slots or 0)
        + (league.ir_slots or 0)
    )

    rows = []
    for team in league.teams:
        ids = rosters.get(team.espn_team_id)
        if not ids:
            continue
        roster = engine.roster_players(ids)
        if not roster:
            continue
        start_pts = build_optimal_lineup(roster, engine.shape).total_points
        vorp = sum(
            max(0.0, p.projected_points - engine.replacement.replacement_for(p.position))
            for p in roster
        )
        # Coverage from the raw roster entries (which carry names).
        entries = [e for e in (team.roster or []) if e.get("espn_player_id")]
        imported = len(entries)
        missing = [
            e.get("name") or f"#{e['espn_player_id']}"
            for e in entries
            if int(e["espn_player_id"]) not in covered_ids
        ]
        proj_count = imported - len(missing)
        rows.append({
            "name": team.name, "mine": bool(team.is_mine),
            "start": round(start_pts, 1), "vorp": round(vorp, 1),
            "size": len(roster),
            "imported": imported, "projected": proj_count, "missing": missing,
            "coverage": (proj_count / imported) if imported else 0.0,
        })

    if not rows:
        print("No rosters imported yet -- nothing to rank.")
        return 1

    # Ranks (1 = best) on each metric.
    for i, r in enumerate(sorted(rows, key=lambda x: -x["start"]), 1):
        r["srank"] = i
    for i, r in enumerate(sorted(rows, key=lambda x: -x["vorp"]), 1):
        r["vrank"] = i

    rows.sort(key=lambda r: r["start"], reverse=True)
    print(f"{'#':<3}{'Team':<26}{'StartPts':>9}{'SRank':>6}{'RosterVORP':>12}{'VRank':>6}   move")
    print("-" * 74)
    for r in rows:
        move = r["srank"] - r["vrank"]  # +ve => ranks higher on roster value
        arrow = f"+{move}" if move > 0 else (str(move) if move < 0 else " 0")
        tag = "  <- YOU" if r["mine"] else ""
        print(f"{r['srank']:<3}{r['name'][:25]:<26}{r['start']:>9.1f}{r['srank']:>6}"
              f"{r['vorp']:>12.1f}{r['vrank']:>6}{arrow:>7}{tag}")

    mine = next((r for r in rows if r["mine"]), None)
    if mine:
        print(f"\nYour team: #{mine['srank']} of {len(rows)} in starting points, "
              f"#{mine['vrank']} of {len(rows)} in roster VORP  (source: {args.source.upper()}).")
        if mine["srank"] - mine["vrank"] >= 2:
            print("=> You own more roster value than your starting lineup shows -- the gap "
                  "FantasyPros' whole-roster Draft Score rewards. Consistent with your #1 there.")
        elif mine["vrank"] - mine["srank"] >= 2:
            print("=> Your starting lineup outranks your total roster value (thin bench / "
                  "little surplus). The opposite of a FantasyPros-style #1.")
        else:
            print("=> Both rankings agree for your team, so roster method alone does not "
                  "explain a big FantasyPros gap -- worth a closer look if one exists.")
    print("\n('move' = SRank - VRank; +N means the team ranks N spots higher on whole-roster value.)")

    # --- roster coverage: rule out a boring data problem -------------------
    # The decisive check is projection coverage: a *rostered* player with no
    # projection from the active source. Under ESPN this is the import-gap check.
    # Under a non-ESPN source it also shows how much of the ranking the source
    # really drove vs where it fell back to ESPN.
    print(f"\nROSTER COVERAGE  ({args.source.upper()} projections; roster max = {expected_size or '?'})")
    print("-" * 74)
    any_missing = False
    for r in sorted(rows, key=lambda x: (bool(x["missing"]), x["imported"]), reverse=True):
        flag = ""
        if r["missing"]:
            flag = f"  <-- {len(r['missing'])} not covered"
            any_missing = True
        tag = "  <- YOU" if r["mine"] else ""
        print(f"  {r['name'][:26]:<27} {r['imported']:>2}/{expected_size or '?'} rostered"
              f"  ·  {r['projected']:>2} projected  ·  {round(r['coverage'] * 100):>3}%{flag}{tag}")
    if not any_missing:
        if args.source == "espn":
            print("  Every rostered player has a projection -- no import gap on any team.")
            print("  (imported < max just means empty bench/IR spots, which is normal.)")
        else:
            print(f"  {args.source.upper()} covers every rostered player -- no ESPN fallback used.")
    elif args.source == "sleeper":
        print("  '<-- not covered' players fell back to ESPN. Sleeper is fetched for "
              "QB/RB/WR/TE only,\n  so kickers and defenses always fall back -- immaterial "
              "to the skill-position ranking.")
    elif args.source == "fantasypros":
        print("  '<-- not covered' players fell back to ESPN. FantasyPros' free tier only "
              "returns the\n  top of each position, so deeper rostered players fall back -- "
              "check how many before trusting.")

    mine_cov = next((r for r in rows if r["mine"]), None)
    if mine_cov:
        print(f"\n{mine_cov['name']}  (source: {args.source.upper()})")
        print(f"  Roster: {mine_cov['imported']} / {expected_size or '?'}")
        print(f"  Projected by source: {mine_cov['projected']} / {mine_cov['imported']}")
        print(f"  Coverage: {round(mine_cov['coverage'] * 100)}%")
        if mine_cov["missing"]:
            print(f"  Not covered by {args.source.upper()}: {', '.join(mine_cov['missing'])}")
        else:
            print(f"  Not covered by {args.source.upper()}: none")
        if args.source == "espn":
            if not mine_cov["missing"]:
                print(f"  => Clean import: every rostered player is projected. Your "
                      f"#{mine_cov['srank']} / #{mine_cov['vrank']} ratings are the model's "
                      "opinion, not a data gap.")
            else:
                print("  => Import gap: rostered players above have no projection -- "
                      "fix this before trusting the ratings.")
        else:
            print(f"  => {args.source.upper()} ranks your team #{mine_cov['srank']} in starting "
                  f"points, #{mine_cov['vrank']} in roster VORP. Compare these two ranks to the "
                  "ESPN run -- if they barely move, the source is not what's driving a "
                  "FantasyPros-style #1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
