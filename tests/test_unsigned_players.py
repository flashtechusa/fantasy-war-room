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
