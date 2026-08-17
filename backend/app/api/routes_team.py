"""Phase 6 -- My Team."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..engine.roster import (
    bye_week_conflicts,
    build_optimal_lineup,
    positional_strength,
    roster_construction_score,
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
