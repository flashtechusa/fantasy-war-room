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
        # Same production; one fills a hole you can see coming. A third, weaker
        # receiver is on the wire as the man you could stream instead -- without
        # one, neither of these is worth anything as cover, because each IS the
        # other's fallback.
        cover = _p(107, "Covers Bye", "WR", 150, bye=11)
        vague = _p(108, "No Cover", "WR", 150, bye=7)
        streamer = _p(109, "Scrap Heap", "WR", 40)
        targets = recommend_waivers(
            roster=_roster(), free_agents=[cover, vague, streamer], shape=_shape(),
            week=1, faab_budget=100, roster_is_full=True,
        )
        assert targets[0].player.name == "Covers Bye", [t.player.name for t in targets]

    def test_an_equal_player_on_the_wire_is_not_insurance(self):
        """The streaming baseline, stated as a rule.

        Two interchangeable receivers on the wire: claiming one gains nothing
        over claiming the other the week you actually need him. This is what
        stopped the app valuing a backup quarterback at his entire season.
        """
        a = _p(110, "One Of Two", "WR", 150)
        b = _p(111, "Other Of Two", "WR", 150)
        targets = recommend_waivers(
            roster=_roster(), free_agents=[a, b], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets == [], [t.player.name for t in targets]


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


class TestTheDropHasToBeUsable:
    """Reported from real use: the wire named a running back sitting on IR as
    the man to cut for a receiver.

    Dropping an IR player frees an IR slot, not the active roster spot an add
    needs -- so the move gains nothing and costs a player. A player on IR also
    cannot be started, so he has no business in the lineup maths either. The
    route passes the ACTIVE roster for both reasons; these pin the engine's half
    of the contract.
    """

    def test_whoever_is_handed_in_is_who_can_be_dropped(self):
        # The engine drops from the roster it is given, so an IR player kept out
        # of that list can never be suggested.
        active = _roster()      # no IR player in it
        target = recommend_waivers(
            roster=active, free_agents=[_p(120, "New WR", "WR", 150)],
            shape=_shape(), week=1, faab_budget=100, roster_is_full=True,
        )[0]
        assert target.drop is not None
        assert target.drop.espn_player_id in {p.espn_player_id for p in active}

    def test_a_starter_is_never_the_drop(self):
        target = recommend_waivers(
            roster=_roster(), free_agents=[_p(121, "New WR", "WR", 150)],
            shape=_shape(), week=1, faab_budget=100, roster_is_full=True,
        )[0]
        assert target.drop.name == "Spare Part", target.drop.name


class TestTheSwapIsStatedPlainly:
    """"I have two receivers on my bench; this would be a replacement for one of
    them." The screen never answered that question -- whether he beats the man
    he would replace -- so the add read as pointless.
    """

    def test_depth_gain_is_the_difference_over_the_dropped_player(self):
        target = recommend_waivers(
            roster=_roster(), free_agents=[_p(122, "Better Than Spare", "WR", 150)],
            shape=_shape(), week=1, faab_budget=100, roster_is_full=True,
        )[0]
        # Spare Part is worth 20; the candidate 150.
        assert target.depth_gain == 130.0
        assert any("bench upgrade" in r.lower() for r in target.reasons), target.reasons

    def test_an_upgrade_does_not_get_the_bench_line(self):
        # When he beats a starter, "straight bench upgrade" is the wrong framing.
        target = recommend_waivers(
            roster=_roster(), free_agents=[_p(123, "Stud WR", "WR", 260)],
            shape=_shape(), week=1, faab_budget=100, roster_is_full=True,
        )[0]
        assert target.season_gain > 0
        assert not any("bench upgrade" in r.lower() for r in target.reasons)


class TestTheInsuranceFloorDoesNotRejectUsefulBackups:
    """The floor exists to keep warm bodies out, not to reject a real backup.

    A player above replacement level is by definition worth a spot if he has to
    play, so any gain counts for him. One below it still has to clear the old
    bar, or "better than an empty slot" makes every free agent insurance.
    """

    def test_an_above_replacement_backup_counts_on_a_modest_gain(self):
        roster = _roster() + [_p(300, "Existing WR3", "WR", 140)]
        candidate = _p(301, "Slightly Better", "WR", 155)
        candidate.vor = 12.0      # above replacement at the position

        targets = recommend_waivers(
            roster=roster, free_agents=[candidate], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets, "a useful backup must not be rejected for a modest edge"
        assert targets[0].insurance_gain > 0

    def test_a_below_replacement_body_still_has_to_clear_the_bar(self):
        junk = _p(302, "Junk WR", "WR", 2)
        junk.vor = -30.0
        targets = recommend_waivers(
            roster=_roster(), free_agents=[junk], shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets == []


class TestPriorityMeansSomething:
    """Reported from real use, and the reason a fixed insurance value still read
    as an instruction: priority was normalised against the best candidate on the
    list, so the top row always scored 100.

    On a quiet wire the least-bad scrap looked exactly like a league-winner, and
    a user took "100 PRIORITY" on a third quarterback at face value -- correctly,
    because that is what the number was saying.
    """

    def test_a_marginal_add_on_a_quiet_wire_is_not_a_hundred(self):
        """The reported case: two interchangeable backups and nothing else.

        Neither beats a starter, and each is the other's fallback, so claiming
        one is worth a few points over claiming the other when you need him.
        That is a marginal move and has to read as one. (A wire where the only
        cover is MUCH better than the next option is a different story, and
        genuinely is a high priority -- see the test below.)
        """
        wire = [_p(140, "Backup A", "WR", 150), _p(141, "Backup B", "WR", 145)]
        for player in wire:
            player.vor = 15.0      # both worth rostering, neither a starter
        targets = recommend_waivers(
            roster=_roster(), free_agents=wire, shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets, "cover is still worth listing"
        assert targets[0].priority < 40, (
            f"a quiet wire must read as quiet, got {targets[0].priority}"
        )

    def test_cover_nobody_else_can_give_you_still_rates_highly(self):
        # Two starters, no third receiver, and one good option against a scrap
        # heap. Losing a starter would really cost you, and he really fixes it.
        wire = [_p(145, "Only Real Option", "WR", 150), _p(146, "Scrap", "WR", 40)]
        targets = recommend_waivers(
            roster=_roster(), free_agents=wire, shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets[0].priority > 60, (
            "depth you cannot get anywhere else is not a marginal move"
        )

    def test_a_real_upgrade_still_reaches_the_top_of_the_scale(self):
        wire = [_p(142, "Clear Upgrade", "WR", 400)]
        targets = recommend_waivers(
            roster=_roster(), free_agents=wire, shape=_shape(), week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert targets[0].priority == 100.0

    def test_a_player_elsewhere_on_the_wire_does_not_move_his_number(self):
        """The point of an absolute scale: his number is about him.

        Note what does NOT hold here -- another player at HIS position changes
        what he is worth, and should, because it changes what you could claim
        instead of him. A running back cannot do that to a receiver.
        """
        alone = recommend_waivers(
            roster=_roster(), free_agents=[_p(143, "Cover Guy", "WR", 150)],
            shape=_shape(), week=1, faab_budget=100, roster_is_full=True,
        )
        crowded = recommend_waivers(
            roster=_roster(),
            free_agents=[_p(143, "Cover Guy", "WR", 150), _p(144, "A Back", "RB", 400)],
            shape=_shape(), week=1, faab_budget=100, roster_is_full=True,
        )
        mine = next(t for t in crowded if t.player.espn_player_id == 143)
        assert alone[0].priority == mine.priority, (
            "the same player, the same week, the same number"
        )


class TestTheDropIsTheManHeReplaces:
    """Reported from real use: adding a defence, the app said "drop Juwan
    Johnson (TE)".

    That leaves two defences, one fewer tight end, and the old defence rotting
    on the bench. The user was going to drop the Jaguars, which is obviously
    right -- and which the app could not suggest, because _droppable refuses to
    touch a starter and the Jaguars were his only defence.
    """

    @staticmethod
    def _shape():
        return LeagueShape.from_slots(
            team_count=10, roster_slots={"WR": 2, "TE": 1, "DST": 1}, bench_slots=3,
        )

    @staticmethod
    def _roster():
        return [
            _p(1, "My WR1", "WR", 210), _p(2, "My WR2", "WR", 190),
            _p(3, "Brock Bowers", "TE", 120),
            _p(4, "Juwan Johnson", "TE", 45),      # only backup tight end
            _p(5, "Jaguars D/ST", "DST", 55),      # only defence: a starter
        ]

    def test_a_better_defence_replaces_the_defence(self):
        wire = [_p(100, "Bengals D/ST", "DST", 90), _p(101, "Spare D/ST", "DST", 30)]
        target = recommend_waivers(
            roster=self._roster(), free_agents=wire, shape=self._shape(), week=7,
            faab_budget=100, roster_is_full=True,
        )[0]
        assert target.drop is not None
        assert target.drop.name == "Jaguars D/ST", (
            f"the man he replaces, not an unrelated bench player: {target.drop.name}"
        )

    def test_the_only_backup_at_another_position_is_kept(self):
        wire = [_p(100, "Bengals D/ST", "DST", 90), _p(101, "Spare D/ST", "DST", 30)]
        target = recommend_waivers(
            roster=self._roster(), free_agents=wire, shape=self._shape(), week=7,
            faab_budget=100, roster_is_full=True,
        )[0]
        assert target.drop.name != "Juwan Johnson", (
            "a backup tight end is worth more than a second defence"
        )

    def test_a_starter_who_is_still_needed_is_not_cut(self):
        # A third receiver does not displace anybody: both incumbents still start.
        wire = [_p(102, "Third WR", "WR", 150)]
        target = recommend_waivers(
            roster=self._roster(), free_agents=wire, shape=self._shape(), week=7,
            faab_budget=100, roster_is_full=True,
        )
        if target:
            assert target[0].drop.name not in {"My WR1", "My WR2"}, (
                "nobody holding a starting slot gets cut for a backup"
            )

    def test_a_protected_player_is_never_the_drop(self):
        roster = self._roster()
        roster[4].vor = 40.0      # an elite defence, above the protection line
        wire = [_p(100, "Marginally Better", "DST", 60), _p(101, "Spare", "DST", 30)]
        targets = recommend_waivers(
            roster=roster, free_agents=wire, shape=self._shape(), week=7,
            faab_budget=100, roster_is_full=True,
        )
        for target in targets:
            assert target.drop is None or target.drop.name != "Jaguars D/ST"
