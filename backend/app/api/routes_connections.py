"""Connected leagues, and switching between them.

One person, several leagues, possibly on different platforms: this is the API
behind the switcher. Creating a connection does not import anything -- it says
"this league is mine and here is how to read it" -- so the import flow on the
League screen is unchanged and works the same on whichever connection is
active.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import User
from ..services import board as board_service
from ..services import connections as connection_service
from ..services.connections import ConnectionError_
from .deps import current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connections", tags=["connections"])


class ConnectionRequest(BaseModel):
    platform: str = Field(pattern="^(espn|yahoo|demo)$")
    season: int | None = Field(default=None, ge=2000, le=2100)
    league_id: int | None = Field(default=None, ge=1)
    label: str = Field(default="", max_length=120)
    #: ESPN private-league cookies. Yahoo connections are authorised through
    #: the OAuth flow instead, so they are never posted here.
    espn_swid: str | None = None
    espn_s2: str | None = None

    model_config = {"extra": "forbid"}


class ConnectionUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    league_id: int | None = Field(default=None, ge=1)
    season: int | None = Field(default=None, ge=2000, le=2100)
    espn_swid: str | None = None
    espn_s2: str | None = None
    my_team_id: int | None = None
    my_draft_slot: int | None = Field(default=None, ge=1, le=32)
    faab_remaining: int | None = Field(default=None, ge=0, le=100000)

    model_config = {"extra": "forbid"}


def _payload(session: Session, user: User) -> dict:
    rows = connection_service.list_connections(session, user)
    active = connection_service.active_connection(session, user)
    return {
        "connections": [connection_service.describe(row) for row in rows],
        "active_connection_id": active.id if active else None,
    }


@router.get("")
def list_connections(
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Every league this account has connected."""
    result = _payload(session, user)
    session.commit()
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def add_connection(
    payload: ConnectionRequest,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Connect a league and switch to it.

    A Yahoo connection can be created before it is authorised -- you need
    somewhere to hang the tokens the OAuth flow is about to produce.
    """
    try:
        connection = connection_service.create_connection(
            session,
            user,
            platform=payload.platform,
            season=payload.season or settings.espn_season,
            platform_league_id=payload.league_id,
            label=payload.label,
            espn_swid=payload.espn_swid or "",
            espn_s2=payload.espn_s2 or "",
        )
    except ConnectionError_ as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    session.commit()
    board_service.clear_cache()
    return {"connection": connection_service.describe(connection), **_payload(session, user)}


@router.post("/{connection_id}/activate")
def activate_connection(
    connection_id: int,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Switch which league every screen is about."""
    try:
        connection = connection_service.get_connection(session, user, connection_id)
    except ConnectionError_ as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    connection_service.set_active(session, user, connection)
    session.commit()
    # The engine is cached per league; switching leagues has to drop it.
    board_service.clear_cache()
    return {"connection": connection_service.describe(connection), **_payload(session, user)}


@router.patch("/{connection_id}")
def update_connection(
    connection_id: int,
    payload: ConnectionUpdate,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Change a connection's league, credentials or this manager's details."""
    try:
        connection = connection_service.get_connection(session, user, connection_id)
    except ConnectionError_ as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    values = payload.model_dump(exclude_unset=True)
    if "league_id" in values:
        values["platform_league_id"] = values.pop("league_id")
    connection_service.apply_values(session, connection, values)
    session.commit()
    board_service.clear_cache()
    return {"connection": connection_service.describe(connection), **_payload(session, user)}


@router.delete("/{connection_id}")
def delete_connection(
    connection_id: int,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Disconnect a league and delete everything imported under it."""
    try:
        connection = connection_service.get_connection(session, user, connection_id)
    except ConnectionError_ as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    connection_service.delete_connection(session, user, connection)
    session.commit()
    board_service.clear_cache()
    return {"deleted": True, **_payload(session, user)}
