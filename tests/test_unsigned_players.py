"""A player on no NFL roster is not a waiver target.

Reported from real use: Charlie Smyth, a kicker on no NFL team, was the number
one recommendation on the wire -- priority 100, a suggested $6 FAAB bid, and a
real running back named as the drop to make room. ESPN still publishes a
projection for a released player, and nothing checked whether anybody employs
him, so a leftover number made him look like the biggest upgrade available.

He takes no snaps. He cannot be an upgrade at any price.
"""

from __future__ import annotations

from app.engine.league_shape import LeagueShape
from app.engine.waivers import explain_by_position, is_signed, recommend_waivers
from app.engine.weekly import WeeklyPlayer


def _player(pid, name, position, points, pro_team="KC"):
    return WeeklyPlayer(
        espn_player_id=pid, name=name, position=position,
        week_points=points, season_points=points * 16, pro_team=pro_team, week=1,
    )


def _shape():
    return LeagueShape.from_slots(
        team_count=10, roster_slots={"QB": 1, "K": 1}, bench_slots=1,
    )


def test_no_pro_team_means_not_signed():
    assert is_signed(_player(1, "Employed", "K", 9.0, pro_team="KC")) is True
    for nobody in ("FA", "fa", "", "  ", "None", "-"):
        assert is_signed(_player(1, "Released", "K", 9.0, pro_team=nobody)) is False


def test_an_unsigned_upgrade_is_never_recommended():
    roster = [_player(1, "My QB", "QB", 20.0), _player(2, "Weak Kicker", "K", 1.0)]
    # On paper the biggest upgrade on the board -- and on no NFL roster.
    wire = [_player(3, "Charlie Smyth", "K", 9.0, pro_team="FA")]

    targets = recommend_waivers(
        roster=roster, free_agents=wire, shape=_shape(), week=1,
        faab_budget=100, roster_is_full=True,
    )
    assert targets == [], "an unsigned player cannot be an upgrade at any price"


def test_the_same_player_with_a_team_is_recommended():
    # The control: identical numbers, employed. The filter must be about the
    # NFL team and nothing else.
    roster = [_player(1, "My QB", "QB", 20.0), _player(2, "Weak Kicker", "K", 1.0)]
    wire = [_player(3, "Charlie Smyth", "K", 9.0, pro_team="NO")]

    targets = recommend_waivers(
        roster=roster, free_agents=wire, shape=_shape(), week=1,
        faab_budget=100, roster_is_full=True,
    )
    assert [t.player.name for t in targets] == ["Charlie Smyth"]


def test_the_best_available_is_never_an_unsigned_player():
    roster = [_player(1, "My QB", "QB", 20.0), _player(2, "Weak Kicker", "K", 1.0)]
    wire = [
        _player(3, "Unsigned Stud", "K", 15.0, pro_team="FA"),
        _player(4, "Real Kicker", "K", 8.0, pro_team="BUF"),
    ]
    verdicts = explain_by_position(roster, wire, _shape(), recommended=set())
    named = [v.best_name for v in verdicts if v.position == "K"]
    assert named and named[0] == "Real Kicker", (
        '"the best out there" has to mean someone who can actually play'
    )


class TestInventedWeeklyPoints:
    """Where the unsigned kicker's points actually came from.

    He had no week-6 projection -- ESPN does not project a man with no team --
    so the fallback handed him his preseason season total divided by the weeks:
    about 9.7 points a week, enough to beat a top-four kicker and top the wire.

    ESPN projects whoever is expected to play. Being left out of a week it HAS
    published is the answer, not a gap to fill with an August estimate. A week
    it has not published yet is different: there the season average is the only
    estimate available, and the screen says so.
    """

    @staticmethod
    def _league():
        from app.models import League

        return League(
            id=1, espn_league_id=1, season=2026, source="espn",
            regular_season_weeks=14, roster_slots={"K": 1}, bench_slots=2,
        )

    class _Engine:
        """Minimal stand-in: everyone carries a big preseason projection."""

        shape = None
        allow_fantasypros = False
        scoring = None

        class _Replacement:
            @staticmethod
            def vor(position, points):
                return points

        replacement = _Replacement()

        @staticmethod
        def points_for(player_id):
            return 136.0      # a full season of kicking, projected in August

    def _build(self, monkeypatch, published: dict[int, float], week: int = 6):
        from app.models import Player
        from app.services import season as season_service

        players = [
            Player(
                id=1, espn_player_id=11, season=2026, source="espn",
                name="Released Kicker", position="K", pro_team="FA",
            ),
            Player(
                id=2, espn_player_id=22, season=2026, source="espn",
                name="Employed Kicker", position="K", pro_team="HOU",
            ),
        ]

        class _Scalars:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Session:
            def scalars(self, *a, **k):
                return _Scalars(players)

        monkeypatch.setattr(
            season_service, "weekly_points_by_player", lambda *a, **k: published
        )
        return season_service.build_weekly_players(
            _Session(), self._league(), self._Engine(), week
        )

    def test_a_player_left_out_of_a_published_week_scores_nothing(self, monkeypatch):
        # ESPN published week 6 and projected only the employed kicker.
        out = {p.name: p for p in self._build(monkeypatch, {22: 8.3})}

        assert out["Released Kicker"].week_points == 0.0, (
            "no projection must not become an invented one"
        )
        assert out["Released Kicker"].week_projection_missing is True
        assert out["Released Kicker"].week_projection_is_real is False

        assert out["Employed Kicker"].week_points == 8.3
        assert out["Employed Kicker"].week_projection_missing is False

    def test_an_unpublished_week_still_gets_the_season_average(self, monkeypatch):
        # Nothing published for the week at all -- a future week. The estimate is
        # the only number available, and it is labelled, not suppressed.
        out = {p.name: p for p in self._build(monkeypatch, {})}

        for player in out.values():
            assert player.week_points > 0, "a future week still needs an estimate"
            assert player.week_projection_is_real is False
            assert player.week_projection_missing is False, (
                "an estimate is not the same as a missing projection"
            )
