"""Phase 6 -- My Team."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..engine.roster import (
    bye_week_conflicts,
    build_optimal_lineup,
    positional_strength,
    roster_construction_score,
    starter_tiers,
)
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import draft as draft_service
from ..services import season as season_service
from .deps import BoardContext, board_dep
from .serializers import (
    serialize_conflicts,
    serialize_lineup,
    serialize_needs,
    serialize_position,
    serialize_strengths,
)

router = APIRouter(prefix="/api/team", tags=["team"])


def _league_starter_tiers(session: Session, engine, league) -> dict[str, list[float]]:
    """What the league's teams actually start, slot by slot.

    This is the bar every team is graded against. Deriving it from the rosters
    rather than from the top of the imported player pool is what keeps the
    grade stable: importing a deeper pool used to raise the bar for everybody
    at once, so scores fell league-wide while no roster had changed.
    """
    rosters = season_service.rosters_by_team(session, league)
    lineups = [
        engine.optimal_lineup(ids)
        for ids in (rosters.get(team.espn_team_id) for team in league.teams)
        if ids
    ]
    return starter_tiers(lineups) if lineups else {}


def _analyse(engine, roster: list, shape, tiers: dict[str, list[float]] | None = None) -> tuple:
    """The same analysis every team gets, so comparisons are like-for-like."""
    lineup = build_optimal_lineup(roster, shape)
    conflicts = bye_week_conflicts(roster, shape)
    strengths = positional_strength(
        roster,
        shape,
        {
            position: baseline.replacement_points
            for position, baseline in engine.replacement.positions.items()
        },
        engine.average_starter_points(),
        league_starter_tiers=tiers,
    )
    score, notes = roster_construction_score(
        roster, shape, strengths, conflicts, picks_made=len(roster)
    )
    return lineup, conflicts, strengths, score, notes


@router.get("/league")
def read_all_teams(
    context: BoardContext = Depends(board_dep),
    session: Session = Depends(get_db),
) -> dict:
    """Every team in the league, scored the same way and ranked against each other.

    A construction score on its own is close to meaningless -- the scale starts
    at a neutral 60, so a perfectly average roster reads like a failing grade.
    Ranking every team against the same yardstick is what turns the number into
    information, and it is also the honest way to present it: "6th of 12" says
    something a bare 55.5 does not.
    """
    engine = context.engine
    league = context.league

    rosters = season_service.rosters_by_team(session, league)
    tiers = _league_starter_tiers(session, engine, league)

    rows = []
    for team in league.teams:
        roster_ids = rosters.get(team.espn_team_id) or set()
        if not roster_ids:
            continue
        roster = engine.roster_players(roster_ids)
        lineup, conflicts, strengths, score, notes = _analyse(
            engine, roster, engine.shape, tiers
        )
        rows.append(
            {
                "espn_team_id": team.espn_team_id,
                "name": team.name,
                "owners": team.owners or [],
                "is_mine": bool(team.is_mine),
                "roster_size": len(roster_ids),
                "roster_construction_score": score,
                "roster_construction_notes": notes,
                "projected_starter_points": lineup.total_points,
                "weekly_points": lineup.weekly_points,
                "positional_strength": serialize_strengths(strengths),
                "strengths": [s.position for s in strengths if s.grade in {"elite", "strong"}],
                "weaknesses": [s.position for s in strengths if s.grade in {"weak", "critical"}],
                "bye_conflicts": serialize_conflicts(conflicts),
            }
        )

    # Rank by projected starter points -- the thing that actually wins games.
    # Construction score measures roster *shape*, which is a different question
    # and deliberately reported alongside rather than blended in.
    rows.sort(key=lambda r: r["projected_starter_points"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    by_score = sorted(rows, key=lambda r: r["roster_construction_score"], reverse=True)
    for index, row in enumerate(by_score, start=1):
        row["construction_rank"] = index

    points = [r["projected_starter_points"] for r in rows]
    scores = [r["roster_construction_score"] for r in rows]

    if not rows:
        # Pre-draft this is the truthful answer, not a failure: nobody has a
        # roster yet. Saying so beats an empty table with no explanation.
        return {
            "teams": [],
            "team_count": 0,
            "league_median_points": 0.0,
            "league_median_score": 0.0,
            "note": (
                "No team has a roster yet. This fills in once the draft has "
                "happened and rosters are imported."
            ),
        }

    return {
        "teams": rows,
        "team_count": len(rows),
        "league_median_points": round(_median(points), 2),
        "league_median_score": round(_median(scores), 1),
        "note": (
            "Ranked by projected starting-lineup points. Construction score "
            "measures roster shape (depth, balance, bye spread) and is ranked "
            "separately -- a team can be strong and badly shaped, or the reverse."
        ),
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


@router.get("")
def read_my_team(
    context: BoardContext = Depends(board_dep),
    session: Session = Depends(get_db),
) -> dict:
    """Starters, bench, strengths, weaknesses, bye conflicts and what to draft next."""
    engine = context.engine
    # Prefer the real ESPN roster: it stays correct through adds, drops and
    # trades, which a draft log never sees. Fall back to draft picks only
    # while drafting, before ESPN has a roster to report.
    my_ids = season_service.espn_roster_ids(session, context.league)
    roster_source = "espn"
    if not my_ids:
        my_ids = draft_service.my_player_ids(context.draft)
        roster_source = "draft"
    roster = engine.roster_players(my_ids)

    lineup = build_optimal_lineup(roster, engine.shape)
    conflicts = bye_week_conflicts(roster, engine.shape)
    strengths = positional_strength(
        roster,
        engine.shape,
        {
            position: baseline.replacement_points
            for position, baseline in engine.replacement.positions.items()
        },
        engine.average_starter_points(),
        league_starter_tiers=_league_starter_tiers(session, engine, context.league),
    )
    score, notes = roster_construction_score(
        roster, engine.shape, strengths, conflicts, picks_made=len(my_ids)
    )

    needs = serialize_needs(context.board.needs)
    top_need = needs[0] if needs else None

    return {
        "picks_made": len(my_ids),
        "roster_source": roster_source,
        "my_team_identified": season_service.my_team(session, context.league) is not None,
        "lineup": serialize_lineup(lineup),
        "positional_strength": serialize_strengths(strengths),
        "strengths": [s.position for s in strengths if s.grade in {"elite", "strong"}],
        "weaknesses": [s.position for s in strengths if s.grade in {"weak", "critical"}],
        "bye_conflicts": serialize_conflicts(conflicts),
        "remaining_needs": needs,
        "highest_marginal_value": (
            {
                "position": top_need["position"],
                "marginal_value": top_need["marginal_value"],
                "note": top_need["note"],
            }
            if top_need
            else None
        ),
        "roster_construction_score": score,
        "roster_construction_notes": notes,
        "draft_position": serialize_position(context.position),
        "replacement_levels": engine.replacement.as_dict(),
    }
