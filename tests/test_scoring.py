"""League-specific scoring: the same stat line must score differently under
different league rules. That property is the whole reason this layer exists.
"""

from __future__ import annotations

from app.engine.scoring import LeagueScoring

# A 100-catch, 1400-yard, 10-TD receiving season.
WR_LINE = {"53": 100.0, "42": 1400.0, "43": 10.0, "72": 1.0}

# 4500 passing yards, 35 TD, 10 INT, 300 rushing yards, 3 rushing TD.
QB_LINE = {"3": 4500.0, "4": 35.0, "20": 10.0, "24": 300.0, "25": 3.0}


def rules(**overrides) -> list[dict]:
    base = {
        3: 0.04, 4: 4.0, 20: -2.0, 24: 0.1, 25: 6.0,
        42: 0.1, 43: 6.0, 53: 0.0, 72: -2.0,
    }
    base.update({int(k): v for k, v in overrides.items()})
    return [
        {"stat_id": stat_id, "abbrev": "", "label": "", "points": points}
        for stat_id, points in base.items()
    ]


class TestScoringFormats:
    def test_standard_scoring(self):
        scoring = LeagueScoring.from_dicts(rules())
        # 1400*0.1 + 10*6 - 1*2 = 140 + 60 - 2
        assert scoring.score(WR_LINE, "WR") == 198.0

    def test_half_ppr_adds_half_a_point_per_catch(self):
        scoring = LeagueScoring.from_dicts(rules(**{"53": 0.5}))
        assert scoring.score(WR_LINE, "WR") == 248.0

    def test_full_ppr_adds_a_point_per_catch(self):
        scoring = LeagueScoring.from_dicts(rules(**{"53": 1.0}))
        assert scoring.score(WR_LINE, "WR") == 298.0

    def test_ppr_changes_the_answer(self):
        """The point of the whole module: rules change valuations."""
        standard = LeagueScoring.from_dicts(rules())
        ppr = LeagueScoring.from_dicts(rules(**{"53": 1.0}))
        assert ppr.score(WR_LINE, "WR") - standard.score(WR_LINE, "WR") == 100.0

    def test_six_point_passing_touchdowns(self):
        four = LeagueScoring.from_dicts(rules())
        six = LeagueScoring.from_dicts(rules(**{"4": 6.0}))
        assert six.score(QB_LINE, "QB") - four.score(QB_LINE, "QB") == 70.0

    def test_qb_line_under_standard_rules(self):
        scoring = LeagueScoring.from_dicts(rules())
        # 180 + 140 - 20 + 30 + 18
        assert scoring.score(QB_LINE, "QB") == 348.0


class TestEdgeCases:
    def test_unknown_stat_ids_are_ignored(self):
        scoring = LeagueScoring.from_dicts(rules())
        assert scoring.score({"9999": 500.0}, "WR") == 0.0

    def test_empty_stat_line_scores_zero(self):
        assert LeagueScoring.from_dicts(rules()).score({}, "WR") == 0.0

    def test_integer_and_string_stat_keys_agree(self):
        scoring = LeagueScoring.from_dicts(rules(**{"53": 1.0}))
        assert scoring.score({53: 100.0}, "WR") == scoring.score({"53": 100.0}, "WR")

    def test_zero_point_rules_are_dropped(self):
        scoring = LeagueScoring.from_dicts(rules())
        assert all(rule.points != 0 or rule.points_overrides for rule in scoring.rules)

    def test_position_overrides_apply_only_to_that_position(self):
        scoring = LeagueScoring.from_dicts(
            [{"stat_id": 43, "abbrev": "", "label": "", "points": 6.0,
              "points_overrides": {"16": 10.0}}]
        )
        assert scoring.score({"43": 2.0}, "WR") == 12.0
        assert scoring.score({"43": 2.0}, "DST") == 20.0


class TestBreakdown:
    def test_breakdown_sums_to_the_total(self):
        scoring = LeagueScoring.from_dicts(rules(**{"53": 0.5}))
        items = scoring.breakdown(WR_LINE, "WR")
        assert round(sum(item.points for item in items), 2) == scoring.score(WR_LINE, "WR")

    def test_breakdown_is_ordered_by_impact(self):
        scoring = LeagueScoring.from_dicts(rules(**{"53": 0.5}))
        items = scoring.breakdown(WR_LINE, "WR")
        magnitudes = [abs(item.points) for item in items]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_breakdown_skips_zero_contributions(self):
        scoring = LeagueScoring.from_dicts(rules())
        items = scoring.breakdown(WR_LINE, "WR")
        assert all(item.points != 0 for item in items)
        assert not any(item.stat_id == 53 for item in items)  # receptions worth 0


class TestIntrospection:
    def test_format_label_reflects_the_rules(self):
        assert LeagueScoring.from_dicts(rules()).format_label == "Standard"
        assert LeagueScoring.from_dicts(rules(**{"53": 1.0})).format_label == "Full PPR"
        assert LeagueScoring.from_dicts(rules(**{"53": 0.5})).format_label == "0.5 PPR"

    def test_format_label_notes_unusual_passing_td_values(self):
        label = LeagueScoring.from_dicts(rules(**{"4": 6.0})).format_label
        assert "6pt passing TD" in label

    def test_points_per_reception_read_from_the_rules(self):
        assert LeagueScoring.from_dicts(rules(**{"53": 0.5})).points_per_reception == 0.5
