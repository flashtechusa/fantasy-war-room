"""Uneven-trade roster-move analysis: overflow detection and safe drop ranking.

The properties that matter:
- both sides' resulting sizes are computed, and each is compared to the *active*
  roster limit (bench included, IR excluded);
- a 1-for-2 needs one drop, a 1-for-3 needs two, an even swap needs none;
- the recommendation prefers a drop that does NOT open a starting-lineup hole,
  even when a hole-creating player has lower value.
"""

from __future__ import annotations

from app.engine.league_shape import LeagueShape
from app.engine.roster import RosterPlayer
from app.engine.roster_move import active_roster_limit, analyse_roster_move

SHAPE = LeagueShape.from_slots(
    team_count=12,
    roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1},
    bench_slots=1,
    ir_slots=2,
)
LIMIT = active_roster_limit({"QB": 1, "RB": 2, "WR": 2, "TE": 1}, 1)  # 6 starters + 1 bench = 7


def P(pid, pos, pts, vor=None):
    return RosterPlayer(
        espn_player_id=pid, name=f"P{pid}", position=pos,
        projected_points=pts, vor=pts if vor is None else vor,
    )


def test_active_limit_excludes_ir():
    # 15 active (9 starters + 6 bench); the 2 IR slots are not part of the cap.
    assert active_roster_limit(
        {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1}, 6
    ) == 15


def _roster7():
    # A legal, full 7-man roster (QB, 2 RB, 2 WR, TE, + 1 bench WR).
    return [
        P(1, "QB", 300), P(2, "RB", 250), P(3, "RB", 240),
        P(4, "WR", 200), P(5, "WR", 190), P(6, "TE", 150), P(7, "WR", 60),
    ]


def _analyse(my, give, receive, their=None):
    return analyse_roster_move(
        shape=SHAPE, roster_limit=LIMIT,
        my_team_id=1, my_team_name="Me", their_team_id=2, their_team_name="Them",
        my_current=my, their_current=their or [P(90 + i, "RB", 100) for i in range(7)],
        give=give, receive=receive,
    )


def test_one_for_two_requires_one_drop():
    my = _roster7()
    a = _analyse(my, give=[my[6]], receive=[P(20, "RB", 220), P(21, "RB", 210)])
    assert a.mine.current_size == 7 and a.mine.resulting_size == 8
    assert a.mine.drops_required == 1
    assert len(a.recommended_ids) == 1
    assert a.theirs.drops_required == 0


def test_one_for_three_requires_two_drops():
    my = _roster7()
    a = _analyse(
        my, give=[my[6]],
        receive=[P(20, "RB", 220), P(21, "RB", 210), P(22, "WR", 205)],
    )
    assert a.mine.resulting_size == 9
    assert a.mine.drops_required == 2
    assert len(a.recommended_ids) == 2
    assert len(set(a.recommended_ids)) == 2  # two distinct players


def test_even_trade_requires_no_drop():
    my = _roster7()
    a = _analyse(my, give=[my[6], my[5]], receive=[P(20, "RB", 220), P(21, "TE", 160)])
    assert a.mine.resulting_size == 7
    assert a.mine.drops_required == 0
    assert a.recommended_ids == []


def test_other_team_overflow_is_detected():
    my = _roster7()
    their = _roster7()  # they are also full at 7
    # I give 2, receive 1: my roster shrinks (7->6), theirs grows (7->8).
    a = _analyse(my, give=[my[6], my[5]], receive=[P(20, "RB", 220)], their=their)
    assert a.mine.drops_required == 0
    assert a.theirs.resulting_size == 8
    assert a.theirs.drops_required == 1


def test_recommendation_avoids_a_starting_hole():
    # The QB is the lowest-value player, but it is the only QB -- dropping it
    # opens a starting hole. The recommendation must avoid it and cut a surplus
    # RB instead, even though the QB has lower value.
    my = [
        P(1, "QB", 40, vor=1),  # only QB, lowest value
        P(2, "RB", 250), P(3, "RB", 240),
        P(4, "WR", 200), P(5, "WR", 190), P(6, "TE", 150), P(7, "WR", 60),
    ]
    a = _analyse(my, give=[my[6]], receive=[P(20, "RB", 220), P(21, "RB", 210)])
    assert a.mine.drops_required == 1
    rec_id = a.recommended_ids[0]
    assert rec_id != 1, "must not recommend dropping the only QB (opens a hole)"
    rec = next(c for c in a.candidates if c.espn_player_id == rec_id)
    assert rec.creates_hole is False
    # And the QB is flagged as hole-creating in the candidate list.
    qb = next(c for c in a.candidates if c.espn_player_id == 1)
    assert qb.creates_hole is True
