"""Bringing an existing database up to the current schema.

There is no migration framework here, and adding one to a project whose whole
database is a single SQLite file you can delete would be a poor trade. What
there is instead: a small, idempotent, forward-only step that runs at startup
and knows how to get from the single-tenant schema to the one with accounts.

The interesting part is that `leagues` and `players` gained a
`connection_id` *and* changed their unique constraints. SQLite cannot drop a
constraint in place, so those two tables are rebuilt: rename aside, let
`create_all` build the new shape, copy the rows back by column name, drop the
old. Row ids are preserved, so every foreign key pointing at them stays valid.

`PRAGMA legacy_alter_table=ON` is what makes the rename safe: without it,
SQLite helpfully rewrites other tables' foreign keys to follow the renamed
table, which is the opposite of what a rebuild wants.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

#: Tables whose unique constraints changed when connections arrived.
_REBUILT = ("leagues", "players")

#: Config keys that used to be installation-wide and are now per connection.
_MOVED_TO_CONNECTION = {
    "espn_league_id": "platform_league_id",
    "yahoo_league_id": "platform_league_id",
    "espn_season": "season",
    "espn_swid": "espn_swid",
    "espn_s2": "espn_s2",
    "yahoo_access_token": "yahoo_access_token",
    "yahoo_refresh_token": "yahoo_refresh_token",
    "yahoo_token_expires": "yahoo_token_expires",
    "yahoo_guid": "yahoo_guid",
    "my_team_id": "my_team_id",
    "my_draft_slot": "my_draft_slot",
    "faab_remaining": "faab_remaining",
}


def needs_connection_migration(engine: Engine) -> bool:
    """True when this database predates accounts and holds data worth keeping."""
    inspector = inspect(engine)
    if "leagues" not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns("leagues")}
    return "connection_id" not in columns


def migrate(engine: Engine, create_all) -> None:
    """Run any pending migration. Safe to call on every start.

    `create_all` is passed in rather than imported so this module stays a leaf:
    it is called from `db.init_db`, in between the two halves of setup.
    """
    if not needs_connection_migration(engine):
        create_all()
        _ensure_local_account(engine)
        return

    if engine.dialect.name != "sqlite":
        create_all()
        log.warning(
            "This database predates accounts and is not SQLite, so the tables were not "
            "rebuilt. Add connection_id to leagues and players and replace their unique "
            "constraints by hand before running multi-user."
        )
        return

    log.info("Migrating the database to the connection-scoped schema.")
    legacy_columns = _capture_columns(engine)

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # Without this, SQLite rewrites every other table's foreign keys to
        # follow the rename, and the copied-back tables end up orphaned.
        conn.exec_driver_sql("PRAGMA legacy_alter_table=ON")
        for table in _REBUILT:
            conn.exec_driver_sql(f"ALTER TABLE {table} RENAME TO {table}_legacy")
        conn.commit()

    create_all()

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in _REBUILT:
            columns, expressions = _copy_plan(conn, table, legacy_columns[table])
            if not columns:
                continue
            conn.exec_driver_sql(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"SELECT {', '.join(expressions)} FROM {table}_legacy"
            )
        for table in _REBUILT:
            conn.exec_driver_sql(f"DROP TABLE {table}_legacy")
        conn.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
        conn.commit()

    connection_id = _ensure_local_account(engine)
    if connection_id is not None:
        with engine.connect() as conn:
            for table in _REBUILT:
                conn.exec_driver_sql(
                    f"UPDATE {table} SET connection_id = {int(connection_id)} "
                    "WHERE connection_id IS NULL"
                )
            conn.commit()
    log.info("Migration complete: existing leagues now belong to the local account.")


def _capture_columns(engine: Engine) -> dict[str, list[str]]:
    inspector = inspect(engine)
    return {table: [c["name"] for c in inspector.get_columns(table)] for table in _REBUILT}


def _copy_plan(conn, table: str, legacy: list[str]) -> tuple[list[str], list[str]]:
    """(columns, select expressions) for copying the old table into the new one.

    Two kinds of gap have to be filled, and both are real rather than
    hypothetical: a column the old schema never had (the database predates a
    release that added one), and a column it had but left null where the new
    schema requires a value. Either one fails the copy outright, taking the
    user's whole database with it, so every required column without a default
    gets a literal of the right shape.
    """
    columns: list[str] = []
    expressions: list[str] = []
    legacy_set = set(legacy)

    for _, name, column_type, not_null, default, primary_key in conn.exec_driver_sql(
        f"PRAGMA table_info({table})"
    ).fetchall():
        fills_needed = bool(not_null) and default is None and not primary_key
        if name in legacy_set:
            columns.append(name)
            expressions.append(
                f"COALESCE({name}, {_literal(column_type)})" if fills_needed else name
            )
        elif fills_needed:
            columns.append(name)
            expressions.append(_literal(column_type))
    return (columns, expressions)


def _literal(column_type: str) -> str:
    """A stand-in value of the right type for a required column with no data.

    JSON columns get the string `null`, which decodes to None -- every reader
    already writes `or {}` around these, and guessing `{}` for a list column
    would be worse than a null.
    """
    kind = (column_type or "").upper()
    if "JSON" in kind:
        return "'null'"
    if "CHAR" in kind or "TEXT" in kind:
        return "''"
    if "FLOAT" in kind or "REAL" in kind or "NUMERIC" in kind:
        return "0.0"
    if "DATE" in kind or "TIME" in kind:
        return "CURRENT_TIMESTAMP"
    return "0"


def _ensure_local_account(engine: Engine) -> int | None:
    """Create the implicit local account and move its settings onto a connection.

    Returns the connection id when there was one to make. A database with no
    stored configuration gets nothing: a fresh install should start on the "add
    a league" screen, not on a connection pointing at nowhere.
    """
    from .config import get_settings
    from .services import accounts, connections

    settings = get_settings()
    from .db import session_scope

    with session_scope() as session:
        user = accounts.get_or_create_local_user(session)
        existing = connections.list_connections(session, user)
        if existing:
            return existing[0].id

        stored = _read_app_config(session)
        platform = stored.pop("platform", None) or settings.platform
        values = {
            field: stored[key]
            for key, field in _MOVED_TO_CONNECTION.items()
            if key in stored
        }

        league_id = values.pop("platform_league_id", None)
        season = values.pop("season", None) or settings.espn_season
        demo = str(stored.get("demo_mode", "")).lower() in {"1", "true", "yes"}

        if demo or (league_id is None and not settings.espn_league_id):
            if not (demo or settings.demo_mode):
                return None
            connection = connections.create_connection(
                session, user, platform="demo", season=int(season), label="Demo league"
            )
            _forget_moved_keys(session)
            return connection.id

        connection = connections.create_connection(
            session,
            user,
            platform=platform if platform in connections.PLATFORMS else "espn",
            season=int(season),
            platform_league_id=int(league_id) if league_id else settings.espn_league_id,
            **{k: v for k, v in values.items()},
        )
        _forget_moved_keys(session)
        return connection.id


def _read_app_config(session) -> dict:
    from sqlalchemy import select

    from .models import AppConfig

    return {row.key: row.value for row in session.scalars(select(AppConfig)).all()}


def _forget_moved_keys(session) -> None:
    """Drop per-league settings from the shared table once they have a home.

    Leaving them behind would be a second source of truth for credentials, and
    on a multi-user install a shared one.
    """
    from sqlalchemy import select

    from .models import AppConfig

    for row in session.scalars(select(AppConfig)).all():
        if row.key in _MOVED_TO_CONNECTION or row.key == "platform":
            session.delete(row)
    session.flush()
