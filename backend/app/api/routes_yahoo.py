"""Connecting a Yahoo account, from the browser.

ESPN needs two cookies pasted in. Yahoo needs an OAuth handshake, which is more
steps but no harder: register a free app at developer.yahoo.com, paste its
Client ID and Secret into the League screen, follow the link Yahoo gives you,
and paste the code back. These endpoints are the three moving parts of that --
start, complete, disconnect -- plus a league picker so nobody has to go hunting
for their league id.

Tokens are written straight to the local configuration store and are never
returned by any of these endpoints.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import Connection, User
from ..services import board as board_service
from ..services import connections as connection_service
from ..services.runtime_config import describe, write_overrides
from ..services.yahoo_auth import build_client, build_oauth, clear_tokens, store_tokens
from ..yahoo.client import YahooConnectionError, fetch_user_leagues
from ..yahoo.oauth import OUT_OF_BAND, YahooAuthError
from .deps import connection_dep, current_user, require_admin, settings_dep

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/yahoo", tags=["yahoo"])


class YahooAppRequest(BaseModel):
    """The credentials Yahoo issues for a registered app."""

    yahoo_client_id: str = Field(min_length=10)
    yahoo_client_secret: str = Field(min_length=10)
    #: `oob` (Yahoo shows a code to paste) or an https URL registered on the app.
    yahoo_redirect_uri: str = "oob"
    yahoo_league_id: int | None = Field(default=None, ge=1)

    model_config = {"extra": "forbid"}


class YahooCodeRequest(BaseModel):
    code: str = Field(min_length=4)

    model_config = {"extra": "forbid"}


@router.get("/status")
def read_status(
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    connection: Connection | None = Depends(connection_dep),
) -> dict:
    """What is configured, what is connected, and what is still missing."""
    config = describe(session, connection=connection)
    connection = None
    if settings.can_reach_yahoo:
        try:
            connection = build_client(settings).check_connection()
        except (YahooConnectionError, YahooAuthError) as exc:
            connection = {"connected": False, "detail": str(exc)}
    return {
        "platform": settings.platform,
        "app_configured": settings.has_yahoo_app,
        "connected": settings.has_yahoo_credentials,
        "league_id": settings.yahoo_league_id,
        "season": settings.espn_season,
        "redirect_uri": settings.yahoo_redirect_uri,
        "out_of_band": settings.yahoo_redirect_uri == OUT_OF_BAND,
        "config": config,
        "connection": connection,
    }


@router.put("/app")
def save_app(
    payload: YahooAppRequest,
    session: Session = Depends(get_db),
    user: User = Depends(require_admin),
    connection: Connection | None = Depends(connection_dep),
) -> dict:
    """Store the Yahoo app credentials and point this account at Yahoo.

    The Client ID and Secret are installation-wide -- one Yahoo developer app
    serves every user of this server -- while the league belongs to whoever is
    signed in.
    """
    values = payload.model_dump(exclude_unset=True)
    write_overrides(session, values)

    connection = connection_service.apply_config_values(
        session,
        user,
        connection if connection is None or connection.is_yahoo else None,
        {"platform": "yahoo", **{k: v for k, v in values.items() if k == "yahoo_league_id"}},
    )
    session.commit()
    board_service.clear_cache()
    return {"saved": True, "config": describe(session, connection=connection)}


@router.post("/auth/start")
def start_auth(settings: Settings = Depends(settings_dep)) -> dict:
    """The Yahoo URL to send the user to."""
    oauth = build_oauth(settings)
    try:
        url = oauth.authorize_url()
    except YahooAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {
        "authorize_url": url,
        "redirect_uri": oauth.redirect_uri,
        "out_of_band": oauth.redirect_uri == OUT_OF_BAND,
        "instructions": (
            "Open the link, approve access, then paste the code Yahoo shows you back here."
            if oauth.redirect_uri == OUT_OF_BAND
            else "Open the link and approve access. Yahoo will send you back here."
        ),
    }


@router.post("/auth/complete")
def complete_auth(
    payload: YahooCodeRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    user: User = Depends(current_user),
    connection: Connection | None = Depends(connection_dep),
) -> dict:
    """Trade the code Yahoo showed the user for tokens, and store them."""
    oauth = build_oauth(settings, persist=False)
    try:
        tokens = oauth.exchange_code(payload.code)
    except YahooAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    connection = _yahoo_connection(session, user, connection)
    store_tokens(session, connection, tokens)
    session.commit()
    board_service.clear_cache()
    log.info("Connected a Yahoo account for user %s.", user.id)
    return {"connected": True, "config": describe(session, connection=connection)}


def _yahoo_connection(
    session: Session, user: User, connection: Connection | None
) -> Connection:
    """The connection these Yahoo tokens belong on.

    An account whose active connection is an ESPN league gets a new Yahoo one
    rather than having its ESPN league overwritten -- holding both at once is
    the point of connections.
    """
    if connection is not None and connection.is_yahoo:
        return connection
    for existing in connection_service.list_connections(session, user):
        if existing.is_yahoo:
            connection_service.set_active(session, user, existing)
            return existing
    return connection_service.create_connection(
        session, user, platform="yahoo", season=get_settings().espn_season
    )


@router.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(
    code: str = Query(default=""),
    error: str = Query(default=""),
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    user: User = Depends(current_user),
    connection: Connection | None = Depends(connection_dep),
) -> HTMLResponse:
    """Where Yahoo lands when a real redirect URI is configured.

    Returns a page rather than JSON because a browser is what arrives here.
    """
    if error or not code:
        return HTMLResponse(
            _page("Yahoo did not authorise the app", error or "No code was returned."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    oauth = build_oauth(settings, persist=False)
    try:
        tokens = oauth.exchange_code(code)
    except YahooAuthError as exc:
        return HTMLResponse(
            _page("Could not finish connecting to Yahoo", str(exc)),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    store_tokens(session, _yahoo_connection(session, user, connection), tokens)
    session.commit()
    board_service.clear_cache()
    return HTMLResponse(
        _page("Yahoo connected", "You can close this tab and go back to the app.")
    )


@router.delete("/auth")
def disconnect(
    session: Session = Depends(get_db),
    connection: Connection | None = Depends(connection_dep),
) -> dict:
    """Forget this connection's Yahoo tokens, keeping the app registration."""
    if connection is not None:
        clear_tokens(session, connection)
        session.commit()
    board_service.clear_cache()
    return {"connected": False, "config": describe(session, connection=connection)}


@router.get("/leagues")
def list_leagues(settings: Settings = Depends(settings_dep)) -> dict:
    """The connected account's NFL leagues for the configured season.

    Saves the user hunting for a league id, and doubles as proof the connection
    works before anything is imported.
    """
    if not settings.has_yahoo_credentials:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Not connected to Yahoo yet.",
        )
    try:
        leagues = fetch_user_leagues(build_oauth(settings), settings.espn_season)
    except (YahooConnectionError, YahooAuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    season = settings.espn_season
    for_season = [entry for entry in leagues if entry["season"] == season]
    return {
        "season": season,
        # Yahoo returns whatever seasons the account is in. Showing them all
        # when none match the configured season means a mis-set season looks
        # like a mis-set season, not like "you have no leagues".
        "leagues": for_season or leagues,
        "filtered_to_season": bool(for_season),
    }


def _page(title: str, detail: str) -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>"
        "<body style=\"font-family:system-ui;margin:3rem auto;max-width:32rem;"
        "line-height:1.5;color:#0f172a\">"
        f"<h1 style='font-size:1.25rem'>{title}</h1><p>{detail}</p>"
        "<p><a href='/'>Back to Fantasy War Room</a></p></body>"
    )
