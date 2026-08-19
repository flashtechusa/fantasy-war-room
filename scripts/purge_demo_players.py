"""Remove synthetic demo players from a live database.

The player pool is keyed by season, not by league, so a demo import writes into
the same table a real league reads. Once that has happened the fake players sit
at the top of every position: they inflate the bar each team is graded against,
move replacement level (and so every VOR), and get offered as free agents who
do not exist.

Tagging players by source stops it recurring. This removes the ones already
there.

    # show what would go, change nothing
    C:\\FantasyWarRoom\\.venv\\Scripts\\python.exe scripts\\purge_demo_players.py

    # do it (backs the database up first)
    C:\\FantasyWarRoom\\.venv\\Scripts\\python.exe scripts\\purge_demo_players.py --yes
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    League,
    Player,
    PlayerProjection,
    PlayerWeeklyProjection,
)

DEFAULT_DB = ROOT / "data" / "fantasy_war_room.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--yes", action="store_true", help="actually delete (default is a dry run)"
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}")
        return 1

    engine = create_engine(f"sqlite:///{args.db.as_posix()}", future=True)
    session = sessionmaker(bind=engine, future=True)()

    leagues = session.scalars(select(League)).all()
    real = {lg.source for lg in leagues if lg.source != "demo"}
    if not real:
        print("Every league here is a demo league. Nothing to clean.")
        return 0

    print(f"Leagues: " + ", ".join(f"{lg.name} ({lg.source})" for lg in leagues))

    doomed = session.scalars(
        select(Player).where(Player.season.in_([lg.season for lg in leagues]))
    ).all()
    doomed = [p for p in doomed if (p.source or "espn") not in real]
    if not doomed:
        print("\nNo foreign players in the pool. Nothing to do.")
        return 0

    by_position: dict[str, int] = defaultdict(int)
    for player in doomed:
        by_position[player.position] += 1

    print(f"\n{len(doomed)} players do not belong to a real league here:")
    for position in sorted(by_position):
        print(f"   {position:4} {by_position[position]:5}")
    print("\n  a sample, so you can confirm these are not real people:")
    for player in doomed[:8]:
        print(f"   {player.name} ({player.position}, id {player.espn_player_id})")

    if not args.yes:
        print("\nDry run. Re-run with --yes to delete them.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = args.db.with_name(f"{args.db.stem}-before-purge-{stamp}.db")
    shutil.copy2(args.db, backup)
    print(f"\nBacked up to {backup}")

    ids = [p.id for p in doomed]
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start : chunk_start + 500]
        for model in (PlayerWeeklyProjection, PlayerProjection):
            for row in session.scalars(
                select(model).where(model.player_id.in_(chunk))
            ).all():
                session.delete(row)
        for player in session.scalars(
            select(Player).where(Player.id.in_(chunk))
        ).all():
            session.delete(player)
    session.commit()

    remaining = session.scalar(select(Player).where(Player.id.in_(ids)))
    print(f"Deleted {len(ids)} players. Remaining from that set: "
          f"{'none' if remaining is None else 'some -- rerun'}")
    print("\nRestart the app so it rebuilds its rankings:")
    print("   Stop-ScheduledTask -TaskName FantasyWarRoom; "
          "Start-ScheduledTask -TaskName FantasyWarRoom")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
