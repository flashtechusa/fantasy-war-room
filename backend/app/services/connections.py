"""Connections: which league a request is about, and whose credentials it uses.

A user may have several -- an ESPN league and a Yahoo league is the case this
was built for -- and exactly one is active at a time. Switching is a single
write, because every screen already asks the same two questions ("what are my
settings?" and "what is my league?") and both now answer through the active
connection.

The split between what lives here and what stays in `AppConfig` is worth being
precise about:

* **Here, per user per league**: the ESPN cookies, the Yahoo tokens, which team
  is theirs, their draft slot, their waiver budget. All of it is personal, and
  none of it should ever be visible to another account.
* **`AppConfig`, per installation**: the Yahoo developer app (client id and
  secret) and a FantasyPros key. Those belong to whoever runs the server, are
  shared by every user of it, and are exactly what the platform's terms expect
  an operator to hold.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import Connection, User, utcnow

log = logging.getLogger(__name__)

PLATFORMS = {"espn", "yahoo", "demo"}


class ConnectionError_(RuntimeError):
    """Raised when a connection cannot be created or used."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def list_connections(session: Session, user: User) -> list[Connection]:
    return list(
        session.scalars(
            select(Connection)
            .where(Connection.user_id == user.id)
            .order_by(Connection.created_at, Connection.id)
        ).all()
    )


def active_connection(session: Session, user: User) -> Connection | None:
    """The connection this user is currently looking at.

    Falls back to their first if none is flagged -- a user with connections but
    no active one should see a league, not an empty app.
    """
    rows = list_connections(session, user)
    if not rows:
        return None
    for row in rows:
        if row.is_active:
            return row
    rows[0].is_active = True
    session.flush()
    return rows[0]


def get_connection(session: Session, user: User, connection_id: int) -> Connection:
    """One of this user's connections. Another user's is reported as missing."""
    row = session.get(Connection, int(connection_id))
    if row is None or row.user_id != user.id:
        raise ConnectionError_("No such connection.")
    return row


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def create_connection(
    session: Session,
    user: User,
    *,
    platform: str,
    season: int,
    platform_league_id: int | None = None,
    label: str = "",
    activate: bool = True,
    **credentials,
) -> Connection:
    platform = (platform or "espn").strip().lower()
    if platform not in PLATFORMS:
        raise ConnectionError_(f"Unknown platform {platform!r}.")

    existing = session.scalars(
        select(Connection).where(
            Connection.user_id == user.id,
            Connection.platform == platform,
            Connection.platform_league_id == platform_league_id,
            Connection.season == int(season),
        )
    ).first()
    if existing is not None:
        # Re-adding a league you already have should land you on it rather than
        # failing with a constraint error.
        apply_values(session, existing, {"label": label, **credentials})
        if activate:
            set_active(session, user, existing)
        return existing

    connection = Connection(
        user_id=user.id,
        platform=platform,
        season=int(season),
        platform_league_id=platform_league_id,
        label=label or "",
    )
    session.add(connection)
    session.flush()
    apply_values(session, connection, credentials)
    if activate or len(list_connections(session, user)) == 1:
        set_active(session, user, connection)
    log.info("Added a %s connection for user %s", platform, user.id)
    return connection


#: Fields a caller may write. Anything else is ignored rather than trusted.
WRITABLE = {
    "label",
    "platform",
    "platform_league_id",
    "season",
    "espn_swid",
    "espn_s2",
    "yahoo_access_token",
    "yahoo_refresh_token",
    "yahoo_token_expires",
    "yahoo_guid",
    "my_team_id",
    "my_team_name",
    "my_draft_slot",
    "faab_remaining",
}


