"""Keeping the Yahoo connection alive between requests.

The OAuth machinery in `app.yahoo.oauth` deliberately knows nothing about
storage: it holds tokens and calls back when they change.  This is the other
half -- reading those tokens out of configuration and writing refreshed ones
back, so a connection made once in the browser survives restarts and the hourly
token expiry with no further interaction.

Tokens are stored exactly where ESPN's cookies are: the local SQLite file (if
they were entered in the app) or `.env` (if they were put there by hand). They
are never returned by the API and never logged.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..yahoo.client import YahooClient, YahooConnectionError
from ..yahoo.oauth import YahooOAuth, YahooTokens
from .runtime_config import effective_settings, write_overrides

log = logging.getLogger(__name__)


def tokens_from_settings(settings: Settings) -> YahooTokens:
    return YahooTokens(
        access_token=settings.yahoo_access_token or "",
        refresh_token=settings.yahoo_refresh_token or "",
        expires_at=float(settings.yahoo_token_expires or 0.0),
        guid=settings.yahoo_guid or "",
    )


def store_tokens(session: Session, tokens: YahooTokens) -> None:
    """Persist tokens as runtime configuration."""
    write_overrides(
        session,
        {
            "yahoo_access_token": tokens.access_token,
            "yahoo_refresh_token": tokens.refresh_token,
            "yahoo_token_expires": tokens.expires_at,
            "yahoo_guid": tokens.guid,
        },
    )


def clear_tokens(session: Session) -> None:
    """Disconnect the Yahoo account, leaving the app registration in place."""
    write_overrides(
        session,
        {
            "yahoo_access_token": None,
            "yahoo_refresh_token": None,
            "yahoo_token_expires": None,
            "yahoo_guid": None,
        },
    )


def build_oauth(settings: Settings | None = None, persist: bool = True) -> YahooOAuth:
    """A YahooOAuth wired to persist any token it refreshes.

    The refresh callback opens its own short session rather than borrowing the
    request's: a refresh can happen in the middle of an import, and losing the
    new token because the surrounding transaction rolled back would log the
    user out for no visible reason.
    """
    settings = settings or get_settings()

    def _save(tokens: YahooTokens) -> None:
        if not persist:
            return
        from ..db import session_scope

        try:
            with session_scope() as session:
                store_tokens(session, tokens)
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
    """A YahooClient for the configured league. Raises if it cannot be built."""
    settings = settings or get_settings()
    if settings.yahoo_league_id is None:
        raise YahooConnectionError(
            "No Yahoo league configured. Enter your league id on the League screen -- it is "
            "the number in your league URL."
        )
    if not settings.has_yahoo_app:
        raise YahooConnectionError(
            "No Yahoo app configured. Create one at developer.yahoo.com (Fantasy Sports, "
            "Read permission) and paste its Client ID and Client Secret in."
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
    """Convenience for routes that need the effective settings only."""
    return effective_settings(session, get_settings())
