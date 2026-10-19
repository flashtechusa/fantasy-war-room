"""A table that already exists never gains a column on its own.

create_all CREATES tables; it does not alter one that is already there. So a
column added to a shipped table reaches a fresh database (and every test run)
and never reaches an install that took the earlier version -- where the new code
then queries a column that is not there.

That happened: player_actuals shipped one day and gained recent_weeks the next,
and the live install started answering 502. A suite that builds its database
from scratch every run cannot catch it, which is why _ADDED_COLUMNS exists and
why it has to be kept honest by comparing it against the models.
"""

from __future__ import annotations

import sqlalchemy as sa


def test_recent_weeks_is_registered_for_migration():
    from app.db import _ADDED_COLUMNS      # noqa: SLF001

    assert "recent_weeks" in _ADDED_COLUMNS.get("player_actuals", {})


def test_an_older_table_gains_the_column_on_startup(tmp_path):
    """Simulate the install that broke: the table, minus the newer column."""
    from app.db import _ensure_added_columns      # noqa: SLF001

    engine = sa.create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as conn:
        # player_actuals as it shipped the first day.
        conn.execute(sa.text(
            "CREATE TABLE player_actuals ("
            " id INTEGER PRIMARY KEY, player_id INTEGER, games_played FLOAT,"
            " raw_stats JSON, source_points FLOAT, recent_games FLOAT,"
            " recent_raw_stats JSON, recent_source_points FLOAT,"
            " last_week INTEGER, updated_at DATETIME)"
        ))

    before = {c["name"] for c in sa.inspect(engine).get_columns("player_actuals")}
    assert "recent_weeks" not in before

    _ensure_added_columns(engine)

    after = {c["name"] for c in sa.inspect(engine).get_columns("player_actuals")}
    assert "recent_weeks" in after, "the live install must heal itself on startup"


def test_every_registered_column_exists_on_the_model():
    """Keeps the migration list honest: a typo here is a column nobody gets."""
    from app.db import _ADDED_COLUMNS      # noqa: SLF001
    from app.models import Base

    tables = Base.metadata.tables
    for table, columns in _ADDED_COLUMNS.items():
        assert table in tables, f"{table} is not a table on the models"
        real = set(tables[table].columns.keys())
        missing = set(columns) - real
        assert not missing, f"{table}: {missing} are not columns on the model"


def test_a_broken_actuals_table_does_not_take_the_app_down(drafted_league):
    """Degrade to projections rather than 500 on every screen."""
    from app.db import session_scope
    from app.services.board import clear_cache

    with session_scope() as session:
        session.execute(sa.text("DROP TABLE IF EXISTS player_actuals"))
    clear_cache()

    for path in ("/api/season/lineup", "/api/season/waivers", "/api/players"):
        response = drafted_league.get(path)
        assert response.status_code == 200, f"{path}: {response.status_code}"
