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
from ..services import season as season_service
from ..services.importer import get_active_league
from ..services.runtime_config import settings_for_user
from .routes_auth import current_user


def settings_dep(
    session: Session = Depends(get_db),
    user=Depends(current_user),
) -> Settings:
    """Settings as they apply to whoever is signed in.

    Every route that touches ESPN goes through this, which is what makes the
    app multi-tenant: two people signed in at once resolve to their own
    leagues and their own credentials. A user without a connection of their
    own falls through to the install-wide one, so a single-user install
    behaves exactly as it did before accounts existed.
    """
    return settings_for_user(session, user, get_settings())


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
    settings: Settings = Depends(settings_dep),
) -> ValuationEngine:
    # Per-user projection source. "espn" (the default) is the native source and
    # is byte-identical to before this option existed; "sleeper"/"fantasypros"
    # pick one source with ESPN fallback per uncovered player; "consensus" blends
    # whatever sources have data. Resolved from the user's saved projection_mode.
    mode = settings.projection_mode or "espn"
    try:
        return build_engine(session, league, active_source=mode)
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
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    draft: DraftSession = Depends(draft_session_dep),
) -> BoardContext:
    """The board for the current state -- what every screen renders.

    Once ESPN has rosters (i.e. the draft happened), they are the truth: every
    rostered player in the league counts as drafted, and mine are the ones on
    my team. Reading the draft log instead would show a post-draft league as
    though nobody had been picked, which made roster needs contradict the
    lineup sitting directly above them.
    """
    espn_roster = season_service.espn_roster_ids(session, league)
    if espn_roster:
        rostered_everywhere: list[dict] = []
        for team in league.teams:
            for entry in team.roster or []:
                player_id = entry.get("espn_player_id")
                if player_id:
                    rostered_everywhere.append(
                        {"espn_player_id": int(player_id), "overall_pick": 0}
                    )
        drafted = rostered_everywhere
        mine = espn_roster
    else:
        drafted = draft_service.drafted_payload(draft)
        mine = draft_service.my_player_ids(draft)

    board, position = build_board(
        engine=engine,
        league=league,
        drafted=drafted,
        my_player_ids=mine,
        my_slot=draft.my_draft_slot,
        rounds=draft.rounds,
    )
    return BoardContext(
        league=league, engine=engine, draft=draft, board=board, position=position
    )
