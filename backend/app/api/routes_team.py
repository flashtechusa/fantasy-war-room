"""Phase 6 -- My Team."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..engine.roster import (
    bye_week_conflicts,
    build_optimal_lineup,
    positional_strength,
    roster_construction_score,
)
from ..services import draft as draft_service
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
def read_my_team(context: BoardContext = Depends(board_dep)) -> dict:
    """Starters, bench, strengths, weaknesses, bye conflicts and what to draft next."""
    engine = context.engine
    my_ids = draft_service.my_player_ids(context.draft)
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
