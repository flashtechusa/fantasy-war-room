"""Buying a backup, not an upgrade.

Asked for directly: "sometimes I am not looking to replace a starter, it is to
improve my bench in case of a bye or if someone gets hurt."

The wire could not answer that. Value was measured purely as the change to your
optimal STARTING lineup, so a player who does not crack it scored a flat zero
and was dropped from the list -- however good he was, and however thin the bench
behind your starters. Byes are certain and injuries near-certain, so that is a
real hole the wire should be able to fill.
"""

from __future__ import annotations

from app.engine.league_shape import LeagueShape
from app.engine.waivers import (
    bye_weeks_covered,
    insurance_value,
    recommend_waivers,
    upgrade_ids,
)
from app.engine.weekly import WeeklyPlayer


def _p(pid, name, position, points, *, bye=None, team="KC"):
    return WeeklyPlayer(
        espn_player_id=pid, name=name, position=position, pro_team=team,
        week_points=round(points / 14, 2), season_points=points,
        bye_week=bye, week=1, availability="FREEAGENT",
    )


def _shape():
    return LeagueShape.from_slots(
        team_count=10, roster_slots={"QB": 1, "RB": 2, "WR": 2}, bench_slots=3,
    )


def _roster():
    """Two strong receivers starting, and nothing behind them."""
    return [
        _p(1, "My QB", "QB", 300),
        _p(2, "My RB1", "RB", 220), _p(3, "My RB2", "RB", 200),
        _p(4, "My WR1", "WR", 210, bye=7), _p(5, "My WR2", "WR", 190),
        _p(6, "Spare Part", "RB", 20),
    ]


class TestTheBenchCase:
    def test_a_backup_who_changes_nothing_today_is_still_listed(self):
        # Worse than both starting receivers, so the starting lineup does not
        # move at all -- and he is exactly who you want when one of them is out.
        backup = _p(100, "Decent WR3", "WR", 150)
        targets = recommend_waivers(
            roster=_roster(), free_agents=[backup], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert [t.player.name for t in targets] == ["Decent WR3"]
        target = targets[0]
        assert target.season_gain <= 0, "he is not an upgrade, and should not claim to be"
        assert target.insurance_gain > 0
        assert target.verdict in {"insurance", "bye cover"}

    def test_the_card_says_who_he_is_covering_for(self):
        backup = _p(100, "Decent WR3", "WR", 150)
        target = recommend_waivers(
            roster=_roster(), free_agents=[backup], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )[0]
        assert target.insures in {"My WR1", "My WR2"}
        assert any("misses time" in r for r in target.reasons), target.reasons

    def test_a_player_who_helps_nothing_at_all_is_still_refused(self):
        # The list must not become "everyone on the wire". A fifth running back
        # behind two starters and a spare improves neither the lineup nor the
        # bench in any meaningful way.
        junk = _p(101, "Junk WR", "WR", 2)
        targets = recommend_waivers(
            roster=_roster(), free_agents=[junk], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert [t.player.name for t in targets] == []

    def test_an_upgrade_still_outranks_insurance(self):
        upgrade = _p(102, "Better WR", "WR", 260)
        backup = _p(103, "Bench WR", "WR", 150)
        targets = recommend_waivers(
            roster=_roster(), free_agents=[upgrade, backup], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert [t.player.name for t in targets][0] == "Better WR"
        assert upgrade_ids(targets) == {102}, "only the upgrade beats a starter"


class TestByeCover:
    def test_a_bye_at_the_position_is_named(self):
        # My WR1 is off in week 7; this candidate is not.
        candidate = _p(104, "Week 7 Help", "WR", 150, bye=11)
        weeks = bye_weeks_covered(_roster(), _shape(), candidate, week=1)
        assert weeks == [7]

        target = recommend_waivers(
            roster=_roster(), free_agents=[candidate], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )[0]
        assert target.covers_byes == [7]
        assert any("week 7 bye" in r for r in target.reasons), target.reasons

    def test_sharing_the_bye_is_no_help(self):
        # Off the same week as the man he is supposed to cover.
        candidate = _p(105, "Same Bye", "WR", 150, bye=7)
        assert bye_weeks_covered(_roster(), _shape(), candidate, week=1) == []

    def test_a_bye_already_past_is_not_a_reason(self):
        candidate = _p(106, "Too Late", "WR", 150, bye=11)
        assert bye_weeks_covered(_roster(), _shape(), candidate, week=9) == []

    def test_covering_a_bye_outranks_vague_depth(self):
        # Same player, same production; one of them fills a hole you can see
        # coming. The certain one is worth more than the hypothetical one.
        cover = _p(107, "Covers Bye", "WR", 150, bye=11)
        vague = _p(108, "No Cover", "WR", 150, bye=7)
        targets = recommend_waivers(
            roster=_roster(), free_agents=[cover, vague], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets[0].player.name == "Covers Bye", [t.player.name for t in targets]


class TestInsuranceValueItself:
    def test_it_measures_the_lineup_with_the_starter_gone(self):
        roster = _roster()
        gain, covering = insurance_value(roster, _shape(), _p(109, "WR3", "WR", 150), None)
        assert gain > 0 and covering in {"My WR1", "My WR2"}

    def test_a_position_you_are_deep_at_is_worth_less(self):
        thin = _roster()
        deep = _roster() + [_p(200, "Good WR3", "WR", 180)]
        candidate = _p(110, "Another WR", "WR", 150)

        thin_gain, _ = insurance_value(thin, _shape(), candidate, None)
        deep_gain, _ = insurance_value(deep, _shape(), candidate, None)
        assert deep_gain < thin_gain, (
            "insurance is worth less when somebody already covers the position"
        )
