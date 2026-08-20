"""SQLAlchemy engine / session plumbing.

SQLite by default -- the whole app is a single file you can back up or delete.
Swap `FWR_DATABASE_URL` for Postgres later and nothing else has to change.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url

    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        path = settings.sqlite_path()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Anchor the URL to the resolved path. Handing SQLAlchemy the raw
            # relative URL makes the database location depend on the process
            # working directory, so the same install opens a different file
            # depending on how it was launched -- which silently presented a
            # fully configured app as "no league configured yet" when a Windows
            # service started it from C:\Windows\System32.
            url = f"sqlite:///{path.as_posix()}"

    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


#: Demo players are numbered from 100000 up. Real ESPN ids sit far outside
#: that band, so it identifies synthetic rows in a database that predates the
#: `source` column.
DEMO_ID_RANGE = (100000, 100999)

#: Simple columns added to existing tables after their first release. `create_all`
#: creates missing *tables* but never alters existing ones, so a column added
#: to a table that already exists in a deployed database has to be filled in by
#: hand. Each entry is nullable with no default, so the ALTER is portable
#: across SQLite and Postgres and existing rows simply read as the falsy
#: default until they are next written. Columns needing a data backfill (see
#: `_backfill_player_source`) do not belong here.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "user_espn_config": {
        "verified": "BOOLEAN",
        "use_sleeper_projections": "BOOLEAN",
        "projection_mode": "VARCHAR(20)",
        "fantasypros_api_key_encrypted": "VARCHAR(600)",
    },
}


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_added_columns(engine)
    _backfill_player_source(engine)


def _ensure_added_columns(engine: Engine) -> None:
    """Add plain new columns to tables that predate them (no backfill)."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            # create_all just made it with every column present.
            continue
        present = {col["name"] for col in inspector.get_columns(table)}
        for name, ddl_type in columns.items():
            if name in present:
                continue
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def _backfill_player_source(engine: Engine) -> None:
    """Add and populate `players.source` on a database that predates it.

    Unlike the plain columns above, this one needs a data backfill: existing
    rows are marked `espn` except demo players, which are identified by their
    id band. Kept deliberately small: add the column, backfill it, move on.
    """
    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(players)"))
        }
        if not columns or "source" in columns:
            return
        connection.execute(
            text("ALTER TABLE players ADD COLUMN source VARCHAR(20) DEFAULT 'espn'")
        )
        connection.execute(text("UPDATE players SET source = 'espn'"))
        low, high = DEMO_ID_RANGE
        connection.execute(
            text(
                "UPDATE players SET source = 'demo' "
                "WHERE espn_player_id BETWEEN :low AND :high"
            ),
            {"low": low, "high": high},
        )


def reset_engine() -> None:
    """Drop cached engine/session factory (tests point at a temp database)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background/CLI work."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
