"""Roster construction: lineup optimisation, needs, byes, scoring."""

from __future__ import annotations

import pytest

from app.engine.league_shape import LeagueShape
from app.engine.roster import (
    RosterPlayer,
    bye_week_conflicts,
    build_optimal_lineup,
    positional_needs,
    positional_strength,
    roster_construction_score,
)


def player(name, position, points, bye=None, vor=0.0) -> RosterPlayer:
    return RosterPlayer(
        espn_player_id=abs(hash(name)) % 100000,
        name=name,
        position=position,
        projected_points=points,
        bye_week=bye,
        vor=vor,
    )


@pytest.fixture
def shape() -> LeagueShape:
    return LeagueShape.from_slots(
        12, {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}, bench_slots=6
    )


class TestLeagueShape:
    def test_counts_add_up(self, shape):
        assert shape.starter_slots == 9
        assert shape.roster_size == 15
        assert shape.draft_rounds == 15

    def test_flex_eligibility(self, shape):
        assert shape.flex_eligible("RB")
        assert shape.flex_eligible("TE")
        assert not shape.flex_eligible("QB")

    def test_superflex_detection(self):
        superflex = LeagueShape.from_slots(12, {"QB": 1, "OP": 1, "RB": 2}, bench_slots=5)
        assert superflex.is_superflex()
        assert superflex.flex_eligible("QB")

    def test_unknown_slots_are_ignored(self):
        shape = LeagueShape.from_slots(12, {"QB": 1, "LB": 3, "HC": 1}, bench_slots=4)
        assert shape.dedicated == {"QB": 1}

    def test_positions_in_play_are_ordered(self, shape):
        assert shape.positions_in_play == ["QB", "RB", "WR", "TE", "K", "DST"]


class TestOptimalLineup:
    def test_best_players_fill_dedicated_slots(self, shape):
        roster = [
            player("QB1", "QB", 380),
            player("RB1", "RB", 300),
            player("RB2", "RB", 250),
            player("RB3", "RB", 200),
            player("WR1", "WR", 290),
            player("WR2", "WR", 240),
            player("TE1", "TE", 180),
            player("K1", "K", 140),
            player("D1", "DST", 130),
        ]
        lineup = build_optimal_lineup(roster, shape)
        assigned = {a.slot: a.player.name for a in lineup.starters if a.player}
        assert assigned["QB"] == "QB1"
        assert assigned["TE"] == "TE1"
        # The third RB is the best flex-eligible player left.
        assert assigned["FLEX"] == "RB3"
        assert lineup.total_points == 2110

    def test_flex_takes_the_best_leftover_regardless_of_position(self, shape):
        roster = [
            player("RB1", "RB", 300),
            player("RB2", "RB", 250),
            player("WR1", "WR", 290),
            player("WR2", "WR", 240),
            player("TE1", "TE", 260),
            player("TE2", "TE", 210),
        ]
        lineup = build_optimal_lineup(roster, shape)
        assigned = {a.slot: a.player.name for a in lineup.starters if a.player}
        assert assigned["TE"] == "TE1"
        assert assigned["FLEX"] == "TE2"

    def test_nobody_starts_twice(self, shape):
        roster = [player(f"P{i}", "RB", 200 - i) for i in range(4)]
        lineup = build_optimal_lineup(roster, shape)
        started = [a.player.name for a in lineup.starters if a.player]
        assert len(started) == len(set(started))

    def test_empty_slots_are_reported(self, shape):
        lineup = build_optimal_lineup([player("RB1", "RB", 300)], shape)
        assert "QB" in lineup.empty_slots
        assert lineup.total_points == 300

    def test_extras_go_to_the_bench(self, shape):
        roster = [player(f"RB{i}", "RB", 300 - i * 10) for i in range(6)]
        lineup = build_optimal_lineup(roster, shape)
        # 2 RB slots + 1 FLEX = 3 starters, 3 on the bench.
        assert len(lineup.bench) == 3

    def test_empty_roster_is_safe(self, shape):
        lineup = build_optimal_lineup([], shape)
        assert lineup.total_points == 0.0
        assert len(lineup.empty_slots) == shape.starter_slots

    def test_weekly_points_is_the_season_total_over_17(self, shape):
        lineup = build_optimal_lineup([player("RB1", "RB", 340)], shape)
        assert lineup.weekly_points == 20.0