def apply_values(session: Session, connection: Connection, values: dict) -> Connection:
    """Write the fields a caller supplied. `None` clears a credential."""
    for key, value in (values or {}).items():
        if key not in WRITABLE:
            continue
        if key == "platform":
            value = (value or "").strip().lower()
            if value not in PLATFORMS:
                continue
        if key in ("platform_league_id", "my_team_id", "my_draft_slot", "faab_remaining"):
            connection_value = int(value) if value not in (None, "") else None
            setattr(connection, key, connection_value)
            continue
        if key == "season":
            connection.season = int(value)
            continue
        if key == "yahoo_token_expires":
            connection.yahoo_token_expires = float(value or 0.0)
            continue
        setattr(connection, key, "" if value is None else str(value))
    connection.updated_at = utcnow()
    session.flush()
    return connection


def set_active(session: Session, user: User, connection: Connection) -> Connection:
    """Make this the connection every screen renders. Exactly one stays active."""
    for row in list_connections(session, user):
        row.is_active = row.id == connection.id
    session.flush()
    return connection


def delete_connection(session: Session, user: User, connection: Connection) -> None:
    """Remove a connection and everything imported under it."""
    was_active = connection.is_active
    session.delete(connection)
    session.flush()
    if was_active:
        remaining = list_connections(session, user)
        if remaining:
            set_active(session, user, remaining[0])


def clear_yahoo_tokens(session: Session, connection: Connection) -> Connection:
    return apply_values(
        session,
        connection,
        {
            "yahoo_access_token": "",
            "yahoo_refresh_token": "",
            "yahoo_token_expires": 0.0,
            "yahoo_guid": "",
        },
    )


# ---------------------------------------------------------------------------
# Bootstrapping
# ---------------------------------------------------------------------------


def ensure_default_connection(
    session: Session, user: User, settings: Settings | None = None
) -> Connection | None:
    """Give a brand-new account the connection its environment already implies.

    A self-hosted install that has `FWR_ESPN_LEAGUE_ID` in `.env` should not
    have to re-enter it in a UI that did not exist when they set it up -- so
    the first run turns that configuration into a connection and carries on.
    Returns None when there is nothing to seed, which is the normal case for a
    hosted signup.
    """
    settings = settings or get_settings()
    if list_connections(session, user):
        return active_connection(session, user)

    if settings.demo_mode:
        return create_connection(
            session, user, platform="demo", season=settings.espn_season, label="Demo league"
        )
    if settings.is_yahoo and settings.yahoo_league_id:
        return create_connection(
            session,
            user,
            platform="yahoo",
            season=settings.espn_season,
            platform_league_id=settings.yahoo_league_id,
            yahoo_access_token=settings.yahoo_access_token or "",
            yahoo_refresh_token=settings.yahoo_refresh_token or "",
            yahoo_token_expires=settings.yahoo_token_expires or 0.0,
            yahoo_guid=settings.yahoo_guid or "",
            my_team_id=settings.my_team_id,
            my_draft_slot=settings.my_draft_slot,
        )
    if settings.espn_league_id:
        return create_connection(
            session,
            user,
            platform="espn",
            season=settings.espn_season,
            platform_league_id=settings.espn_league_id,
            espn_swid=settings.espn_swid or "",
            espn_s2=settings.espn_s2 or "",
            my_team_id=settings.my_team_id,
            my_team_name=settings.my_team_name or "",
            my_draft_slot=settings.my_draft_slot,
        )
    return None


#: How the flat config API's keys map onto connection fields. The config
#: surface predates connections and the UI still speaks it, so the translation
#: lives here rather than being duplicated in the route.
CONFIG_TO_FIELD = {
    "platform": "platform",
    "espn_league_id": "platform_league_id",
    "yahoo_league_id": "platform_league_id",
    "espn_season": "season",
    "espn_swid": "espn_swid",
    "espn_s2": "espn_s2",
    "my_team_id": "my_team_id",
    "my_team_name": "my_team_name",
    "my_draft_slot": "my_draft_slot",
    "faab_remaining": "faab_remaining",
}


