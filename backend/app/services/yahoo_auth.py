"""Keeping a Yahoo connection alive between requests.

The OAuth machinery in `app.yahoo.oauth` deliberately knows nothing about
storage: it holds tokens and calls back when they change. This is the other
half -- reading those tokens off the user's connection and writing refreshed
ones back, so a connection made once in the browser survives restarts and the
hourly token expiry with no further interaction.

Tokens belong to a person, not to the installation, so they live on the
`Connection` row alongside that user's league. The Yahoo *app* credentials
(client id and secret) are the opposite -- one developer app per installation,
shared by everyone on it -- so those stay in `AppConfig`.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import Connection
from ..yahoo.client import YahooClient, YahooConnectionError
from ..yahoo.oauth import YahooOAuth, YahooTokens
from .connections import apply_values, clear_yahoo_tokens
from .runtime_config import effective_settings

log = logging.getLogger(__name__)


def tokens_from_settings(settings: Settings) -> YahooTokens:
    return YahooTokens(
        access_token=settings.yahoo_access_token or "",
        refresh_token=settings.yahoo_refresh_token or "",
        expires_at=float(settings.yahoo_token_expires or 0.0),
        guid=settings.yahoo_guid or "",
    )


def store_tokens(session: Session, connection: Connection, tokens: YahooTokens) -> None:
    """Persist tokens onto the connection they belong to."""
    apply_values(
        session,
        connection,
        {
            "yahoo_access_token": tokens.access_token,
            "yahoo_refresh_token": tokens.refresh_token,
            "yahoo_token_expires": tokens.expires_at,
            "yahoo_guid": tokens.guid,
        },
    )


def clear_tokens(session: Session, connection: Connection) -> None:
    """Disconnect a Yahoo account, leaving the installation's app registration."""
    clear_yahoo_tokens(session, connection)


def build_oauth(settings: Settings | None = None, persist: bool = True) -> YahooOAuth:
    """A YahooOAuth wired to persist any token it refreshes.

    The refresh callback opens its own short session rather than borrowing the
    request's: a refresh can happen in the middle of an import, and losing the
    new token because the surrounding transaction rolled back would sign the
    user out for no visible reason.
    """
    settings = settings or get_settings()
    connection_id = settings.active_connection_id

    def _save(tokens: YahooTokens) -> None:
        if not persist or connection_id is None:
            return
        from ..db import session_scope

        try:
            with session_scope() as session:
                connection = session.get(Connection, connection_id)
                if connection is not None:
                    store_tokens(session, connection, tokens)
        except Exception as exc:  # pragma: no cover - storage is best effort
            log.warning("Could not persist refreshed Yahoo tokens: %s", exc)

    return YahooOAuth(
        client_id=settings.yahoo_client_id or "",
        client_secret=settings.yahoo_client_secret or "",
        redirect_uri=settings.yahoo_redirect_uri or "oob",
        tokens=tokens_from_settings(settings),
        on_refresh=_save,
    )


def build_client(settings: Settings | None = None) -> YahooClient:
    """A YahooClient for the active connection. Raises if it cannot be built."""
    settings = settings or get_settings()
    if settings.yahoo_league_id is None:
        raise YahooConnectionError(
            "No Yahoo league selected. Enter your league id on the League screen -- it is "
            "the number in your league URL."
        )
    if not settings.has_yahoo_app:
        raise YahooConnectionError(
            "This installation has no Yahoo app configured. Whoever runs the server needs to "
            "create one at developer.yahoo.com (Fantasy Sports, Read permission) and add its "
            "Client ID and Secret."
        )
    if not settings.has_yahoo_credentials:
        raise YahooConnectionError(
            "Not connected to Yahoo yet. Use 'Connect Yahoo' on the League screen."
        )
    return YahooClient(
        league_id=settings.yahoo_league_id,
        season=settings.espn_season,
        oauth=build_oauth(settings),
    )


def settings_with_overrides(session: Session) -> Settings:
    """Convenience for callers that need installation settings only."""
    return effective_settings(session, get_settings())
