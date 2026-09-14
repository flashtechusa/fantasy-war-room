"""Upgrading a single-tenant database in place.

Someone running this before accounts existed has a database full of their
league, their draft and their projections. The upgrade has to keep all of it,
attach it to the local account, and move their credentials out of the shared
config table -- without a migration framework and without SQLite being able to
drop a constraint.

These tests build a database in the *old* shape and run the real startup path
over it.
"""

from __future__ import annotations

import sqlite3

import pytest

LEGACY_LEAGUES = """
CREATE TABLE leagues (
    id INTEGER NOT NULL PRIMARY KEY,
    espn_league_id INTEGER,
    season INTEGER,
    name VARCHAR(200),
    team_count INTEGER,
    scoring_type VARCHAR(50),
    is_ppr BOOLEAN,
    ppr_value FLOAT,
    roster_slots JSON,
    bench_slots INTEGER,
    ir_slots INTEGER,
    draft_type VARCHAR(50),
    draft_order JSON,
    draft_completed BOOLEAN,
    keeper_count INTEGER,
    waiver_type VARCHAR(50),
    uses_faab BOOLEAN,
    acquisition_budget INTEGER,
    waiver_process_days JSON,
    regular_season_weeks INTEGER,
    playoff_team_count INTEGER,
    playoff_matchup_length INTEGER,
    playoff_seed_tie_rule VARCHAR(60),
    trade_deadline INTEGER,
    veto_votes_required INTEGER,
    source VARCHAR(20),
    imported_at DATETIME,
    raw_settings JSON,
    CONSTRAINT uq_league_season UNIQUE (espn_league_id, season)
)
"""

LEGACY_PLAYERS = """
CREATE TABLE players (
    id INTEGER NOT NULL PRIMARY KEY,
    season INTEGER,
    espn_player_id INTEGER,
    name VARCHAR(200),
    position VARCHAR(10),
    pro_team VARCHAR(10),
    eligible_slots JSON,
    bye_week INTEGER,
    projected_games FLOAT,
    injury_status VARCHAR(40),
    injured BOOLEAN,
    percent_owned FLOAT,
    percent_started FLOAT,
    adp FLOAT,
    position_adp FLOAT,
    espn_rank INTEGER,
    espn_position_rank INTEGER,
    espn_projected_points FLOAT,
    rookie BOOLEAN,
    availability VARCHAR(20),
    on_team_id INTEGER,
    updated_at DATETIME,
    CONSTRAINT uq_player_season UNIQUE (season, espn_player_id)
)
"""

LEGACY_TEAMS = """
CREATE TABLE league_teams (
    id INTEGER NOT NULL PRIMARY KEY,
    league_id INTEGER REFERENCES leagues (id) ON DELETE CASCADE,
    espn_team_id INTEGER,
    name VARCHAR(200),
    abbrev VARCHAR(20),
    owners JSON,
    logo_url VARCHAR(500),
    division_name VARCHAR(100),
    draft_slot INTEGER,
    is_mine BOOLEAN,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    roster JSON,
    CONSTRAINT uq_team_per_league UNIQUE (league_id, espn_team_id)
)
"""

