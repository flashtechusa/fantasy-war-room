"""Phase 1 -- League Settings screen."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..espn.client import EspnConnectionError, EspnNotConfigured
from ..models import (
    HistoricalDraftPick,
    League,
    Player,
    PlayerProjection,
    ProjectionSource,
    User,
)
from ..projections.fantasypros import FantasyProsError
from ..projections.sleeper import SOURCE_KEY as SLEEPER_SOURCE_KEY
from ..projections.sleeper import SleeperError
from ..services import board as board_service
from ..services import projections as projection_service
from ..services import runtime_config
from ..services.importer import import_league, import_players
from ..services.provider import build_provider
from .deps import league_dep, settings_dep
from .routes_auth import require_user
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
    try:
        # Inside the try: choosing a provider is the step that fails when the
        # account has no league connected, and that has to reach the user as
        # an instruction rather than a 500.
        provider = build_provider(settings)
        league = import_league(
            session,
            provider=provider,
            settings=settings,
            include_players=payload.include_players,
            include_history=payload.include_history,
        )
    except EspnNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
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
        select(func.count())
        .select_from(Player)
        .where(Player.season == league.season, Player.source == league.source)
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
    except EspnNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
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


# ---------------------------------------------------------------------------
# Sleeper projections -- an isolated, optional, per-user comparison source
# ---------------------------------------------------------------------------


class SleeperToggle(BaseModel):
    enabled: bool


def _sleeper_status(session: Session, league: League, user: User) -> dict:
    """Everything the projection-source UI needs, secrets-free."""
    source = session.scalars(
        select(ProjectionSource).where(ProjectionSource.key == SLEEPER_SOURCE_KEY)
    ).first()
    matched = session.scalar(
        select(func.count(PlayerProjection.id))
        .join(Player, Player.id == PlayerProjection.player_id)
        .where(
            PlayerProjection.source_key == SLEEPER_SOURCE_KEY,
            Player.season == league.season,
            Player.source == league.source,
        )
    ) or 0
    pool_size = session.scalar(
        select(func.count(Player.id)).where(
            Player.season == league.season, Player.source == league.source
        )
    ) or 0
    config = runtime_config.user_config(session, user)
    use_sleeper = bool(getattr(config, "use_sleeper_projections", False)) if config else False
    return {
        # The per-user toggle, and therefore the source the board is using now.
        "use_sleeper_projections": use_sleeper,
        "active_projection_source": "sleeper" if use_sleeper else "default",
        "sleeper": {
            "imported": matched > 0,
            "players_matched": matched,
            "pool_size": pool_size,
            "coverage": round(matched / pool_size, 3) if pool_size else 0.0,
            "updated_at": source.updated_at if source else None,
            "scope": "QB/RB/WR/TE re-scored under your rules; K and D/ST stay on the existing source.",
        },
    }


@router.get("/projections/sleeper")
def sleeper_status(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Whether Sleeper projections are imported, and whether this user uses them."""
    return _sleeper_status(session, league, user)


@router.post("/projections/sleeper/import", status_code=status.HTTP_201_CREATED)
def import_sleeper_projections(
    week: int | None = Query(None, description="A week number, or omit for season totals."),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Fetch Sleeper's raw component projections and store them (isolated).

    Stored disabled, so this never changes the default board. It is used only
    when this user turns the toggle on. Re-runnable to refresh the numbers.
    """
    try:
        report = projection_service.import_sleeper(session, league, week=week)
    except SleeperError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    session.commit()
    board_service.clear_cache()
    return {**report, "status": _sleeper_status(session, league, user)}


@router.post("/projections/sleeper/toggle")
def toggle_sleeper_projections(
    payload: SleeperToggle,
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Turn 'Use Sleeper Projections' on or off for the signed-in user.

    Turning it on imports Sleeper's projections first if none are stored yet, so
    the board has something to re-score. Off restores the default source exactly.
    """
    if payload.enabled:
        already = session.scalar(
            select(func.count(PlayerProjection.id))
            .join(Player, Player.id == PlayerProjection.player_id)
            .where(
                PlayerProjection.source_key == SLEEPER_SOURCE_KEY,
                Player.season == league.season,
                Player.source == league.source,
            )
        ) or 0
        if not already:
            try:
                projection_service.import_sleeper(session, league)
                session.commit()
            except SleeperError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
                ) from exc

    runtime_config.set_use_sleeper_projections(session, user, payload.enabled)
    board_service.clear_cache()
    return _sleeper_status(session, league, user)
