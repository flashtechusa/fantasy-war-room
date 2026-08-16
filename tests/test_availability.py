"""The ADP-based availability model."""

from __future__ import annotations

import pytest

from app.engine.availability import (
    AvailabilityModel,
    expected_best_available,
    expected_count_available,
    measure_market_drift,
    sigma_for,
    survival,
)


class TestSurvival:
    def test_a_late_adp_player_is_certain_at_pick_one(self):
        assert survival(1, 100.0) > 0.99

    def test_survival_falls_monotonically(self):
        values = [survival(pick, 30.0) for pick in range(1, 80)]
        assert all(a >= b for a, b in zip(values, values[1:]))

    def test_survival_is_about_half_at_adp(self):
        assert survival(24.0, 24.0) == pytest.approx(0.5, abs=0.06)

    def test_uncertainty_grows_with_adp(self):
        assert sigma_for(150) > sigma_for(10)

    def test_sigma_is_capped(self):
        assert sigma_for(10_000) == sigma_for(9_000)


class TestConditionalAvailability:
    def test_current_pick_is_certain(self):
        model = AvailabilityModel(current_pick=10, next_pick=25)
        assert model.probability_available_at(10, adp=3.0) == 1.0

    def test_early_adp_player_is_gone_by_our_next_pick(self):
        model = AvailabilityModel(current_pick=1, next_pick=24)
        assert model.probability_available_next(adp=2.0) < 0.05

    def test_late_adp_player_survives(self):
        model = AvailabilityModel(current_pick=1, next_pick=24)
        assert model.probability_available_next(adp=140.0) > 0.95

    def test_probabilities_sum_with_gone(self):
        model = AvailabilityModel(current_pick=20, next_pick=40)
        available = model.probability_available_next(adp=35.0)
        gone = model.probability_gone_next(adp=35.0)
        assert available + gone == pytest.approx(1.0, abs=1e-4)

    def test_conditioning_helps_a_player_who_has_already_fallen(self):
        """Surviving to pick 40 with an ADP of 30 makes lasting longer likelier."""
        unconditioned = survival(50, 30.0) / max(survival(1, 30.0), 1e-9)
        model = AvailabilityModel(current_pick=40, next_pick=50)
        assert model.probability_available_next(adp=30.0) > unconditioned

    def test_no_next_pick_means_nothing_to_protect(self):
        model = AvailabilityModel(current_pick=200, next_pick=None)
        assert model.probability_available_next(adp=5.0) == 1.0

    def test_probability_is_always_a_probability(self):
        model = AvailabilityModel(current_pick=60, next_pick=84)
        for adp in (1.0, 20.0, 60.0, 120.0, 400.0, None):
            value = model.probability_available_next(adp, fallback_rank=200)
            assert 0.0 <= value <= 1.0

    def test_missing_adp_falls_back_to_our_own_ranking(self):
        model = AvailabilityModel(current_pick=1, next_pick=24)
        early = model.probability_available_next(None, fallback_rank=5)
        late = model.probability_available_next(None, fallback_rank=250)
        assert late > early


class TestMarketDrift:
    def test_no_drift_without_enough_samples(self):
        assert measure_market_drift([(1, 3.0), (2, 5.0)]) == 0.0

    def test_reaching_room_produces_positive_drift(self):
        # Every player taken ~10 picks ahead of ADP.
        drafted = [(pick, pick + 10.0) for pick in range(1, 21)]
        assert measure_market_drift(drafted) > 0

    def test_slow_room_produces_negative_drift(self):
        drafted = [(pick, pick - 10.0) for pick in range(1, 21)]
        assert measure_market_drift(drafted) < 0

    def test_drift_is_capped(self):
        drafted = [(pick, pick + 500.0) for pick in range(1, 30)]
        assert measure_market_drift(drafted) <= 18.0

    def test_drift_shifts_effective_adp_earlier(self):
        model = AvailabilityModel(current_pick=1, next_pick=20, market_drift=8.0)
        plain = AvailabilityModel(current_pick=1, next_pick=20)
        assert model.probability_available_next(30.0) < plain.probability_available_next(30.0)

    def test_picks_without_adp_are_ignored(self):
        assert measure_market_drift([(i, None) for i in range(1, 30)]) == 0.0


class TestExpectedBest:
    def test_empty_pool_is_zero(self):
        assert expected_best_available([]) == 0.0

    def test_a_certain_player_gives_his_own_value(self):
        assert expected_best_available([(100.0, 1.0), (90.0, 1.0)]) == 100.0

    def test_expectation_sits_between_the_candidates(self):
        result = expected_best_available([(100.0, 0.5), (80.0, 1.0)])
        # 100*0.5 + 80*1.0*0.5 = 90
        assert result == pytest.approx(90.0, abs=0.01)

    def test_unsorted_input_is_handled(self):
        assert expected_best_available([(80.0, 1.0), (100.0, 0.5)]) == pytest.approx(90.0, 0.01)

    def test_lower_probabilities_lower_the_expectation(self):
        likely = expected_best_available([(100.0, 0.9), (50.0, 0.9)])
        unlikely = expected_best_available([(100.0, 0.1), (50.0, 0.9)])
        assert likely > unlikely

    def test_expected_count_is_the_sum_of_probabilities(self):
        assert expected_count_available([0.5, 0.25, 0.25]) == 1.0
