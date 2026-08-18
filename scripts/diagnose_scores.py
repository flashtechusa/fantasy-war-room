"""Why is every team's roster construction score what it is -- and what changed?

The grade is built from two numbers, and only one of them is on screen: what a
team starts, and the bar it is measured against. When every team's score moves
at once the rosters have not changed, so the bar has.

    # what the scores are built from right now
    C:\FantasyWarRoom\.venv\Scripts\python.exe scripts\diagnose_scores.py

    # what changed since a backup the auto-updater kept
    C:\FantasyWarRoom\.venv\Scripts\python.exe scripts\diagnose_scores.py ^
        --compare C:\FantasyWarRoom\backups\fantasy_war_room-20260817-043000.db

The bar is `ValuationEngine.average_starter_points()`: the mean of the top N
players at a position in the *imported pool*, where N is the league's total
demand for that position. It is not the same thing as what an average team
actually starts, and comparing the two is the point of this report.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.engine.roster import (  # noqa: E402
    bye_week_conflicts,
    positional_strength,
    roster_construction_score,
)
from app.models import League, Player  # noqa: E402
from app.services import board as board_service  # noqa: E402
from app.services import season as season_service  # noqa: E402

DEFAULT_DB = ROOT / "data" / "fantasy_war_room.db"


def open_db(path: Path):
    if not path.exists():
        raise SystemExit(f"No database at {path}")
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)
    return sessionmaker(bind=engine, future=True)()


def snapshot(path: Path) -> dict:
    """Everything the score is built from, for one database file."""
    session = open_db(path)
    league = session.scalars(
        select(League).order_by(League.imported_at.desc())
    ).first()
    if league is None:
        raise SystemExit(f"{path.name} holds no imported league.")

    # The cache is keyed on league identity, which two database files share.
    board_service.clear_cache()
    engine = board_service.build_engine(session, league)
    shape = engine.shape
    bar = engine.average_starter_points()

    pool: dict[str, int] = {}
    demand_slots: dict[str, int] = {}
    top_n: dict[str, list[tuple[str, float]]] = {}
    for position, group in engine._by_position.items():
        demand = engine.replacement.positions.get(position)
        slots = shape.league_starter_demand(position)
        if demand is not None:
            slots = max(slots + demand.flex_demand, 1)
        slots = max(slots, 1)
        pool[position] = len(group)
        demand_slots[position] = slots
        top_n[position] = [
            (p.name, round(engine._points[p.espn_player_id], 1)) for p in group[:slots]
        ]

    rosters = season_service.rosters_by_team(session, league)
    started: dict[str, list[float]] = defaultdict(list)
    teams = []
    for team in league.teams:
        ids = rosters.get(team.espn_team_id) or set()
        if not ids:
            continue
        roster = engine.roster_players(ids)
        lineup = engine.optimal_lineup(ids)
        for assignment in lineup.starters:
            if assignment.player is not None:
                started[assignment.player.position].append(
                    assignment.player.projected_points
                )
        strengths = positional_strength(
            roster,
            shape,
            {p: b.replacement_points for p, b in engine.replacement.positions.items()},
            bar,
        )
        score, _ = roster_construction_score(
            roster, shape, strengths,
            bye_week_conflicts(roster, shape), picks_made=len(roster),
        )
        teams.append(
            {
                "name": team.name,
                "score": score,
                "edge": round(sum(s.edge for s in strengths), 1),
                "size": len(roster),
            }
        )

    return {
        "path": path,
        "league": league,
        "weeks": league.regular_season_weeks or 17,
        "pool_total": len(engine.players),
        "bar": bar,
        "pool": pool,
        "slots": demand_slots,
        "top_n": top_n,
        "started": dict(started),
        "teams": teams,
        "points": {
            p.name: round(engine._points[p.espn_player_id], 1) for p in engine.players
        },
    }


def supply(path: Path) -> None:
    """Who the app thinks is actually gettable.

    Three screens disagree when this is wrong: My Team offers an upgrade worth
    hundreds of points, Waivers offers a defence worth three, and VOR moves
    without anyone changing a roster. All of them read the same supply.
    """
    session = open_db(path)
    league = session.scalars(
        select(League).order_by(League.imported_at.desc())
    ).first()
    if league is None:
        return

    print("\n" + "=" * 68)
    print("SUPPLY -- who the app believes is available")
    print("=" * 68)

    players = session.scalars(
        select(Player).where(Player.season == league.season)
    ).all()
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for player in players:
        buckets[player.position][player.availability or "(blank)"] += 1

    states = sorted({s for row in buckets.values() for s in row})
    print("\n  pool by position and ESPN availability")
    print("  pos   " + "".join(f"{s:>12}" for s in states) + "       total")
    for position in sorted(buckets):
        row = buckets[position]
        total = sum(row.values())
        print(
            f"  {position:4}  " + "".join(f"{row.get(s, 0):>12}" for s in states)
            + f"  {total:10}"
        )

    rostered = {
        int(entry["espn_player_id"])
        for team in league.teams
        for entry in (team.roster or [])
        if entry.get("espn_player_id")
    }
    print(f"\n  rostered across all {len(league.teams)} teams : {len(rostered)}")
    print(f"  players in the pool             : {len(players)}")
    print(f"  therefore treated as gettable   : {len(players) - len(rostered)}")
    if not rostered:
        print(
            "\n  !! No team rosters are stored. Every player in the pool"
            " then looks free, so 'best available' becomes the best player"
            " in the league and the upgrade figures on My Team are"
            " nonsense. Re-import."
        )

    board_service.clear_cache()
    engine = board_service.build_engine(session, league)
    free = [p for p in engine.players if p.espn_player_id not in rostered]
    by_position: dict[str, list] = defaultdict(list)
    for player in free:
        by_position[player.position].append(player)

    print("\n  best 3 the app would offer you at each position")
    print("  (if a name here is on somebody's team, that is the bug)")
    for position in sorted(by_position):
        ranked = sorted(
            by_position[position],
            key=lambda p: engine._points[p.espn_player_id],
            reverse=True,
        )[:3]
        names = ", ".join(
            f"{p.name} {engine._points[p.espn_player_id]:.0f}" for p in ranked
        )
        print(f"  {position:4}  {names}")

    wire = [p for p in players if (p.availability or "") in {"FREEAGENT", "WAIVERS"}]
    wire_positions: dict[str, int] = defaultdict(int)
    for player in wire:
        wire_positions[player.position] += 1
    print(
        f"\n  ESPN calls {len(wire)} of them free agents: "
        + ", ".join(f"{k} {v}" for k, v in sorted(wire_positions.items()))
    )
    if wire and set(wire_positions) <= {"DST", "K"}:
        print(
            "     Only kickers and defences came back from the free-agent import,\n"
            "     which is why the waiver screen offers nothing else."
        )


def report(snap: dict) -> None:
    league, weeks = snap["league"], snap["weeks"]
    print(f"League : {league.name} ({league.season}), {league.team_count} teams")
    print(f"Source : {league.source}   imported {league.imported_at}")
    print(f"Pool   : {snap['pool_total']} players")
    print(f"Weeks  : {weeks} (per-week figures divide by this)\n")

    print("THE BAR each team is measured against")
    print("  pos  pool   N used   truncated   bar/season   bar/week")
    thin = []
    for position in sorted(snap["bar"]):
        n, slots = snap["pool"][position], snap["slots"][position]
        short = n < slots
        if short:
            thin.append(position)
        value = snap["bar"][position]
        print(
            f"  {position:4} {n:5} {slots:8} {'YES' if short else 'no':>11}"
            f" {value:12.1f} {value / weeks:10.1f}"
        )
    if thin:
        print(
            f"\n  !! {', '.join(thin)}: the pool holds fewer players than the league\n"
            "     starts, so the bar averages only the ones that exist. Re-import\n"
            "     with a deeper player list."
        )

    if not snap["teams"]:
        print("\nNo rosters found -- nothing to compare the bar against.")
        return

    print(f"\nWHAT {len(snap['teams'])} TEAMS ACTUALLY START")
    print("  pos   avg started   the bar   gap      slots started")
    for position in sorted(snap["started"]):
        values = snap["started"][position]
        mean = sum(values) / len(values)
        bar = snap["bar"].get(position, 0.0)
        print(
            f"  {position:4} {mean:13.1f} {bar:9.1f} {mean - bar:+7.1f} {len(values):16}"
        )

    print("\nEVERY TEAM, SAME YARDSTICK")
    print("  score   edge total   size   team")
    for team in sorted(snap["teams"], key=lambda t: -t["score"]):
        flag = "  <-- edge clamped" if abs(team["edge"]) > 240 else ""
        print(
            f"  {team['score']:5.1f} {team['edge']:12.1f} {team['size']:6}"
            f"   {team['name']}{flag}"
        )

    edges = [t["edge"] for t in snap["teams"]]
    below = sum(1 for e in edges if e < 0)
    clamped = sum(1 for e in edges if abs(e) > 240)
    print(
        f"\n  {below} of {len(edges)} teams are below the bar overall.\n"
        f"  {clamped} of {len(edges)} are past +/-240, where the score stops telling\n"
        "  them apart -- the edge term is clamped to +/-20 points."
    )
    if below >= len(edges) * 0.75:
        print(
            "\n  When nearly every team is below the bar, the bar is the problem,\n"
            "  not the rosters: these teams hold most of the players it is built from."
        )


def compare(now: dict, then: dict) -> None:
    print("\n" + "=" * 68)
    print(f"WHAT CHANGED since {then['path'].name}")
    print("=" * 68)
    print(f"  imported : {then['league'].imported_at}  ->  {now['league'].imported_at}")
    print(f"  pool     : {then['pool_total']}  ->  {now['pool_total']}"
          f"  ({now['pool_total'] - then['pool_total']:+d} players)\n")

    print("  THE BAR")
    print("  pos    then      now     move   pool then -> now")
    for position in sorted(set(now["bar"]) | set(then["bar"])):
        a, b = then["bar"].get(position, 0.0), now["bar"].get(position, 0.0)
        print(
            f"  {position:4} {a:8.1f} {b:8.1f} {b - a:+8.1f}"
            f"   {then['pool'].get(position, 0):5} -> {now['pool'].get(position, 0)}"
        )

    print("\n  BIGGEST PROJECTION MOVES (these are what move the bar)")
    moves = []
    for name, points in now["points"].items():
        before = then["points"].get(name)
        if before is None:
            moves.append((points, name, None, points))
        elif abs(points - before) > 0.05:
            moves.append((abs(points - before), name, before, points))
    moves.sort(reverse=True)
    if not moves:
        print("    None. Every player projects exactly what they did before.")
    for _size, name, before, after in moves[:25]:
        was = "NEW" if before is None else f"{before:.1f}"
        print(f"    {name:28} {was:>8} -> {after:8.1f}")
    if len(moves) > 25:
        print(f"    ... and {len(moves) - 25} more")

    gone = sorted(set(then["points"]) - set(now["points"]))
    if gone:
        print(f"\n  DROPPED FROM THE POOL ({len(gone)}): {', '.join(gone[:15])}"
              f"{' ...' if len(gone) > 15 else ''}")

    print("\n  TEAM SCORES")
    before_by_name = {t["name"]: t for t in then["teams"]}
    for team in sorted(now["teams"], key=lambda t: -t["score"]):
        old = before_by_name.get(team["name"])
        if old is None:
            print(f"    {team['name']:28} {'NEW':>8} -> {team['score']:6.1f}")
        else:
            print(
                f"    {team['name']:28} {old['score']:8.1f} -> {team['score']:6.1f}"
                f"   ({team['score'] - old['score']:+.1f})"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="database to read")
    parser.add_argument(
        "--compare", type=Path, help="an older database to diff against, e.g. a backup"
    )
    args = parser.parse_args()

    now = snapshot(args.db)
    report(now)
    supply(args.db)
    if args.compare:
        compare(now, snapshot(args.compare))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
