"""Shared FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..engine.draft_math import DraftPosition
from ..engine.valuation import BoardResult, ValuationEngine
from ..models import DraftSession, League
from ..services import draft as draft_service
from ..services.board import LeagueNotImported, build_board, build_engine
from ..services.importer import get_active_league
from ..services.runtime_config import effective_settings


def settings_dep(session: Session = Depends(get_db)) -> Settings:
    """Environment settings with any UI-entered overrides applied.

    Every route that touches ESPN goes through this, so credentials typed into
    the app take effect immediately without a restart.
    """
    return effective_settings(session, get_settings())


def league_dep(
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> League:
    league = get_active_league(session, settings)
    if league is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No league imported yet. POST /api/league/import (or set FWR_DEMO_MODE=true "
                "to explore with synthetic data)."
            ),
        )
    return league


def engine_dep(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
) -> ValuationEngine:
    try:
        return build_engine(session, league)
    except LeagueNotImported as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def draft_session_dep(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> DraftSession:
    return draft_service.get_or_create_session(session, league, settings)


@dataclass
class BoardContext:
    league: League
    engine: ValuationEngine
    draft: DraftSession
    board: BoardResult
    position: DraftPosition


def board_dep(
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    draft: DraftSession = Depends(draft_session_dep),
) -> BoardContext:
    """The board for the *current* draft state -- what every screen renders."""
    board, position = build_board(
        engine=engine,
        league=league,
        drafted=draft_service.drafted_payload(draft),
        my_player_ids=draft_service.my_player_ids(draft),
        my_slot=draft.my_draft_slot,
        rounds=draft.rounds,
    )
    return BoardContext(
        league=league, engine=engine, draft=draft, board=board, position=position
    )
