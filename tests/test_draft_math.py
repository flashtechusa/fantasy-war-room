"""Snake / linear draft pick arithmetic.

An off-by-one here silently corrupts the availability model and every
recommendation built on it, so this file is deliberately exhaustive.
"""

from __future__ import annotations

import pytest

from app.engine.draft_math import (
    describe_position,
    next_pick_after,
    overall_pick_for,
    pick_in_round,
    picks_for_slot,
    picks_until_next,
    round_of,
    slot_for_pick,
    upcoming_picks,
)


class TestRounds:
    @pytest.mark.parametrize(
        "pick,teams,expected",
        [(1, 10, 1), (10, 10, 1), (11, 10, 2), (20, 10, 2), (21, 10, 3), (144, 12, 12)],
    )
    def test_round_of(self, pick, teams, expected):
        assert round_of(pick, teams) == expected

    @pytest.mark.parametrize(
        "pick,teams,expected", [(1, 10, 1), (10, 10, 10), (11, 10, 1), (25, 12, 1)]
    )
    def test_pick_in_round(self, pick, teams, expected):
        assert pick_in_round(pick, teams) == expected

    def test_rejects_nonsense(self):
        with pytest.raises(ValueError):
            round_of(0, 10)
        with pytest.raises(ValueError):
            slot_for_pick(5, 0)


class TestSnakeOrder:
    def test_odd_rounds_run_forwards(self):
        assert [slot_for_pick(p, 10) for p in range(1, 11)] == list(range(1, 11))

    def test_even_rounds_run_backwards(self):
        assert [slot_for_pick(p, 10) for p in range(11, 21)] == list(range(10, 0, -1))

    def test_third_round_runs_forwards_again(self):
        assert [slot_for_pick(p, 10) for p in range(21, 31)] == list(range(1, 11))

    def test_linear_never_reverses(self):
        assert [slot_for_pick(p, 10, "LINEAR") for p in range(11, 21)] == list(range(1, 11))

    def test_slot_and_pick_are_inverses(self):
        for teams in (8, 10, 12, 14):
            for pick in range(1, teams * 16 + 1):
                slot = slot_for_pick(pick, teams)
                round_num = round_of(pick, teams)
                assert overall_pick_for(round_num, slot, teams) == pick

    def test_every_pick_in_a_round_is_used_once(self):
        teams = 12
        for round_num in range(1, 17):
            picks = {overall_pick_for(round_num, slot, teams) for slot in range(1, teams + 1)}
            expected = set(range((round_num - 1) * teams + 1, round_num * teams + 1))
            assert picks == expected


class TestTurnTiming:
    def test_first_slot_gets_the_wheel(self):
        # Slot 1 in a 10-team snake: 1, 20, 21, 40, 41, ...
        assert picks_for_slot(1, 10, 5) == [1, 20, 21, 40, 41]

    def test_last_slot_gets_the_turn(self):
        assert picks_for_slot(10, 10, 5) == [10, 11, 30, 31, 50]

    def test_middle_slot(self):
        assert picks_for_slot(5, 10, 4) == [5, 16, 25, 36]

    def test_linear_slot_picks_are_evenly_spaced(self):
        assert picks_for_slot(3, 10, 4, "LINEAR") == [3, 13, 23, 33]

    def test_next_pick_after(self):
        assert next_pick_after(1, 10, 16, 1) == 20
        assert next_pick_after(1, 10, 16, 19) == 20
        assert next_pick_after(1, 10, 16, 20) == 21

    def test_next_pick_is_none_at_the_end(self):
        assert next_pick_after(1, 10, 2, 21) is None

    def test_picks_until_next_counts_intervening_picks(self):
        # From pick 1 (slot 1), the next is 20; picks 2..19 intervene = 18.
        assert picks_until_next(1, 10, 16, 1) == 18
        # Turn slot picks back to back.
        assert picks_until_next(10, 10, 16, 10) == 0

    def test_upcoming_picks_respects_limit(self):
        assert upcoming_picks(1, 10, 16, 1, limit=3) == [1, 20, 21]
        assert upcoming_picks(1, 10, 16, 22, limit=2) == [40, 41]


class TestDescribePosition:
    def test_on_the_clock_for_me(self):
        position = describe_position(current_pick=1, my_slot=1, team_count=10, rounds=16)
        assert position.is_my_pick
        assert position.round_num == 1
        assert position.my_next_pick == 20
        assert position.picks_until_my_next == 18
        assert position.my_upcoming_picks[0] == 1

    def test_someone_else_on_the_clock(self):
        position = describe_position(current_pick=5, my_slot=1, team_count=10, rounds=16)
        assert not position.is_my_pick
        assert position.on_the_clock_slot == 5
        assert position.my_next_pick == 20
        assert position.picks_until_my_next == 15

    def test_turn_slot_back_to_back(self):
        position = describe_position(current_pick=10, my_slot=10, team_count=10, rounds=16)
        assert position.is_my_pick
        assert position.my_next_pick == 11
        assert position.picks_until_my_next == 0

    def test_rounds_remaining_shrinks(self):
        early = describe_position(current_pick=1, my_slot=1, team_count=10, rounds=16)
        late = describe_position(current_pick=151, my_slot=1, team_count=10, rounds=16)
        assert early.rounds_remaining == 16
        assert late.rounds_remaining == 0 or late.rounds_remaining < early.rounds_remaining

    def test_past_the_end_is_clamped(self):
        position = describe_position(current_pick=999, my_slot=1, team_count=10, rounds=2)
        assert not position.is_my_pick
        assert position.my_next_pick is None
