"""Replacement level and VOR.

The properties that matter: baselines must move with league size, starting
requirements and flex configuration, and must not be affected by things that
shouldn't change them.
"""

from __future__ import annotations

import pytest

from app.engine.league_shape import LeagueShape
from app.engine.replacement import compute_replacement_levels


def descending(count: int, top: float, bottom: float) -> list[float]:
    """A clean linear points curve, so expectations are exactly checkable."""
    if count == 1:
        return [top]
    step = (top - bottom) / (count - 1)
    return [round(top - step * i, 3) for i in range(count)]


@pytest.fixture
def pool() -> dict[str, list[float]]:
    return {
        "QB": descending(40, 400, 150),
        "RB": descending(90, 330, 20),
        "WR": descending(120, 315, 15),
        "TE": descending(45, 245, 20),
        "K": descending(32, 152, 95),
        "DST": descending(32, 148, 75),
    }


def shape_for(team_count=12, flex=1, rb=2, wr=2, qb=1, bench=6) -> LeagueShape:
    slots = {"QB": qb, "RB": rb, "WR": wr, "TE": 1, "K": 1, "DST": 1}
    if flex:
        slots["FLEX"] = flex
    return LeagueShape.from_slots(team_count, slots, bench_slots=bench)


class TestDemandAccounting:
    def test_demand_is_starters_plus_flex_plus_bench(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        for baseline in levels.positions.values():
            assert baseline.demand == (
                baseline.starter_demand + baseline.flex_demand + baseline.bench_demand
            )

    def test_starter_demand_is_teams_times_slots(self, pool):
        levels = compute_replacement_levels(pool, shape_for(team_count=12, rb=2))
        assert levels.positions["RB"].starter_demand == 24
        assert levels.positions["QB"].starter_demand == 12

    def test_flex_demand_is_shared_among_eligible_positions(self, pool):
        shape = shape_for(team_count=12, flex=1)
        levels = compute_replacement_levels(pool, shape)
        flex_total = sum(levels.positions[p].flex_demand for p in ("RB", "WR", "TE"))
        assert flex_total == 12
        assert levels.positions["QB"].flex_demand == 0

    def test_bench_allocation_favours_rb_and_wr_over_kickers(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        assert levels.positions["K"].bench_demand <= 3
        assert levels.positions["DST"].bench_demand <= 3
        assert levels.positions["RB"].bench_demand > levels.positions["K"].bench_demand
        assert levels.positions["WR"].bench_demand > levels.positions["TE"].bench_demand

    def test_total_bench_allocation_does_not_exceed_supply(self, pool):
        shape = shape_for(team_count=12, bench=6)
        levels = compute_replacement_levels(pool, shape)
        allocated = sum(b.bench_demand for b in levels.positions.values())
        assert allocated <= shape.bench_slots * shape.team_count

    def test_converges(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        assert 1 <= levels.iterations <= 8


class TestLeagueSensitivity:
    def test_bigger_leagues_push_replacement_deeper_and_lower(self, pool):
        small = compute_replacement_levels(pool, shape_for(team_count=8))
        large = compute_replacement_levels(pool, shape_for(team_count=14))
        assert large.positions["RB"].demand > small.positions["RB"].demand
        assert large.positions["RB"].replacement_points < small.positions["RB"].replacement_points

    def test_superflex_raises_qb_demand_and_lowers_qb_replacement(self, pool):
        one_qb = LeagueShape.from_slots(
            12, {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}, bench_slots=6
        )
        superflex = LeagueShape.from_slots(
            12,
            {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "OP": 1, "K": 1, "DST": 1},
            bench_slots=6,
        )
        assert superflex.is_superflex()
        base = compute_replacement_levels(pool, one_qb)
        flex = compute_replacement_levels(pool, superflex)
        assert flex.positions["QB"].demand > base.positions["QB"].demand
        assert flex.positions["QB"].replacement_points < base.positions["QB"].replacement_points

    def test_two_qb_leagues_double_qb_starter_demand(self, pool):
        single = compute_replacement_levels(pool, shape_for(qb=1))
        double = compute_replacement_levels(pool, shape_for(qb=2))
        assert double.positions["QB"].starter_demand == 2 * single.positions["QB"].starter_demand

    def test_more_flex_slots_deepen_rb_wr_demand(self, pool):
        none = compute_replacement_levels(pool, shape_for(flex=0))
        three = compute_replacement_levels(pool, shape_for(flex=3))
        assert three.positions["RB"].demand + three.positions["WR"].demand > (
            none.positions["RB"].demand + none.positions["WR"].demand
        )

    def test_deeper_benches_lower_replacement(self, pool):
        shallow = compute_replacement_levels(pool, shape_for(bench=3))
        deep = compute_replacement_levels(pool, shape_for(bench=9))
        assert deep.positions["RB"].replacement_points < shallow.positions["RB"].replacement_points


class TestVor:
    def test_vor_is_points_minus_replacement(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        replacement = levels.replacement_for("RB")
        assert levels.vor("RB", replacement + 50) == pytest.approx(50, abs=0.01)

    def test_replacement_level_player_has_zero_vor(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        assert levels.vor("WR", levels.replacement_for("WR")) == pytest.approx(0, abs=0.01)

    def test_vor_is_negative_below_replacement(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        assert levels.vor("TE", levels.replacement_for("TE") - 30) < 0

    def test_elite_rb_beats_elite_qb_in_a_one_qb_league(self, pool):
        """QBs score more but are replaceable; VOR must reflect that."""
        levels = compute_replacement_levels(pool, shape_for(qb=1))
        assert levels.vor("RB", pool["RB"][0]) > levels.vor("QB", pool["QB"][0])

    def test_unknown_position_has_no_replacement(self, pool):
        levels = compute_replacement_levels(pool, shape_for())
        assert levels.replacement_for("LB") == 0.0


class TestFlexBaselines:
    def test_starter_bar_is_above_replacement(self, pool):
        levels = compute_replacement_levels(pool, shape_for(flex=1))
        assert levels.flex_starter_bar > levels.flex_replacement_points

    def test_starter_bar_beats_the_worst_dedicated_baseline(self, pool):
        levels = compute_replacement_levels(pool, shape_for(flex=1))
        assert levels.flex_starter_bar > levels.positions["RB"].replacement_points

    def test_no_flex_slot_still_produces_a_bar(self, pool):
        levels = compute_replacement_levels(pool, shape_for(flex=0))
        assert levels.flex_starter_bar > 0
        assert levels.flex_demand_total == 0


class TestRobustness:
    def test_empty_pool_does_not_crash(self):
        levels = compute_replacement_levels({}, shape_for())
        assert all(b.replacement_points == 0.0 for b in levels.positions.values())

    def test_pool_smaller_than_demand_is_clamped(self):
        tiny = {position: descending(3, 100, 50) for position in
                ("QB", "RB", "WR", "TE", "K", "DST")}
        levels = compute_replacement_levels(tiny, shape_for())
        assert all(b.replacement_points > 0 for b in levels.positions.values())

    def test_serialisation_round_trip(self, pool):
        payload = compute_replacement_levels(pool, shape_for()).as_dict()
        assert "positions" in payload and "flex_starter_bar" in payload
        assert payload["positions"]["RB"]["explanation"]
