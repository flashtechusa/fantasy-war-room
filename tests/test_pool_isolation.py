"""Real and synthetic players must never share a league's rankings.

The player pool is keyed by (season, espn_player_id) and NOT by league, which
is deliberate -- two real leagues in one season share raw stats and each scores
them under its own rules. Demo players landed in that same table, so a single
demo import put 330 fabricated players into a live league: they took the top of
every position, moved replacement level, and were offered as free agents who do
not exist. Every team's construction score fell at once as a result, days apart
from anyone touching a roster.
"""

from __future__ import annotations

import pytest


def add_foreign_players(league_source: str, season: int, count: int, position: str,
                        points: float, first_id: int = 900000) -> None:
    """Players from a provider this league does not use."""
    from app.db import session_scope
    from app.models import Player, PlayerProjection

    foreign = "demo" if league_source != "demo" else "espn"
    with session_scope() as session:
        for index in range(count):
            player = Player(
                season=season,
                espn_player_id=first_id + index,
                name=f"Fabricated {position} {index}",
                position=position,
                source=foreign,
                availability="FREEAGENT",
            )
            session.add(player)
            session.flush()
            session.add(
                PlayerProjection(
                    player_id=player.id,
                    source_key="espn",
                    # Passing yards and touchdowns: enough to top any real player.
                    raw_stats={"3": points * 10, "4": points},
                )
            )
        session.commit()


@pytest.fixture
def league_and_engine(drafted_league):
    from app.db import session_scope
    from app.models import League
    from app.services import board as board_service

    board_service.clear_cache()
    with session_scope() as session:
        league = session.query(League).one()
        return league.id, league.season, league.source


class TestDemoPlayersStayOutOfRealLeagues:
    def test_the_engine_only_ranks_this_league_s_source(self, league_and_engine):
        from app.db import session_scope
        from app.models import League
        from app.services import board as board_service

        _, season, source = league_and_engine
        with session_scope() as session:
            league = session.query(League).one()
            before = len(board_service.build_engine(session, league).players)

        add_foreign_players(source, season, 5, "QB", 900, first_id=900000)
        board_service.clear_cache()

        with session_scope() as session:
            league = session.query(League).one()
            engine = board_service.build_engine(session, league)
        assert len(engine.players) == before
        assert not [p for p in engine.players if p.name.startswith("Fabricated")]

    def test_a_foreign_import_cannot_move_replacement_level(self, league_and_engine):
        from app.db import session_scope
        from app.models import League
        from app.services import board as board_service

        _, season, source = league_and_engine
        with session_scope() as session:
            league = session.query(League).one()
            before = board_service.build_engine(session, league).replacement.as_dict()

        add_foreign_players(source, season, 40, "RB", 800, first_id=900100)
        board_service.clear_cache()

        with session_scope() as session:
            league = session.query(League).one()
            after = board_service.build_engine(session, league).replacement.as_dict()
        assert after == before, "foreign players moved a real league's VOR"

    def test_a_foreign_player_is_never_offered_as_a_free_agent(self, league_and_engine):
        from app.db import session_scope
        from app.models import League
        from app.services import board as board_service
        from app.services import season as season_service

        _, season, source = league_and_engine
        add_foreign_players(source, season, 3, "WR", 900, first_id=900500)
        board_service.clear_cache()

        with session_scope() as session:
            league = session.query(League).one()
            engine = board_service.build_engine(session, league)
            wire = season_service.build_weekly_players(
                session, league, engine, week=1,
                availability={"FREEAGENT", "WAIVERS"},
            )
        assert not [p for p in wire if "Fabricated" in p.name]

    def test_the_bar_and_the_scores_do_not_move(self, drafted_league, league_and_engine):
        """The reported symptom: every team's score fell at once."""
        client = drafted_league
        _, season, source = league_and_engine

        before = client.get("/api/team/league").json()
        assert before["teams"], "no teams to compare"

        add_foreign_players(source, season, 30, "WR", 950, first_id=900700)
        from app.services import board as board_service

        board_service.clear_cache()

        after = client.get("/api/team/league").json()
        assert [t["roster_construction_score"] for t in after["teams"]] == [
            t["roster_construction_score"] for t in before["teams"]
        ]


class TestTheSourceColumnIsBackfilled:
    def test_an_existing_database_gains_the_column(self, tmp_path, monkeypatch):
        """`create_all` never alters an existing table, so this is the upgrade."""
        from sqlalchemy import create_engine, text

        path = tmp_path / "old.db"
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE players (id INTEGER PRIMARY KEY, season INTEGER,"
                    " espn_player_id INTEGER, name VARCHAR, position VARCHAR)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO players (season, espn_player_id, name, position)"
                    " VALUES (2026, 4038941, 'Justin Herbert', 'QB'),"
                    "        (2026, 100004, 'Jalen Fontaine', 'QB')"
                )
            )
        engine.dispose()

        monkeypatch.setenv("FWR_DATABASE_URL", f"sqlite:///{path}")
        from app import db as db_module

        db_module.reset_engine()
        db_module.init_db()

        with db_module.get_engine().begin() as connection:
            rows = dict(
                connection.execute(text("SELECT name, source FROM players")).all()
            )
        db_module.reset_engine()

        assert rows["Justin Herbert"] == "espn"
        assert rows["Jalen Fontaine"] == "demo", "demo id band was not recognised"


class TestAnAccountWithNoLeagueCannotImport:
    """How the fabricated players got in: a client account clicked Import.

    A signed-in user with no ESPN connection resolved to settings with no
    league id, and "no league id" selected the demo provider. Importing then
    wrote 330 synthetic players into a pool keyed by season -- shared with the
    owner's real league in that same season.
    """

    def test_no_connection_means_no_provider(self):
        import pytest

        from app.config import Settings
        from app.espn.client import EspnNotConfigured
        from app.services.provider import build_provider

        with pytest.raises(EspnNotConfigured):
            build_provider(Settings(demo_mode=False, _env_file=None))

    def test_explicit_demo_mode_still_works(self):
        from app.config import Settings
        from app.services.provider import DemoProvider, build_provider

        assert isinstance(
            build_provider(Settings(demo_mode=True, _env_file=None)), DemoProvider
        )

    def test_a_client_account_importing_is_told_to_connect_a_league(self, anon_client):
        """End to end, the way it actually happened.

        Ari signed in, pressed Import to see how it worked, and the app made
        her a league out of thin air.
        """
        from app.db import session_scope
        from app.models import Player
        from app.services import auth as auth_service

        with session_scope() as session:
            auth_service.ensure_owner(session, "owner", "owner-password-1")
            session.commit()

        anon_client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "owner-password-1"},
        )
        created = anon_client.post(
            "/api/admin/users", json={"username": "ari", "role": "client"}
        ).json()

        anon_client.post("/api/auth/logout")
        signed_in = anon_client.post(
            "/api/auth/login",
            json={"username": "ari", "password": created["password"]},
        )
        assert signed_in.status_code == 200, signed_in.text

        response = anon_client.post("/api/league/import")
        assert response.status_code == 409, response.text
        assert "connect" in response.json()["detail"].lower()

        with session_scope() as session:
            assert session.query(Player).count() == 0, (
                "a client with no ESPN connection created players"
            )
