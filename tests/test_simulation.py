"""Phase 7 -- the draft simulator."""

from __future__ import annotations

from collections import Counter

import pytest

from app.engine.simulate import DraftSimulator


@pytest.fixture(scope="module")
def _sim_cache():
    return {}


@pytest.fixture
def simulation(engine):
    return DraftSimulator(engine, my_slot=5, seed=42).run(simulations=120)


class TestSimulationShape:
    def test_one_outcome_per_round(self, simulation, engine):
        assert len(simulation.picks) == engine.shape.draft_rounds

    def test_picks_follow_the_snake_for_my_slot(self, simulation):
        teams, slot = simulation.team_count, simulation.my_slot
        assert simulation.picks[0].overall_pick == slot
        assert simulation.picks[1].overall_pick == 2 * teams - slot + 1

    def test_position_shares_sum_to_one(self, simulation):
        for pick in simulation.picks:
            total = sum(pick.position_counts.values())
            assert total == pytest.approx(1.0, abs=0.02)

    def test_roster_percentiles_are_ordered(self, simulation):
        assert (
            simulation.p10_roster_points
            <= simulation.mean_roster_points
            <= simulation.p90_roster_points
        )

    def test_outcomes_vary(self, simulation):
        assert simulation.p90_roster_points > simulation.p10_roster_points

    def test_deterministic_for_a_given_seed(self, engine):
        first = DraftSimulator(engine, my_slot=5, seed=99).run(simulations=40)
        second = DraftSimulator(engine, my_slot=5, seed=99).run(simulations=40)
        assert first.mean_roster_points == second.mean_roster_points


class TestOpponentBehaviour:
    def test_opponents_do_not_take_the_same_player_every_time(self, engine):
        """Probabilistic ADP sampling, not greedy list-following.

        With slot 1, picks 2-19 are all opponents. If they drafted by list, the
        best player available at our second pick would be identical every run.
        """
        import random

        best_at_second_pick = Counter()
        for seed in range(20):
            simulator = DraftSimulator(engine, my_slot=1, seed=seed)
            _selections, availability = simulator._run_one(random.Random(seed))
            second_pick = sorted(availability)[1]
            top_available, _best_vor = availability[second_pick]
            best_at_second_pick[top_available[0].name] += 1
        assert len(best_at_second_pick) > 1

    def test_kickers_are_not_taken_early(self, simulation):
        early = simulation.picks[: max(len(simulation.picks) - 3, 1)]
        for pick in early:
            assert pick.position_counts.get("K", 0) == 0
            assert pick.position_counts.get("DST", 0) == 0

    def test_the_strategy_never_over_stacks_a_position(self, engine):
        import random

        simulator = DraftSimulator(engine, my_slot=6, seed=7)
        selections, _ = simulator._run_one(random.Random(7))
        counts = Counter(player.position for player in selections)
        assert counts["QB"] <= simulator.position_caps["QB"]
        assert counts["TE"] <= simulator.position_caps["TE"]


class TestDerivedStrategy:
    def test_a_strategy_is_produced(self, simulation):
        assert simulation.strategy
        assert any(line.startswith("Round 1") for line in simulation.strategy)

    def test_strategy_mentions_sweet_spots(self, simulation):
        assert any("sweet spot" in line or "safe to leave" in line
                   for line in simulation.strategy)

    def test_early_rounds_favour_the_scarce_positions(self, simulation):
        """In a 1-QB, half-PPR league that means RB/WR, derived not hard-coded."""
        first = simulation.picks[0].position_counts
        assert first.get("RB", 0) + first.get("WR", 0) > 0.6

    def test_wait_profiles_cover_the_real_positions(self, simulation):
        positions = {profile.position for profile in simulation.wait_profiles}
        assert {"QB", "RB", "WR", "TE"} <= positions
        assert "K" not in positions  # nothing meaningful to say about kickers

    def test_expected_value_decays_across_rounds(self, simulation):
        for profile in simulation.wait_profiles:
            assert profile.by_round[0] >= profile.by_round[-1]

    def test_each_profile_has_a_verdict_and_a_note(self, simulation):
        for profile in simulation.wait_profiles:
            assert profile.verdict in {"wait", "balanced", "dangerous"}
            assert profile.note


class TestSlotEffects:
    def test_the_first_slot_gets_a_better_first_pick(self, engine):
        first = DraftSimulator(engine, my_slot=1, seed=3).run(simulations=60)
        last = DraftSimulator(engine, my_slot=12, seed=3).run(simulations=60)
        assert first.picks[0].mean_taken_vor > last.picks[0].mean_taken_vor

    def test_availability_reports_are_produced(self, simulation):
        assert any(pick.likely_available for pick in simulation.picks)
        for pick in simulation.picks:
            for _name, _position, probability in pick.likely_available:
                assert 0.5 <= probability <= 1.0
