"""Shared FastAPI dependencies.

The chain every request walks: **user -> connection -> settings -> league**.

That order is the whole tenancy design. `settings_dep` used to describe the
process; now it describes the signed-in user's active connection, and because
every service already took a `Settings`, they became tenant-safe without being
rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..engine.draft_math import DraftPosition
from ..engine.valuation import BoardResult, ValuationEngine
from ..models import Connection, DraftSession, League, User
from ..services import accounts as account_service
from ..services.accounts import SESSION_COOKIE
from ..services import connections as connection_service
from ..services import draft as draft_service
from ..services.board import LeagueNotImported, build_board, build_engine
from ..services import season as season_service
from ..services.importer import get_active_league
from ..services.runtime_config import effective_settings


def current_user(
    request: Request,
    session: Session = Depends(get_db),
) -> User:
    """Who this request is for.

    Single-user installs resolve to one implicit local account with no login,
    so nothing about running this on your own machine changes. Multi-user
    installs require the session cookie and reject the request without it.
    """
    settings = get_settings()
    if not settings.multi_user:
        user = account_service.get_or_create_local_user(session)
        session.commit()
        return user

    token = request.cookies.get(account_service.SESSION_COOKIE, "")
    user = account_service.user_for_token(session, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
        )
    session.commit()
    return user


def optional_user(
    request: Request,
    session: Session = Depends(get_db),
) -> User | None:
    """The signed-in user, or None -- without rejecting the request.

    For endpoints that must answer an anonymous caller: a container health
    probe has no cookie, and a deploy platform that gets a 401 from its health
    check marks the release failed and rolls it back.
    """
    settings = get_settings()
    if not settings.multi_user:
        user = account_service.get_or_create_local_user(session)
        session.commit()
        return user
    user = account_service.user_for_token(session, request.cookies.get(SESSION_COOKIE, ""))
    session.commit()
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """Guards installation-wide settings and anything that touches the server.

    A hosted install has one operator and many users. The Yahoo developer app,
    the FantasyPros key, demo mode and self-update belong to the operator --
    letting any account that signed up change them would be handing strangers
    the server.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the account that set this server up can change that.",
        )
    return user


def connection_dep(
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Connection | None:
    """The league this user is currently looking at.

    A fresh account has none, which is not an error -- it is the state the
    "add a league" screen exists for. A self-hosted install whose league is
    already in `.env` gets one seeded here on first run.
    """
    connection = connection_service.active_connection(session, user)
    if connection is None:
        connection = connection_service.ensure_default_connection(session, user, get_settings())
        session.commit()
    return connection


def settings_dep(
    session: Session = Depends(get_db),
    connection: Connection | None = Depends(connection_dep),
) -> Settings:
    """Settings describing the active connection, not the process.

    Every route that touches a platform goes through this, so credentials typed
    into the app take effect immediately without a restart -- and belong to the
    user who typed them.
    """
    return effective_settings(session, get_settings(), connection)


def league_dep(
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    connection: Connection | None = Depends(connection_dep),
) -> League:
    league = get_active_league(session, settings, connection)
    if league is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No league imported yet. POST /api/league/import (or set FWR_DEMO_MODE=true "
                "to explore with synthetic data)."
                if connection is not None
                else "No league connected yet. Add one on the League screen."
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
