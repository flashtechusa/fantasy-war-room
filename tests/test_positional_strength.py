"""The bar a team's positions are graded against.

Two failures on a live league drove these tests, and both are about the bar
rather than the roster:

* RB read STRONG, then WEAK, with the same two running backs -- filling the
  flex with a third added his points to one side of the comparison and a full
  average starter to the other.
* Every team's construction score fell at once, days apart from any roster
  change, because the bar was the mean of the top N of the imported *player
  pool* and the pool had changed size.
"""

from __future__ import annotations

import pytest

from app.engine.league_shape import LeagueShape
from app.engine.roster import (
    RosterPlayer,
    build_optimal_lineup,
    positional_strength,
    starter_tiers,
)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1}


@pytest.fixture
def shape() -> LeagueShape:
    return LeagueShape.from_slots(team_count=12, roster_slots=SLOTS, bench_slots=6)


def player(name: str, position: str, points: float) -> RosterPlayer:
    return RosterPlayer(
        espn_player_id=abs(hash(name)) % 100000,
        name=name,
        position=position,
        projected_points=points,
    )


def a_team(rb3: float | None = None, wr3: float | None = None) -> list[RosterPlayer]:
    """A roster whose flex is an RB, a WR, or empty."""
    roster = [
        player("QB1", "QB", 440.0),
        player("RB1", "RB", 490.0),
        player("RB2", "RB", 418.0),
        player("WR1", "WR", 282.0),
        player("WR2", "WR", 279.0),
        player("TE1", "TE", 297.0),
        player("K1", "K", 157.0),
        player("D1", "DST", 165.0),
    ]
    if rb3 is not None:
        roster.append(player("RB3", "RB", rb3))
    if wr3 is not None:
        roster.append(player("WR3", "WR", wr3))
    return roster


def league_of(rosters: list[list[RosterPlayer]], shape: LeagueShape) -> dict[str, list[float]]:
    return starter_tiers([build_optimal_lineup(r, shape) for r in rosters])


def strength_for(roster, shape, tiers, position: str):
    strengths = positional_strength(
        roster, shape, replacement_points={}, average_starter_points={},
        league_starter_tiers=tiers,
    )
    return next(s for s in strengths if s.position == position)


class TestStarterTiers:
    def test_tiers_are_ranked_not_averaged(self, shape):
        tiers = league_of([a_team(rb3=263.0), a_team(rb3=263.0)], shape)
        assert tiers["RB"] == [490.0, 418.0, 263.0]

    def test_a_slot_only_some_teams_fill_averages_over_those_teams(self, shape):
        """Nine teams start two RBs; one flexes a third. The third tier is his."""
        rosters = [a_team(wr3=250.0) for _ in range(9)] + [a_team(rb3=263.0)]
        tiers = league_of(rosters, shape)
        assert tiers["RB"][:2] == [490.0, 418.0]
        assert tiers["RB"][2] == 263.0

    def test_no_rosters_yields_no_tiers(self):
        assert starter_tiers([]) == {}


class TestFlexDoesNotFlipTheGrade:
    """The bug as it was reported: 'my running backs went from strong to weak'."""

    def test_adding_a_third_rb_does_not_turn_rb_from_strong_to_weak(self, shape):
        # A league where every other team starts the same two RBs we do.
        others = [a_team(wr3=250.0) for _ in range(11)]

        without_flex_rb = a_team(wr3=250.0)
        with_flex_rb = a_team(rb3=263.0)

        tiers = league_of(others + [with_flex_rb], shape)

        before = strength_for(without_flex_rb, shape, tiers, "RB")
        after = strength_for(with_flex_rb, shape, tiers, "RB")

        assert before.grade not in {"weak", "critical"}
        assert after.grade not in {"weak", "critical"}, (
            f"same two RBs, but flexing a third made RB {after.grade} "
            f"(edge {after.edge})"
        )

    def test_the_flex_player_is_measured_against_other_flex_players(self, shape):
        """A third RB better than everyone else's third is an advantage."""
        others = [a_team(rb3=200.0) for _ in range(11)]
        mine = a_team(rb3=300.0)
        tiers = league_of(others + [mine], shape)

        assert strength_for(mine, shape, tiers, "RB").edge > 0
        assert strength_for(others[0], shape, tiers, "RB").edge < 0

    def test_the_old_mean_bar_is_what_flipped_it(self, shape):
        """Kept as the regression: the fallback path still shows the defect."""
        mean_bar = {"RB": 418.4}
        two = positional_strength(
            a_team(wr3=250.0), shape, {}, mean_bar, league_starter_tiers=None
        )
        three = positional_strength(
            a_team(rb3=263.0), shape, {}, mean_bar, league_starter_tiers=None
        )
        rb_two = next(s for s in two if s.position == "RB")
        rb_three = next(s for s in three if s.position == "RB")
        assert rb_two.edge > 0 and rb_three.edge < 0
        # Which is exactly why the tier bar is preferred wherever rosters exist.


class TestTheBarIsCentredOnTheLeague:
    def test_identical_teams_all_land_at_zero(self, shape):
        rosters = [a_team(rb3=263.0) for _ in range(12)]
        tiers = league_of(rosters, shape)
        for position in ("QB", "RB", "WR", "TE", "K", "DST"):
            assert strength_for(rosters[0], shape, tiers, position).edge == 0.0

    def test_edges_across_the_league_sum_to_about_zero(self, shape):
        rosters = [a_team(rb3=200.0 + index * 20) for index in range(12)]
        tiers = league_of(rosters, shape)
        total = sum(
            sum(
                s.edge
                for s in positional_strength(
                    roster, shape, {}, {}, league_starter_tiers=tiers
                )
            )
            for roster in rosters
        )
        assert abs(total) < 1.0, f"the scale is off-centre by {total:.1f} points"

    def test_the_bar_ignores_the_size_of_the_player_pool(self, shape):
        """The league-wide drop: a deeper import must not move anyone's grade.

        The pool feeds `average_starter_points`; the tiers come from rosters.
        Passing wildly different pool means must change nothing.
        """
        rosters = [a_team(rb3=263.0) for _ in range(12)]
        tiers = league_of(rosters, shape)
        thin = {"QB": 300.0, "RB": 250.0, "WR": 200.0, "TE": 150.0}
        deep = {"QB": 563.4, "RB": 418.4, "WR": 396.9, "TE": 274.2}

        a = positional_strength(rosters[0], shape, {}, thin, league_starter_tiers=tiers)
        b = positional_strength(rosters[0], shape, {}, deep, league_starter_tiers=tiers)
        assert [(s.position, s.edge, s.grade) for s in a] == [
            (s.position, s.edge, s.grade) for s in b
        ]
