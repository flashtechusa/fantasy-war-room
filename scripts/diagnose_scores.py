"""Why is every team's roster construction score what it is?

Run this when the scores look wrong. It prints the two numbers the grade is
built from -- what each team actually starts, and the bar they are measured
against -- side by side, so you can see which one moved.

    C:\FantasyWarRoom\.venv\Scripts\python.exe scripts\diagnose_scores.py

The bar is `ValuationEngine.average_starter_points()`: the mean of the top N
players at a position in the *imported pool*, where N is the league's total
demand for that position. That is not the same thing as what an average team
actually starts, and the gap between the two is what this report exposes.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault(
    "FWR_DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'fantasy_war_room.db').as_posix()}"
)

from app.db import get_session_factory, init_db  # noqa: E402
from app.engine.roster import (  # noqa: E402
    bye_week_conflicts,
    positional_strength,
    roster_construction_score,
)
from app.models import League, Player  # noqa: E402
from app.services import board as board_service  # noqa: E402
from app.services import season as season_service  # noqa: E402


def main() -> int:
    init_db()
    session = get_session_factory()()

    league = (
        session.query(League).order_by(League.imported_at.desc()).first()
    )
    if league is None:
        print("No league imported. Nothing to diagnose.")
        return 1

    weeks = league.regular_season_weeks or 17
    print(f"League : {league.name} ({league.season}), {league.team_count} teams")
    print(f"Source : {league.source}")
    print(f"Pool   : {session.query(Player).filter(Player.season == league.season).count()} players")
    print(f"Weeks  : {weeks} (per-week figures below divide by this)\n")

    engine = board_service.build_engine(session, league)
    shape = engine.shape
    bar = engine.average_starter_points()

    # ---- the bar, and whether it is even being computed over enough players --
    print("THE BAR each team is measured against")
    print("  pos  pool   N used   truncated   bar/season   bar/week")
    truncated = []
    for position in sorted(bar):
        group = engine._by_position.get(position, [])
        demand = engine.replacement.positions.get(position)
        slots = shape.league_starter_demand(position)
        if demand is not None:
            slots = max(slots + demand.flex_demand, 1)
        slots = max(slots, 1)
        short = len(group) < slots
        if short:
            truncated.append(position)
        print(
            f"  {position:4} {len(group):5} {slots:8} {'YES' if short else 'no':>11}"
            f" {bar[position]:12.1f} {bar[position] / weeks:10.1f}"
        )

    if truncated:
        print(
            f"\n  !! {', '.join(truncated)}: the pool holds fewer players than the league\n"
            "     starts, so the bar is the average of only the elite ones. Every\n"
            "     team will read as below average here. Re-import with a deeper\n"
            "     player list."
        )

    # ---- what teams actually start ------------------------------------------
    rosters = season_service.rosters_by_team(session, league)
    actual: dict[str, list[float]] = defaultdict(list)
    scored = []

    for team in league.teams:
        ids = rosters.get(team.espn_team_id) or set()
        if not ids:
            continue
        roster = engine.roster_players(ids)
        lineup = engine.optimal_lineup(ids)
        for assignment in lineup.starters:
            if assignment.player is not None:
                actual[assignment.player.position].append(
                    assignment.player.projected_points
                )
        strengths = positional_strength(
            roster,
            shape,
            {p: b.replacement_points for p, b in engine.replacement.positions.items()},
            bar,
        )
        score, _ = roster_construction_score(
            roster, shape, strengths, bye_week_conflicts(roster, shape),
            picks_made=len(roster),
        )
        scored.append((team.name, score, sum(s.edge for s in strengths), len(roster)))

    if not scored:
        print("\nNo rosters found -- nothing to compare the bar against.")
        return 0

    print(f"\nWHAT {len(scored)} TEAMS ACTUALLY START")
    print("  pos   avg started   the bar   gap      slots started")
    for position in sorted(actual):
        values = actual[position]
        mean = sum(values) / len(values)
        print(
            f"  {position:4} {mean:13.1f} {bar.get(position, 0.0):9.1f}"
            f" {mean - bar.get(position, 0.0):+7.1f} {len(values):16}"
        )

    print("\nEVERY TEAM, SAME YARDSTICK")
    print("  score   edge total   size   team")
    for name, score, edge, size in sorted(scored, key=lambda r: -r[1]):
        flag = "  <-- edge clamped" if abs(edge) > 240 else ""
        print(f"  {score:5.1f} {edge:12.1f} {size:6}   {name}{flag}")

    edges = [row[2] for row in scored]
    below = sum(1 for e in edges if e < 0)
    clamped = sum(1 for e in edges if abs(e) > 240)
    print(
        f"\n  {below} of {len(scored)} teams are below the bar overall.\n"
        f"  {clamped} of {len(scored)} have an edge past +/-240, where the score stops\n"
        "  distinguishing between them -- the edge term is clamped to +/-20 points."
    )
    if below >= len(scored) * 0.75:
        print(
            "\n  When nearly every team is below the bar, the bar is the problem,\n"
            "  not the rosters: these teams hold most of the players it is built\n"
            "  from."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
