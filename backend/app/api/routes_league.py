"""Phase 1 -- League Settings screen."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..espn.client import EspnConnectionError
from ..models import HistoricalDraftPick, League, Player, ProjectionSource
from ..projections.espn_public import EspnPublicError
from ..projections.fantasypros import FantasyProsError
from ..services import board as board_service
from ..services import projections as projection_service
from ..services.importer import import_league, import_players
from ..services.provider import build_provider
from ..yahoo.client import YahooConnectionError
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
    except (EspnConnectionError, YahooConnectionError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface the real reason to the UI
        log.exception("League import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {exc}",
        ) from exc

    projections = None
    if payload.include_players and league.source not in {"espn", "demo"}:
        # A platform that publishes no projections of its own (Yahoo) would
        # otherwise leave every player valued at zero, which looks like a broken
        # app rather than a missing data source. Pulling ESPN's public
        # projections here is what makes the first import land on a usable
        # board. It is best-effort: a failure is reported, not fatal, because
        # the league itself imported fine.
        try:
            projections = projection_service.import_espn_public(session, league)
            session.commit()
        except EspnPublicError as exc:
            log.warning("Could not load public projections: %s", exc)
            projections = {"source": "espn_public", "error": str(exc)}

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
        "projections": projections,
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
    except (EspnConnectionError, YahooConnectionError) as exc:
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


@router.post("/projections/fantasypros")
def import_fantasypros_projections(
    week: str = Query("draft", description="'draft' for season totals, or a week number"),
    weight: float = Query(1.0, ge=0.0, le=10.0),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Pull FantasyPros projections using this installation's own API key.

    Off unless a key is configured. The report includes what failed to match,
    because a half-matched import looks identical to a working one from the
    outside and would quietly skew the blend.
    """
    try:
        report = projection_service.import_fantasypros(
            session,
            league,
            api_key=settings.fantasypros_api_key or "",
            week=int(week) if str(week).isdigit() else "draft",
            weight=weight,
        )
    except FantasyProsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    session.commit()
    board_service.clear_cache()
    return report


@router.post("/projections/espn-public")
def import_public_projections(
    weight: float = Query(1.0, ge=0.0, le=10.0),
    limit: int = Query(900, ge=50, le=2000),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
) -> dict:
    """Pull ESPN's public projections and match them onto the imported pool.

    Needs no credentials, and is how a Yahoo league gets projections at all --
    Yahoo publishes none. Harmless on an ESPN league too, where it becomes a
    second opinion to blend.
    """
    try:
        report = projection_service.import_espn_public(
            session, league, weight=weight, limit=limit
        )
    except EspnPublicError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    session.commit()
    board_service.clear_cache()
    return report
