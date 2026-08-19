"""SQLAlchemy engine / session plumbing.

SQLite by default -- the whole app is a single file you can back up or delete.
Swap `FWR_DATABASE_URL` for Postgres later and nothing else has to change.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
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


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Bring an existing database up to the current schema.

    `create_all` creates missing tables but never alters existing ones, so a
    new column is invisible to every install that already has data -- which is
    all of them. Kept deliberately small: add the column, backfill it, move on.
    """
    from sqlalchemy import text

    engine = get_engine()
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
