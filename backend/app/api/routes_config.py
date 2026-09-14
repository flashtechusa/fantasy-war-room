"""Runtime league configuration, settable from the UI.

Lets you point the app at a league -- on either platform -- without editing a
file, which is necessary when you're running it somewhere you only have a
browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..espn.client import EspnClient, EspnConnectionError
from ..models import Connection, User
from ..services import board as board_service
from ..services import connections as connection_service
from ..services.provider import build_yahoo_client
from ..services.runtime_config import (
    clear_overrides,
    describe,
    effective_settings,
    write_overrides,
)
from ..yahoo.client import YahooConnectionError
from ..yahoo.oauth import YahooAuthError
from ..services.runtime_config import APP_KEYS
from .deps import connection_dep, current_user, require_admin

router = APIRouter(prefix="/api/config", tags=["config"])


class EspnConfigRequest(BaseModel):
    #: `espn` or `yahoo`. A league lives on one platform; this says which.
    platform: str | None = Field(default=None, pattern="^(espn|yahoo)$")
    espn_league_id: int | None = Field(default=None, ge=1)
    espn_season: int | None = Field(default=None, ge=2000, le=2100)
    espn_swid: str | None = None
    espn_s2: str | None = None
    demo_mode: bool | None = None
    my_team_id: int | None = None
    my_draft_slot: int | None = Field(default=None, ge=1, le=32)
    faab_remaining: int | None = Field(default=None, ge=0, le=100000)
    fantasypros_api_key: str | None = None

    # Yahoo. The tokens are set by the OAuth handshake in `routes_yahoo`, not
    # here -- these are the parts a person types.
    yahoo_league_id: int | None = Field(default=None, ge=1)
    yahoo_client_id: str | None = None
    yahoo_client_secret: str | None = None
    yahoo_redirect_uri: str | None = None

    # Reject unknown fields rather than dropping them. A missing field here
    # meant a saved API key was silently discarded while the app reported
    # success -- failing loudly would have caught it immediately.
    model_config = {"extra": "forbid"}


@router.get("")
def read_config(
    session: Session = Depends(get_db),
    connection: Connection | None = Depends(connection_dep),
) -> dict:
    """Current effective configuration. Credentials are reported as set/unset only."""
    return describe(session, connection=connection)


@router.put("")
def update_config(
    payload: EspnConfigRequest,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
    connection: Connection | None = Depends(connection_dep),
) -> dict:
    """Save league configuration and immediately test the connection.

    Only the fields you send are changed; send an empty string to clear one.
    Installation settings (the Yahoo app, a FantasyPros key) are stored once
    for the server; everything league-shaped lands on your own connection.
    """
    values = payload.model_dump(exclude_unset=True)

    # Installation-wide settings belong to whoever runs the server. On a
    # single-user install that is the person at the keyboard; on a hosted one,
    # letting any account rewrite the Yahoo app or flip demo mode would affect
    # everybody.
    installation_values = {key: value for key, value in values.items() if key in APP_KEYS}
    if installation_values and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the account that set this server up can change "
                f"{', '.join(sorted(installation_values))}."
            ),
        )
    write_overrides(session, values)
    connection = connection_service.apply_config_values(
        session, user, connection, values
    )
    session.commit()
    board_service.clear_cache()

    settings = effective_settings(session, connection=connection)
    result: dict = {
        "saved": True,
        "config": describe(session, connection=connection),
        "connection": None,
    }

    if settings.is_yahoo:
        if settings.can_reach_yahoo:
            try:
                result["connection"] = build_yahoo_client(settings).check_connection()
            except (YahooConnectionError, YahooAuthError) as exc:
                result["connection"] = {"connected": False, "detail": str(exc)}
    elif settings.can_reach_espn:
        client = EspnClient(
            league_id=settings.espn_league_id,
            season=settings.espn_season,
            swid=settings.espn_swid,
            espn_s2=settings.espn_s2,
        )
        try:
            result["connection"] = {"connected": True, **client.check_connection()}
        except EspnConnectionError as exc:
            result["connection"] = {"connected": False, "detail": str(exc)}
    return result


@router.delete("")
def reset_config(
    session: Session = Depends(get_db),
    connection: Connection | None = Depends(connection_dep),
    user: User = Depends(require_admin),
) -> dict:
    """Drop UI-entered installation settings and fall back to the environment.

    Connections are left alone: they are your leagues, and dropping them here
    would delete everything imported under them as a side effect of clearing a
    FantasyPros key.
    """
    clear_overrides(session)
    board_service.clear_cache()
    return {"cleared": True, "config": describe(session, connection=connection)}
