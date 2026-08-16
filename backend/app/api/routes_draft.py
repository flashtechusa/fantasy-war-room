"""Phase 5 -- Live Draft."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..espn.client import EspnConnectionError
from ..models import DraftSession, League
from ..services import draft as draft_service
from .deps import BoardContext, board_dep, draft_session_dep, league_dep, settings_dep
from .serializers import (
    serialize_board_meta,
    serialize_draft_session,
    serialize_pick,
    serialize_position,
    serialize_valuation,
)

router = APIRouter(prefix="/api/draft", tags=["draft"])

#: Last ESPN sync per draft session id, so polling can't hammer ESPN.
_last_sync: dict[int, float] = {}


class PickRequest(BaseModel):
    espn_player_id: int
    overall_pick: int | None = Field(
        default=None, description="Defaults to the next open pick."
    )


class SlotRequest(BaseModel):
    my_draft_slot: int = Field(ge=1, le=32)


@router.get("")
def read_draft(draft: DraftSession = Depends(draft_session_dep)) -> dict:
    """Current draft state: picks made, who is on the clock, my upcoming picks."""
    return serialize_draft_session(draft)


@router.get("/state")
def read_state(context: BoardContext = Depends(board_dep)) -> dict:
    """Draft clock + board metadata, without the full player list."""
    return {
        "draft": serialize_draft_session(context.draft),
        "draft_position": serialize_position(context.position),
        "meta": serialize_board_meta(context.board),
    }


@router.post("/pick", status_code=status.HTTP_201_CREATED)
def make_pick(
    payload: PickRequest,
    session: Session = Depends(get_db),
    draft: DraftSession = Depends(draft_session_dep),
    league: League = Depends(league_dep),
) -> dict:
    """Mark a player as drafted. This is the manual path and always works."""
    try:
        pick = draft_service.record_pick(
            session,
            draft,
            espn_player_id=payload.espn_player_id,
            overall_pick=payload.overall_pick,
            league_season=league.season,
        )
    except draft_service.DraftError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"pick": serialize_pick(pick), "draft": serialize_draft_session(draft)}


@router.delete("/pick/{overall_pick}")
def delete_pick(
    overall_pick: int,
    session: Session = Depends(get_db),
    draft: DraftSession = Depends(draft_session_dep),
) -> dict:
    if not draft_service.remove_pick(session, draft, overall_pick):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No pick {overall_pick} recorded"
        )
    return {"removed": overall_pick, "draft": serialize_draft_session(draft)}


@router.post("/undo")
def undo(
    session: Session = Depends(get_db),
    draft: DraftSession = Depends(draft_session_dep),
) -> dict:
    pick = draft_service.undo_last_pick(session, draft)
    if pick is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No picks to undo"
        )
    return {"undone": serialize_pick(pick), "draft": serialize_draft_session(draft)}


@router.post("/reset")
def reset(
    session: Session = Depends(get_db),
    draft: DraftSession = Depends(draft_session_dep),
) -> dict:
    """Clear every pick. Used to restart a mock, or to recover from a bad sync."""
    draft_service.reset_draft(session, draft)
    return {"reset": True, "draft": serialize_draft_session(draft)}


@router.post("/slot")
def set_slot(
    payload: SlotRequest,
    session: Session = Depends(get_db),
    draft: DraftSession = Depends(draft_session_dep),
) -> dict:
    updated = draft_service.set_my_slot(session, draft, payload.my_draft_slot)
    return {"draft": serialize_draft_session(updated)}


@router.post("/sync")
def sync(
    session: Session = Depends(get_db),
    draft: DraftSession = Depends(draft_session_dep),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Optional: pull picks from ESPN's live draft board.

    Rate-limited to one call per `FWR_DRAFT_POLL_INTERVAL` seconds.  Manual
    picks already recorded are never overwritten.
    """
    now = time.monotonic()
    last = _last_sync.get(draft.id, 0.0)
    wait = settings.draft_poll_interval - (now - last)
    if wait > 0:
        return {
            "synced": False,
            "reason": f"Rate limited; try again in {wait:.0f}s",
            "draft": serialize_draft_session(draft),
        }

    try:
        result = draft_service.sync_from_espn(session, draft, league, settings)
    except EspnConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    _last_sync[draft.id] = now
    return {"synced": True, **result, "draft": serialize_draft_session(draft)}


@router.get("/recommendations")
def recommendations(
    context: BoardContext = Depends(board_dep),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """BEST PICK NOW plus the top alternatives, each with its reasoning."""
    available = [v for v in context.board.valuations if not v.is_drafted]
    top = available[:limit]

    best = top[0] if top else None
    return {
        "best_pick": serialize_valuation(best, detail=True) if best else None,
        "top_picks": [serialize_valuation(v, detail=True) for v in top],
        "draft_position": serialize_position(context.position),
        "meta": serialize_board_meta(context.board),
    }
