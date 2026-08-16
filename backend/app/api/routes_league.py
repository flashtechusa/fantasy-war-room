"""Phase 1 -- League Settings screen."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..espn.client import EspnConnectionError
from ..models import HistoricalDraftPick, League, Player, ProjectionSource
from ..services import board as board_service
from ..services.importer import import_league, import_players
from ..services.provider import build_provider
from .deps import league_dep, settings_dep
from .serializers import serialize_history, serialize_league

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/league", tags=["league"])


class ImportRequest(BaseModel):
    include_players: bool = Field(
        default=True, description="Also refresh the player pool and projections."
    )
    include_history: bool = Field(
        default=True, description="Also pull draft results for this and prior seasons."
    )


@router.get("")
def read_league(
    league: League = Depends(league_dep),
) -> dict:
    """The imported league settings, exactly as the engine will use them."""
    return serialize_league(
        league, board_service.league_scoring(league), board_service.league_shape(league)
    )


@router.post("/import", status_code=status.HTTP_201_CREATED)
def run_import(
    payload: ImportRequest | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Connect to ESPN (or the demo provider) and import everything."""
    payload = payload or ImportRequest()
    provider = build_provider(settings)
    try:
        league = import_league(
            session,
            provider=provider,
            settings=settings,
            include_players=payload.include_players,
            include_history=payload.include_history,
        )
    except EspnConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface the real reason to the UI
        log.exception("League import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {exc}",
        ) from exc

    board_service.clear_cache()
    player_count = session.scalar(
        select(func.count()).select_from(Player).where(Player.season == league.season)
    )

    return {
        "imported": True,
        "source": league.source,
        "league": serialize_league(
            league, board_service.league_scoring(league), board_service.league_shape(league)
        ),
        "players_imported": player_count,
    }


@router.post("/players/refresh")
def refresh_players(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Re-pull just the player pool (ADP and injuries move; league rules don't)."""
    try:
        count = import_players(session, league, build_provider(settings), settings)
    except EspnConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    board_service.clear_cache()
    return {"players_imported": count, "season": league.season}


@router.get("/history")
def read_history(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
) -> dict:
    """Previous draft results, when ESPN has them."""
    picks = session.scalars(
        select(HistoricalDraftPick).where(HistoricalDraftPick.league_id == league.id)
    ).all()
    return serialize_history(list(picks))


@router.get("/projection-sources")
def read_projection_sources(session: Session = Depends(get_db)) -> dict:
    """Registered projection providers and their blend weights."""
    sources = session.scalars(select(ProjectionSource)).all()
    return {
        "sources": [
            {
                "key": source.key,
                "label": source.label,
                "weight": source.weight,
                "enabled": source.enabled,
                "updated_at": source.updated_at,
            }
            for source in sources
        ]
    }
