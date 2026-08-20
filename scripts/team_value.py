#!/usr/bin/env python3
"""Read-only: two team rankings side by side -- starting points vs roster VORP.

The app currently ranks teams by projected *starting-lineup* points ("how many
points can this team's best legal lineup score"). FantasyPros' Draft Score is a
different question: total value-over-replacement across the *whole* roster,
bench included ("how much scarce fantasy value does this team own vs what's on
waivers"). A team can rank very differently under the two.

This computes both for your imported league, using the app's real engine and
YOUR 12-team replacement levels (not a generic one):

    Roster VORP = sum over every rostered player of
                  max(0, projection - replacement level at that position)

and prints, per team: projected starting points (+rank) and roster VORP
(+rank). If your team is mid-pack in starting points but top-2 in roster VORP,
that is very likely what your league mate saw in FantasyPros -- reproduced from
your own data, no screenshot needed. Writes nothing.

    cd C:\\FantasyWarRoom
    .venv\\Scripts\\python.exe scripts\\team_value.py
    .venv\\Scripts\\python.exe scripts\\team_value.py --league-id 11507 --season 2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import get_session_factory  # noqa: E402
from app.engine.roster import build_optimal_lineup  # noqa: E402
from app.models import League, Player  # noqa: E402
from app.services import season as season_service  # noqa: E402
from app.services.board import build_engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", type=int, default=None)
    ap.add_argument("--season", type=int, default=None)
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

    engine = build_engine(session, league)
    rosters = season_service.rosters_by_team(session, league)
    print(f"League: {league.name}  season={league.season}  source={league.source}")
    print("Roster VORP = value over YOUR league's replacement level, summed over the whole roster.\n")

    # A player is "projected" if we imported it with a real projection. This is
    # the boring-data check: a rostered player missing here is an import gap, not
    # a model opinion.
    projected_by_id = {
        p.espn_player_id: p
        for p in session.query(Player).filter(
            Player.season == league.season, Player.source == league.source
        ).all()
        if (p.espn_projected_points or 0) > 0
    }
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
            if int(e["espn_player_id"]) not in projected_by_id
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
              f"#{mine['vrank']} of {len(rows)} in roster VORP.")
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
    # projection is a real data gap that drags a rating down for no football
    # reason. Roster fullness (imported vs the league max) is shown for context
    # but is NOT itself a gap -- teams routinely leave bench/IR spots empty, so
    # 14 of 16 is a half-empty bench, not a broken import.
    print(f"\nROSTER COVERAGE  (roster max from league settings = {expected_size or '?'})")
    print("-" * 74)
    any_missing = False
    for r in sorted(rows, key=lambda x: (bool(x["missing"]), x["imported"]), reverse=True):
        flag = ""
        if r["missing"]:
            flag = f"  <-- {len(r['missing'])} missing projection(s)"
            any_missing = True
        tag = "  <- YOU" if r["mine"] else ""
        print(f"  {r['name'][:26]:<27} {r['imported']:>2}/{expected_size or '?'} rostered"
              f"  ·  {r['projected']:>2} projected  ·  {round(r['coverage'] * 100):>3}%{flag}{tag}")
    if not any_missing:
        print("  Every rostered player has a projection -- no import gap on any team.")
        print("  (imported < max just means empty bench/IR spots, which is normal.)")

    mine_cov = next((r for r in rows if r["mine"]), None)
    if mine_cov:
        print(f"\n{mine_cov['name']}")
        print(f"  Roster: {mine_cov['imported']} / {expected_size or '?'}")
        print(f"  Projected: {mine_cov['projected']} / {mine_cov['imported']}")
        print(f"  Coverage: {round(mine_cov['coverage'] * 100)}%")
        if mine_cov["missing"]:
            print(f"  Missing projections: {', '.join(mine_cov['missing'])}")
        else:
            print("  Missing projections: none")
        if not mine_cov["missing"]:
            print(f"  => Clean import: every rostered player is projected. Your "
                  f"#{mine_cov['srank']} / #{mine_cov['vrank']} ratings are the model's "
                  "opinion, not a data gap.")
        else:
            print("  => Import gap: rostered players above have no projection -- "
                  "fix this before trusting the ratings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