class TestPositionalNeeds:
    """Need is marginal value over replacement, not empty-slot counting."""

    def _replacement(self):
        return {"QB": 240.0, "RB": 100.0, "WR": 105.0, "TE": 90.0, "K": 120.0, "DST": 115.0}

    def test_empty_roster_prefers_the_biggest_edge_not_the_biggest_score(self, shape):
        best = {"QB": 400.0, "RB": 330.0, "WR": 315.0, "TE": 245.0, "K": 150.0, "DST": 148.0}
        needs = positional_needs([], shape, best, self._replacement())
        # QB scores most in absolute terms but is the most replaceable.
        assert needs["RB"].marginal_value > needs["QB"].marginal_value
        assert needs["WR"].marginal_value > needs["QB"].marginal_value
        assert needs["K"].marginal_value < needs["TE"].marginal_value

    def test_filling_a_position_reduces_its_need(self, shape):
        best = {"QB": 400.0, "RB": 330.0, "WR": 315.0, "TE": 245.0}
        empty = positional_needs([], shape, best, self._replacement())
        stocked = positional_needs(
            [player("QB1", "QB", 395)], shape, best, self._replacement()
        )
        assert stocked["QB"].marginal_value < empty["QB"].marginal_value

    def test_need_score_is_normalised_to_the_peak(self, shape):
        best = {"QB": 400.0, "RB": 330.0, "WR": 315.0}
        needs = positional_needs([], shape, best, self._replacement())
        assert max(need.need_score for need in needs.values()) == pytest.approx(1.0)
        assert all(0.0 <= need.need_score <= 1.0 for need in needs.values())

    def test_marginal_value_is_never_negative(self, shape):
        roster = [player(f"RB{i}", "RB", 300 - i * 5) for i in range(6)]
        needs = positional_needs(roster, shape, {"RB": 120.0}, self._replacement())
        assert needs["RB"].marginal_value >= 0.0

    def test_urgency_fires_only_when_the_draft_is_running_out(self, shape):
        best = {"QB": 400.0, "RB": 330.0}
        early = positional_needs([], shape, best, self._replacement(), rounds_remaining=12)
        late = positional_needs([], shape, best, self._replacement(), rounds_remaining=1)
        assert not early["QB"].urgent
        assert late["QB"].urgent

    def test_missing_position_has_zero_need(self, shape):
        needs = positional_needs([], shape, {"RB": 330.0}, self._replacement())
        assert needs["QB"].marginal_value == 0.0

    def test_starters_filled_is_capped_at_the_requirement(self, shape):
        roster = [player(f"RB{i}", "RB", 300 - i) for i in range(5)]
        needs = positional_needs(roster, shape, {"RB": 200.0}, self._replacement())
        assert needs["RB"].starters_filled == 2


class TestByeConflicts:
    def test_two_starters_on_the_same_bye_is_a_conflict(self, shape):
        roster = [
            player("RB1", "RB", 300, bye=9),
            player("WR1", "WR", 290, bye=9),
            player("QB1", "QB", 380, bye=5),
        ]
        conflicts = bye_week_conflicts(roster, shape)
        assert len(conflicts) == 1
        assert conflicts[0].week == 9
        assert conflicts[0].severity == "moderate"

    def test_three_starters_is_high_severity(self, shape):
        roster = [
            player("RB1", "RB", 300, bye=9),
            player("WR1", "WR", 290, bye=9),
            player("TE1", "TE", 200, bye=9),
        ]
        assert bye_week_conflicts(roster, shape)[0].severity == "high"

    def test_bench_players_do_not_create_conflicts(self, shape):
        roster = [player(f"RB{i}", "RB", 300 - i * 40, bye=9) for i in range(6)]
        conflicts = bye_week_conflicts(roster, shape)
        # Only the 3 who start (RB, RB, FLEX) count.
        assert conflicts[0].players and len(conflicts[0].players) == 3

    def test_no_bye_data_means_no_conflicts(self, shape):
        assert bye_week_conflicts([player("RB1", "RB", 300)], shape) == []


class TestRosterScore:
    def test_score_is_bounded(self, shape):
        for roster in ([], [player("RB1", "RB", 400, vor=300)] * 3):
            strengths = positional_strength(roster, shape, {}, {})
            score, _ = roster_construction_score(roster, shape, strengths, [], picks_made=3)
            assert 0.0 <= score <= 100.0

    def test_a_strong_roster_scores_better_than_a_weak_one(self, shape):
        replacement = {"QB": 240.0, "RB": 100.0, "WR": 105.0, "TE": 90.0}
        average = {"QB": 300.0, "RB": 180.0, "WR": 185.0, "TE": 140.0}

        strong = [
            player("QB1", "QB", 390, bye=5, vor=150),
            player("RB1", "RB", 330, bye=6, vor=230),
            player("WR1", "WR", 315, bye=7, vor=210),
        ]
        weak = [
            player("QB9", "QB", 250, bye=5, vor=10),
            player("RB40", "RB", 130, bye=6, vor=30),
            player("WR40", "WR", 135, bye=7, vor=30),
        ]
        strong_score, _ = roster_construction_score(
            strong, shape, positional_strength(strong, shape, replacement, average), [], 3
        )
        weak_score, _ = roster_construction_score(
            weak, shape, positional_strength(weak, shape, replacement, average), [], 3
        )
        assert strong_score > weak_score

    def test_bye_conflicts_cost_points(self, shape):
        roster = [player("RB1", "RB", 300, bye=9, vor=200),
                  player("WR1", "WR", 290, bye=9, vor=180)]
        strengths = positional_strength(roster, shape, {}, {})
        clean, _ = roster_construction_score(roster, shape, strengths, [], 2)
        conflicted, _ = roster_construction_score(
            roster, shape, strengths, bye_week_conflicts(roster, shape), 2
        )
        assert conflicted < clean

    def test_notes_explain_the_score(self, shape):
        roster = [player("RB1", "RB", 300, vor=200)]
        _score, notes = roster_construction_score(
            roster, shape, positional_strength(roster, shape, {}, {}), [], 1
        )
        assert notes
        assert any("starting slot" in note.lower() for note in notes)

    def test_undrafted_positions_are_not_graded_as_weak(self, shape):
        """An empty slot is unfilled, not a deficiency -- Needs covers those."""
        roster = [player("RB1", "RB", 330, vor=230)]
        strengths = positional_strength(
            roster, shape, {"RB": 100.0, "QB": 240.0}, {"RB": 180.0, "QB": 300.0}
        )
        assert [s.position for s in strengths] == ["RB"]