def apply_config_values(
    session: Session,
    user: User,
    connection: Connection | None,
    values: dict,
    settings: Settings | None = None,
) -> Connection | None:
    """Route a config-API payload onto the active connection.

    Creates one when the payload describes a league and the account has none,
    which is what makes the existing "enter your league id" form still work on
    a fresh install.
    """
    settings = settings or get_settings()
    translated = {
        CONFIG_TO_FIELD[key]: value
        for key, value in (values or {}).items()
        if key in CONFIG_TO_FIELD
    }
    if not translated:
        return connection

    # A league id is only meaningful alongside the platform it belongs to.
    platform = translated.get("platform") or (connection.platform if connection else None)
    if "yahoo_league_id" in values and platform != "yahoo":
        platform = "yahoo"
        translated["platform"] = "yahoo"
    if "espn_league_id" in values and platform not in ("espn", None):
        # Someone setting an ESPN league id means to be on ESPN.
        platform = "espn"
        translated["platform"] = "espn"

    if connection is None:
        connection = create_connection(
            session,
            user,
            platform=platform or "espn",
            season=int(translated.get("season") or settings.espn_season),
            platform_league_id=translated.get("platform_league_id"),
        )
        translated.pop("platform_league_id", None)
        translated.pop("season", None)

    return apply_values(session, connection, translated)


# ---------------------------------------------------------------------------
# Settings overlay
# ---------------------------------------------------------------------------


def settings_for(connection: Connection | None, base: Settings) -> Settings:
    """`base` with this connection's league and credentials layered on top.

    This is the whole trick: every service already takes a `Settings`, so once
    it describes the active connection instead of the process environment, the
    app is multi-tenant without those services knowing anything happened.
    """
    if connection is None:
        return base

    values = base.model_dump()
    values["active_connection_id"] = connection.id
    values["espn_season"] = connection.season

    if connection.platform == "demo":
        values["demo_mode"] = True
    else:
        values["platform"] = connection.platform

    # Credentials are overlaid whatever the platform: a connection holds
    # whatever its owner has entered, and reporting an ESPN cookie as unset
    # because the connection is currently on demo is a lie the League screen
    # then shows back to them.
    values["espn_swid"] = connection.espn_swid or None
    values["espn_s2"] = connection.espn_s2 or None
    values["yahoo_access_token"] = connection.yahoo_access_token or None
    values["yahoo_refresh_token"] = connection.yahoo_refresh_token or None
    values["yahoo_token_expires"] = connection.yahoo_token_expires or 0.0
    values["yahoo_guid"] = connection.yahoo_guid or None

    # The league id is per-platform, so only one of these may be set at a time.
    # A Yahoo connection inheriting an ESPN league id from the environment
    # would make `get_active_league` filter on an id from the wrong platform.
    if connection.platform == "yahoo":
        values["yahoo_league_id"] = connection.platform_league_id
        values["espn_league_id"] = None
    elif connection.platform == "espn":
        values["espn_league_id"] = connection.platform_league_id
        values["yahoo_league_id"] = None

    values["my_team_id"] = connection.my_team_id
    values["my_team_name"] = connection.my_team_name or None
    values["my_draft_slot"] = connection.my_draft_slot
    values["faab_remaining"] = connection.faab_remaining
    return Settings.model_validate(values)


def describe(connection: Connection | None) -> dict | None:
    """Safe-to-display connection state. Credentials are reported, never returned."""
    if connection is None:
        return None
    return {
        "id": connection.id,
        "platform": connection.platform,
        "label": connection.describe(),
        "league_id": connection.platform_league_id,
        "season": connection.season,
        "is_active": connection.is_active,
        "my_team_id": connection.my_team_id,
        "my_draft_slot": connection.my_draft_slot,
        "faab_remaining": connection.faab_remaining,
        "espn_cookies_set": bool(connection.espn_swid and connection.espn_s2),
        "yahoo_connected": connection.has_yahoo_tokens,
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
    }
