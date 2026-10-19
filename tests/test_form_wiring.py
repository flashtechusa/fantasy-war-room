"""The production record has to reach the rating, not just exist in a table.

The blend itself is unit-tested in test_form_rating. This covers the wiring it
depends on end to end: a row in player_actuals has to change what the engine
says a player is worth, and the screen has to say what it is reasoning from.
Without that chain the import is just a table nobody reads -- which is the state
the app was already in.
"""

from __future__ import annotations

import pytest

TARGETS = "58"


def _give_actuals(espn_player_id: int, *, points: float, recent: float, games: float = 6.0,
                  targets: float = 36.0, recent_targets: float = 18.0) -> None:
    """Record a season's production against a player already in the pool."""
    from app.db import session_scope
    from app.models import Player, PlayerActuals
    from app.services.board import clear_cache

    with session_scope() as session:
        player = session.query(Player).filter(
            Player.espn_player_id == espn_player_id
        ).one()
        row = session.query(PlayerActuals).filter(
            PlayerActuals.player_id == player.id
        ).first()
        if row is None:
            row = PlayerActuals(player_id=player.id)
            session.add(row)
        row.games_played = games
        row.source_points = points
        row.raw_stats = {TARGETS: targets}
        row.recent_games = 3.0
        row.recent_source_points = recent
        row.recent_raw_stats = {TARGETS: recent_targets}
        row.last_week = 6
    clear_cache()      # the engine caches a built board; this is new input


@pytest.fixture
def a_player(drafted_league):
    """Someone in the pool, with the rating they had before any games."""
    from app.db import session_scope
    from app.models import League, Player
    from app.services.board import build_engine

    with session_scope() as session:
        league = session.query(League).one()
        player = session.query(Player).filter(Player.position == "WR").first()
        engine = build_engine(session, league)
        return (player.espn_player_id, player.name, engine.points_for(player.espn_player_id))


def test_production_moves_the_rating(a_player):
    from app.db import session_scope
    from app.models import League
    from app.services.board import build_engine

    player_id, _, before = a_player
    assert before > 0, "the fixture player should have a preseason rating"

    # Six games at a far higher rate than anyone projected.
    _give_actuals(player_id, points=140.0, recent=75.0)

    with session_scope() as session:
        league = session.query(League).one()
        after = build_engine(session, league).points_for(player_id)

    assert after > before, (
        f"{before} -> {after}: six weeks of production has to reach the rating"
    )


def test_a_bust_falls(a_player):
    from app.db import session_scope
    from app.models import League
    from app.services.board import build_engine

    player_id, _, before = a_player
    # The other direction matters just as much: August said he was good and he
    # has not been. A rating that only ever rises is a ratchet, not a rating.
    _give_actuals(player_id, points=6.0, recent=1.5, targets=12.0, recent_targets=3.0)

    with session_scope() as session:
        league = session.query(League).one()
        after = build_engine(session, league).points_for(player_id)

    assert after < before, f"{before} -> {after}: a season of nothing has to count too"


def test_a_player_with_no_record_is_rated_exactly_as_before(a_player):
    """The property that makes this safe to deploy in week 7."""
    from app.db import session_scope
    from app.models import League
    from app.services.board import build_engine, clear_cache

    player_id, _, before = a_player
    clear_cache()

    with session_scope() as session:
        league = session.query(League).one()
        after = build_engine(session, league).points_for(player_id)

    assert after == before, "nobody with an empty record may move at all"


def test_the_waiver_card_says_what_it_is_reasoning_from(drafted_league):
    """The number changed; the screen has to be able to explain why."""
    from app.engine.form import SeasonForm
    from app.engine.waivers import _form_reason      # noqa: SLF001
    from app.engine.weekly import WeeklyPlayer

    player = WeeklyPlayer(
        espn_player_id=1, name="Khalil Shakir", position="WR", pro_team="BUF",
        week_points=12.0, season_points=100.0, week=7,
        form=SeasonForm(
            games_played=6, points=73.0, recent_games=3, recent_points=50.2,
            opportunities=32, recent_opportunities=17,
        ),
    )
    line = _form_reason(player)
    assert line is not None
    assert "12.2" in line and "6 games" in line
    assert "16.7" in line and "last 3" in line

    # And nothing is claimed about a player who has not played.
    assert _form_reason(
        WeeklyPlayer(espn_player_id=2, name="Rookie", position="WR", week=1)
    ) is None