LEGACY_CONFIG = """
CREATE TABLE app_config (
    key VARCHAR(60) NOT NULL PRIMARY KEY,
    value VARCHAR(2000),
    updated_at DATETIME
)
"""


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database in the pre-accounts shape, with a league already imported."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    for statement in (LEGACY_LEAGUES, LEGACY_PLAYERS, LEGACY_TEAMS, LEGACY_CONFIG):
        conn.execute(statement)
    conn.execute(
        "INSERT INTO leagues (id, espn_league_id, season, name, team_count, source, "
        "imported_at) VALUES (7, 123456, 2026, 'Old League', 10, 'espn', '2026-08-01')"
    )
    conn.execute(
        "INSERT INTO league_teams (id, league_id, espn_team_id, name, is_mine) "
        "VALUES (1, 7, 3, 'My Team', 1)"
    )
    conn.execute(
        "INSERT INTO players (id, season, espn_player_id, name, position, adp) "
        "VALUES (11, 2026, 4242, 'Legacy Back', 'RB', 6.5)"
    )
    for key, value in [
        ("espn_league_id", "123456"),
        ("espn_swid", "{OLD-SWID}"),
        ("espn_s2", "old-s2-cookie"),
        ("my_team_id", "3"),
        ("fantasypros_api_key", "keep-me"),
    ]:
        conn.execute(
            "INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, '2026-08-01')",
            (key, value),
        )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FWR_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FWR_DEMO_MODE", "false")

    from app import config, db

    config.reset_settings_cache()
    db.reset_engine()
    yield path
    config.reset_settings_cache()
    db.reset_engine()


class TestConnectionMigration:
    def test_it_runs_and_keeps_the_league(self, legacy_db):
        from app.db import init_db, session_scope
        from app.models import League

        init_db()

        with session_scope() as session:
            league = session.query(League).one()
            assert league.id == 7
            assert league.name == "Old League"
            assert league.espn_league_id == 123456

    def test_rows_are_attached_to_the_local_account(self, legacy_db):
        from app.db import init_db, session_scope
        from app.models import Connection, League, Player, User

        init_db()

        with session_scope() as session:
            user = session.query(User).one()
            assert user.is_local is True

            connection = session.query(Connection).one()
            assert connection.user_id == user.id
            assert connection.platform == "espn"
            assert connection.platform_league_id == 123456
            assert connection.is_active is True

            assert session.query(League).one().connection_id == connection.id
            assert session.query(Player).one().connection_id == connection.id

    def test_credentials_move_out_of_the_shared_table(self, legacy_db):
        """Per-league secrets must not stay in a table every account can read."""
        from app.db import init_db, session_scope
        from app.models import AppConfig, Connection

        init_db()

        with session_scope() as session:
            connection = session.query(Connection).one()
            assert connection.espn_swid == "{OLD-SWID}"
            assert connection.espn_s2 == "old-s2-cookie"
            assert connection.my_team_id == 3

            remaining = {row.key for row in session.query(AppConfig).all()}
            assert "espn_swid" not in remaining
            assert "espn_s2" not in remaining
            # Installation-wide settings stay exactly where they were.
            assert "fantasypros_api_key" in remaining

    def test_foreign_keys_survive_the_rebuild(self, legacy_db):
        """The rebuild renames tables; rows pointing at them must still resolve."""
        from app.db import init_db, session_scope
        from app.models import League

        init_db()

        with session_scope() as session:
            league = session.query(League).one()
            assert [team.name for team in league.teams] == ["My Team"]
            assert league.teams[0].is_mine is True

    def test_the_new_constraints_are_in_place(self, legacy_db):
        """Two accounts must be able to import the same league id."""
        from sqlalchemy import inspect

        from app.db import get_engine, init_db

        init_db()

        constraints = inspect(get_engine()).get_unique_constraints("leagues")
        assert any(
            set(entry["column_names"]) == {"connection_id", "espn_league_id", "season"}
            for entry in constraints
        ), constraints

    def test_running_it_twice_changes_nothing(self, legacy_db):
        from app.db import init_db, session_scope
        from app.models import Connection, League, Player

        init_db()
        init_db()

        with session_scope() as session:
            assert session.query(League).count() == 1
            assert session.query(Player).count() == 1
            assert session.query(Connection).count() == 1


class TestFreshDatabase:
    def test_a_new_database_needs_no_migration(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FWR_DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
        from app import config, db

        config.reset_settings_cache()
        db.reset_engine()

        from app.db import get_engine, init_db
        from app.migrations import needs_connection_migration

        init_db()
        assert needs_connection_migration(get_engine()) is False
