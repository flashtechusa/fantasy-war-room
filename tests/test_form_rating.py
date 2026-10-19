"""Rating players on what they have actually done.

Three complaints from real use had one cause: every valuation came from the
preseason projection, and the app imported no actual results at all.

- A top-four kicker (64 points, 10.7 a game) looked replaceable.
- An unsigned kicker looked like an upgrade on him.
- Khalil Shakir scored 17.9, 17.5 and 14.8 in consecutive weeks and was never
  recommended -- 73 points in six games was invisible.

The rules this pins down:
  * before a snap is played nothing changes at all;
  * evidence outweighs August in proportion to the sample;
  * recent games count for more than September;
  * usage moves the number before the points do, but only a little.
"""

from __future__ import annotations

from app.engine.form import (
    EVIDENCE_HALF_LIFE_GAMES,
    SeasonForm,
    actual_rate,
    blended_rate,
    evidence_weight,
    explain,
    opportunities_in,
    usage_multiplier,
)

#: ESPN stat ids: receiving targets, rushing attempts, pass attempts.
TARGETS, CARRIES, PASS_ATTEMPTS = "58", "23", "0"


class TestEvidenceBeatsTheProjectionSlowly:
    def test_nothing_changes_before_a_game_is_played(self):
        # The property that makes this safe to switch on mid-season: a player
        # nobody has seen is valued exactly as he was before.
        assert blended_rate(9.4, None) == 9.4
        assert blended_rate(9.4, SeasonForm()) == 9.4
        assert evidence_weight(0) == 0.0

    def test_the_weight_climbs_with_the_sample(self):
        assert evidence_weight(1) < evidence_weight(4) < evidence_weight(8)
        # Half the weight at exactly the half-life, by construction.
        assert evidence_weight(EVIDENCE_HALF_LIFE_GAMES) == 0.5
        # Never certain, however long the season runs.
        assert evidence_weight(17) < 1.0

    def test_one_big_game_does_not_rewrite_a_player(self):
        # Week 1, 30 points, preseason said 8. The rating moves, but not far:
        # one game is one game.
        form = SeasonForm(games_played=1, points=30.0, recent_games=1, recent_points=30.0)
        rated = blended_rate(8.0, form)
        assert 8.0 < rated < 14.0, rated

    def test_a_season_of_the_same_moves_it_a_long_way(self):
        # The same per-game production over eight games is no longer a fluke.
        form = SeasonForm(games_played=8, points=240.0, recent_games=3, recent_points=90.0)
        assert blended_rate(8.0, form) > 22.0


class TestRecentGamesCountForMore:
    def test_a_player_heating_up_rates_above_his_season_average(self):
        cold_start = SeasonForm(
            games_played=6, points=72.0, recent_games=3, recent_points=18.0
        )
        heating_up = SeasonForm(
            games_played=6, points=72.0, recent_games=3, recent_points=54.0
        )
        # Identical seasons, opposite directions.
        assert actual_rate(heating_up) > actual_rate(cold_start)
        assert blended_rate(10.0, heating_up) > blended_rate(10.0, cold_start)

    def test_without_a_recent_window_the_season_average_stands(self):
        form = SeasonForm(games_played=4, points=48.0)
        assert actual_rate(form) == 12.0


class TestUsageLeadsScoring:
    def test_a_jump_in_targets_lifts_the_rating_before_the_points_do(self):
        # Same points either way -- touchdowns are lumpy. The difference is that
        # one of them is suddenly being thrown at twice as often.
        steady = SeasonForm(
            games_played=6, points=60.0, recent_games=3, recent_points=30.0,
            opportunities=36, recent_opportunities=18,
        )
        breaking_out = SeasonForm(
            games_played=6, points=60.0, recent_games=3, recent_points=30.0,
            opportunities=36, recent_opportunities=36,
        )
        assert usage_multiplier(steady) == 1.0
        assert usage_multiplier(breaking_out) > 1.0
        assert blended_rate(9.0, breaking_out) > blended_rate(9.0, steady)

    def test_usage_is_a_hint_not_a_multiplier(self):
        # Opportunity tripled. The rating must not triple with it.
        tripled = SeasonForm(
            games_played=6, points=60.0, recent_games=3, recent_points=30.0,
            opportunities=36, recent_opportunities=54,
        )
        assert usage_multiplier(tripled) <= 1.25

    def test_a_thin_sample_has_no_opinion(self):
        # Two targets all season says nothing about a change in role.
        barely_used = SeasonForm(
            games_played=6, points=60.0, recent_games=3, recent_points=30.0,
            opportunities=2, recent_opportunities=2,
        )
        assert usage_multiplier(barely_used) == 1.0

    def test_opportunities_count_targets_carries_and_attempts(self):
        assert opportunities_in({TARGETS: 7, CARRIES: 3, PASS_ATTEMPTS: 0}) == 10
        assert opportunities_in({"42": 500}) == 0, "yards are not opportunities"
        assert opportunities_in({}) == 0


class TestTheReportedCases:
    def test_shakir_is_no_longer_invisible(self):
        """73 points in 6 games, 50.2 of them in the last three."""
        shakir = SeasonForm(
            games_played=6, points=73.0, recent_games=3, recent_points=50.2,
            opportunities=32, recent_opportunities=17,
        )
        preseason_per_game = 8.0      # a modest August projection
        rated = blended_rate(preseason_per_game, shakir)

        assert rated > 11.0, "six weeks of 12.2 a game has to count for something"
        assert rated < shakir.recent_points_per_game, (
            "but not as much as assuming his last three games are the new normal"
        )

    def test_a_reliable_kicker_is_not_rated_below_his_production(self):
        """Fairbairn: 64 points in 6 games, top-four at the position."""
        fairbairn = SeasonForm(
            games_played=6, points=64.0, recent_games=3, recent_points=33.0
        )
        # Even from a pessimistic August projection, the rating has to move
        # toward what he is actually doing.
        assert blended_rate(7.0, fairbairn) > 8.5


class TestTheArithmeticIsShowable:
    def test_explain_reports_both_sides_and_the_weighting(self):
        form = SeasonForm(
            games_played=6, points=73.0, recent_games=3, recent_points=50.2,
            opportunities=32, recent_opportunities=17,
        )
        detail = explain(8.0, form)
        assert detail["basis"] == "blended"
        assert detail["projection_per_game"] == 8.0
        assert detail["actual_per_game"] == 12.17
        assert detail["games_played"] == 6
        assert 0 < detail["evidence_weight"] < 1
        assert detail["blended_per_game"] == round(blended_rate(8.0, form), 2)

    def test_explain_says_so_when_there_is_nothing_but_a_projection(self):
        detail = explain(8.0, None)
        assert detail["basis"] == "projection"
        assert detail["games_played"] == 0
