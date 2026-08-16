"""Phases 3 & 4 -- the Draft Board."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..espn.constants import FLEX_ELIGIBLE
from .deps import BoardContext, board_dep
from .serializers import serialize_board_meta, serialize_position, serialize_valuation

router = APIRouter(prefix="/api/players", tags=["players"])

VALID_FILTERS = {"ALL", "QB", "RB", "WR", "TE", "FLEX", "K", "DST"}


@router.get("")
def read_board(
    context: BoardContext = Depends(board_dep),
    position: str = Query("ALL", description="ALL, QB, RB, WR, TE, FLEX, K or DST"),
    search: str | None = Query(None, description="Case-insensitive name/team search"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    include_drafted: bool = Query(False),
    detail: bool = Query(False, description="Include score components and reasoning"),
) -> dict:
    """The ranked board, filtered and paged for the phone."""
    position = (position or "ALL").upper()
    if position not in VALID_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"position must be one of {sorted(VALID_FILTERS)}",
        )

    rows = context.board.valuations
    if not include_drafted:
        rows = [row for row in rows if not row.is_drafted]

    if position == "FLEX":
        rows = [row for row in rows if row.position in FLEX_ELIGIBLE]
    elif position != "ALL":
        rows = [row for row in rows if row.position == position]

    if search:
        needle = search.strip().lower()
        rows = [
            row
            for row in rows
            if needle in row.name.lower() or needle in (row.pro_team or "").lower()
        ]

    total = len(rows)
    page = rows[offset : offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "filter": position,
        "search": search,
        "players": [serialize_valuation(row, detail=detail) for row in page],
        "draft_position": serialize_position(context.position),
        "meta": serialize_board_meta(context.board),
    }


@router.get("/{espn_player_id}")
def read_player(
    espn_player_id: int,
    context: BoardContext = Depends(board_dep),
) -> dict:
    """One player, with the full reasoning and score breakdown."""
    match = next(
        (v for v in context.board.valuations if v.espn_player_id == espn_player_id), None
    )
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No player {espn_player_id}"
        )

    engine = context.engine
    source = next(
        (p for p in engine.players if p.espn_player_id == espn_player_id), None
    )
    scoring_breakdown = []
    if source is not None and source.raw_stats:
        scoring_breakdown = [
            {
                "stat_id": item.stat_id,
                "abbrev": item.abbrev,
                "label": item.label,
                "stat_value": item.stat_value,
                "points_per": item.points_per,
                "points": item.points,
            }
            for item in engine.scoring.breakdown(source.raw_stats, source.position)
        ]

    payload = serialize_valuation(match, detail=True)
    payload["scoring_breakdown"] = scoring_breakdown
    payload["draft_position"] = serialize_position(context.position)
    return payload
