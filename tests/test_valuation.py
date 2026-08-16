"""Phase 3 -- the Draft Score, its components and its explanations."""

from __future__ import annotations

import pytest

from app.engine.valuation import SCORE_WEIGHTS


class TestBoardOrdering:
    def test_board_is_sorted_by_draft_score(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        scores = [v.draft_score for v in board.valuations if not v.is_drafted]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_are_dense_and_start_at_one(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        available = [v for v in board.valuations if not v.is_drafted]
        assert [v.overall_rank for v in available[:5]] == [1, 2, 3, 4, 5]

    def test_drafted_players_sink_to_the_bottom(self, engine):
        top = engine.build_board(current_pick=1, my_slot=1, next_pick=24).top(1)[0]
        board = engine.build_board(
            drafted_ids={top.espn_player_id}, current_pick=2, my_slot=1, next_pick=24
        )
        assert board.valuations[-1].is_drafted or board.top(1)[0].espn_player_id != top.espn_player_id
        assert board.top(1)[0].espn_player_id != top.espn_player_id

    def test_kickers_and_defenses_are_suppressed_early(self, engine):
        board = engine.build_board(
            current_pick=1, my_slot=1, next_pick=24, rounds_remaining=15
        )
        top_positions = {v.position for v in board.top(30)}
        assert "K" not in top_positions
        assert "DST" not in top_positions

    def test_kickers_become_draftable_at_the_end(self, engine):
        early = engine.build_board(current_pick=1, my_slot=1, next_pick=24, rounds_remaining=15)
        late = engine.build_board(current_pick=170, my_slot=1, next_pick=175, rounds_remaining=1)
        early_k = next(v for v in early.valuations if v.position == "K")
        late_k = next(v for v in late.valuations if v.position == "K")
        assert late_k.draft_score > early_k.draft_score

    def test_the_top_of_the_board_is_positive_vor(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        assert all(v.vor > 0 for v in board.top(20))


class TestScoreComposition:
    def test_weights_sum_to_one(self):
        assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_every_component_is_present_and_bounded(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for valuation in board.top(10):
            keys = {component.key for component in valuation.components}
            assert keys == set(SCORE_WEIGHTS)
            for component in valuation.components:
                assert 0.0 <= component.value <= 100.0
                assert component.weight == SCORE_WEIGHTS[component.key]

    def test_components_reconstruct_the_score(self, engine):
        """Transparency guarantee: the parts must add up to the whole."""
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for valuation in board.top(15):
            total = sum(component.contribution for component in valuation.components)
            if valuation.flags:
                assert valuation.draft_score <= round(total, 1) + 0.1
            else:
                assert valuation.draft_score == pytest.approx(total, abs=0.15)

    def test_scores_are_bounded(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        assert all(0.0 <= v.draft_score <= 100.0 for v in board.valuations)

    def test_every_component_carries_an_explanation(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for component in board.top(1)[0].components:
            assert component.label
            assert component.detail

    def test_injury_designation_reduces_the_score(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        injured = [v for v in board.valuations if "injury risk" in v.flags]
        assert injured
        for valuation in injured:
            total = sum(component.contribution for component in valuation.components)
            assert valuation.draft_score < total


class TestExplanations:
    def test_every_recommendation_has_reasons(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for valuation in board.top(10):
            assert len(valuation.reasons) >= 2
            assert valuation.why_now
            assert valuation.why_wait
            assert valuation.principal_risk

    def test_the_first_reason_states_the_vor_ranking(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        assert "by VOR" in board.top(1)[0].reasons[0]

    def test_reasons_never_compare_against_kickers(self, engine):
        """'Deeper than kicker' is noise, not insight."""
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for valuation in board.top(20):
            for reason in valuation.reasons:
                if "alternatives are substantially deeper" in reason:
                    assert "K/" not in reason and "DST" not in reason

    def test_urgency_is_quoted_when_a_player_will_be_gone(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        best = board.top(1)[0]
        assert best.gone_probability > 0.5
        assert any("unavailable at our next selection" in r for r in best.reasons)

    def test_no_next_pick_means_no_urgency_language(self, engine):
        board = engine.build_board(current_pick=225, my_slot=1, next_pick=None)
        for valuation in board.top(5):
            assert not any("unavailable at our next" in r for r in valuation.reasons)


class TestDraftStateEffects:
    def test_roster_need_shifts_recommendations(self, engine):
        base = engine.build_board(current_pick=25, my_slot=1, next_pick=48)
        rb_ids = {
            v.espn_player_id for v in base.valuations if v.position == "RB" and not v.is_drafted
        }
        stocked = engine.build_board(
            current_pick=25,
            my_slot=1,
            next_pick=48,
            my_player_ids=set(list(rb_ids)[:4]),
            drafted_ids=set(list(rb_ids)[:4]),
        )
        assert stocked.needs["RB"].marginal_value < base.needs["RB"].marginal_value

    def test_need_does_not_override_value_entirely(self, engine):
        """A roster hole must not make the engine reach for a bad player."""
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        te_ids = {v.espn_player_id for v in board.valuations if v.position == "TE"}
        # Fill every position except TE with the best available.
        others = [v.espn_player_id for v in board.top(60) if v.espn_player_id not in te_ids][:8]
        with_hole = engine.build_board(
            current_pick=100,
            my_slot=1,
            next_pick=120,
            my_player_ids=set(others),
            drafted_ids=set(others),
            rounds_remaining=6,
        )
        best = with_hole.top(1)[0]
        assert best.vor > 0, "should never recommend a below-replacement player"

    def test_availability_responds_to_the_gap(self, engine):
        near = engine.build_board(current_pick=10, my_slot=1, next_pick=12)
        far = engine.build_board(current_pick=10, my_slot=1, next_pick=40)
        near_by_id = {v.espn_player_id: v for v in near.valuations}
        for valuation in far.valuations[:40]:
            counterpart = near_by_id[valuation.espn_player_id]
            assert counterpart.availability_next_pick >= valuation.availability_next_pick - 1e-9

    def test_market_drift_is_measured_from_the_picks_made(self, engine):
        board = engine.build_board(
            current_pick=25,
            my_slot=1,
            next_pick=48,
            drafted_with_picks=[(pick, pick + 12.0) for pick in range(1, 25)],
        )
        assert board.market_drift > 0

    def test_scarcity_is_reported_per_position(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for position in ("QB", "RB", "WR", "TE"):
            scarcity = board.scarcity[position]
            assert scarcity.starters_left > 0
            assert 0 <= scarcity.scarcity_score <= 100
            assert scarcity.explain()


class TestUpsideAndFloor:
    def test_floor_is_below_the_projection_and_upside_above(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        for valuation in board.top(25):
            assert valuation.floor_points <= valuation.projected_points
            assert valuation.upside_points >= valuation.projected_points

    def test_injured_players_have_a_lower_floor_ratio(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        healthy, hurt = [], []
        for valuation in board.valuations:
            if valuation.projected_points <= 0:
                continue
            ratio = valuation.floor_points / valuation.projected_points
            (hurt if valuation.injury_status == "OUT" else healthy).append(ratio)
        assert hurt and healthy
        assert sum(hurt) / len(hurt) < sum(healthy) / len(healthy)


class TestLineupHelpers:
    def test_optimal_lineup_from_ids(self, engine):
        board = engine.build_board(current_pick=1, my_slot=1, next_pick=24)
        ids = {v.espn_player_id for v in board.top(9)}
        lineup = engine.optimal_lineup(ids)
        assert lineup.total_points > 0

    def test_average_starter_points_exist_per_position(self, engine):
        averages = engine.average_starter_points()
        assert {"QB", "RB", "WR", "TE"} <= set(averages)
        assert all(value > 0 for value in averages.values())
