"""Snake / linear draft pick arithmetic.

Small, pure, and heavily tested -- everything about "when do I pick again?"
flows from here, and an off-by-one would quietly poison the availability model
and every recommendation built on it.

Conventions: overall picks and rounds are **1-indexed**, draft slots are
1..team_count where slot 1 makes the first overall pick.
"""

from __future__ import annotations

from dataclasses import dataclass

SNAKE = "SNAKE"
LINEAR = "LINEAR"


def is_snake(draft_type: str | None) -> bool:
    """ESPN reports SNAKE, LINEAR, AUCTION, OFFLINE. Everything but LINEAR snakes."""
    return (draft_type or SNAKE).upper() != LINEAR


def round_of(overall_pick: int, team_count: int) -> int:
    _validate(overall_pick, team_count)
    return (overall_pick - 1) // team_count + 1


def pick_in_round(overall_pick: int, team_count: int) -> int:
    """Position within the round (1..team_count), in draft order."""
    _validate(overall_pick, team_count)
    return (overall_pick - 1) % team_count + 1


def slot_for_pick(overall_pick: int, team_count: int, draft_type: str = SNAKE) -> int:
    """Which draft slot owns this overall pick."""
    _validate(overall_pick, team_count)
    index = (overall_pick - 1) % team_count
    round_num = round_of(overall_pick, team_count)
    if is_snake(draft_type) and round_num % 2 == 0:
        return team_count - index
    return index + 1


def overall_pick_for(
    round_num: int, slot: int, team_count: int, draft_type: str = SNAKE
) -> int:
    """The overall pick number a slot owns in a given round."""
    if round_num < 1:
        raise ValueError("round_num must be >= 1")
    if not 1 <= slot <= team_count:
        raise ValueError(f"slot must be within 1..{team_count}")
    if is_snake(draft_type) and round_num % 2 == 0:
        return (round_num - 1) * team_count + (team_count - slot + 1)
    return (round_num - 1) * team_count + slot


def picks_for_slot(
    slot: int, team_count: int, rounds: int, draft_type: str = SNAKE
) -> list[int]:
    """Every overall pick a slot owns, in order."""
    return [
        overall_pick_for(r, slot, team_count, draft_type) for r in range(1, max(rounds, 0) + 1)
    ]


def upcoming_picks(
    slot: int,
    team_count: int,
    rounds: int,
    current_pick: int,
    draft_type: str = SNAKE,
    limit: int | None = None,
) -> list[int]:
    """A slot's remaining picks at or after `current_pick`."""
    remaining = [p for p in picks_for_slot(slot, team_count, rounds, draft_type) if p >= current_pick]
    return remaining[:limit] if limit else remaining


def next_pick_after(
    slot: int,
    team_count: int,
    rounds: int,
    current_pick: int,
    draft_type: str = SNAKE,
) -> int | None:
    """The slot's next pick strictly after `current_pick`. None if the draft is over."""
    for pick in picks_for_slot(slot, team_count, rounds, draft_type):
        if pick > current_pick:
            return pick
    return None


def picks_until_next(
    slot: int,
    team_count: int,
    rounds: int,
    current_pick: int,
    draft_type: str = SNAKE,
) -> int | None:
    """How many other picks happen between now and our next selection.

    If it is currently our turn, this is the gap to the pick *after* this one --
    exactly the window a player has to survive for us to get him again.
    """
    nxt = next_pick_after(slot, team_count, rounds, current_pick, draft_type)
    if nxt is None:
        return None
    return nxt - current_pick - 1


@dataclass(frozen=True)
class DraftPosition:
    """Everything about where a draft currently stands, from one slot's view."""

    current_pick: int
    round_num: int
    pick_in_round: int
    on_the_clock_slot: int
    my_slot: int
    is_my_pick: bool
    my_next_pick: int | None
    picks_until_my_next: int | None
    my_upcoming_picks: list[int]
    rounds: int
    team_count: int
    draft_type: str

    @property
    def rounds_remaining(self) -> int:
        return max(self.rounds - self.round_num + 1, 0)


def describe_position(
    current_pick: int,
    my_slot: int,
    team_count: int,
    rounds: int,
    draft_type: str = SNAKE,
    upcoming_limit: int = 6,
) -> DraftPosition:
    """Snapshot of the draft clock relative to my slot."""
    total_picks = team_count * rounds
    current_pick = max(1, min(current_pick, total_picks + 1))
    over = current_pick > total_picks

    on_clock = slot_for_pick(min(current_pick, total_picks), team_count, draft_type)
    is_mine = (not over) and on_clock == my_slot

    nxt = next_pick_after(my_slot, team_count, rounds, current_pick - 1, draft_type)
    if is_mine:
        # We're on the clock; "next" means the pick after this one.
        following = next_pick_after(my_slot, team_count, rounds, current_pick, draft_type)
    else:
        following = nxt

    gap = None if following is None else following - current_pick - (1 if is_mine else 0)

    return DraftPosition(
        current_pick=current_pick,
        round_num=round_of(min(current_pick, total_picks), team_count),
        pick_in_round=pick_in_round(min(current_pick, total_picks), team_count),
        on_the_clock_slot=on_clock,
        my_slot=my_slot,
        is_my_pick=is_mine,
        my_next_pick=following,
        picks_until_my_next=gap,
        my_upcoming_picks=upcoming_picks(
            my_slot, team_count, rounds, current_pick, draft_type, limit=upcoming_limit
        ),
        rounds=rounds,
        team_count=team_count,
        draft_type=(draft_type or SNAKE).upper(),
    )


def _validate(overall_pick: int, team_count: int) -> None:
    if team_count < 1:
        raise ValueError("team_count must be >= 1")
    if overall_pick < 1:
        raise ValueError("overall_pick must be >= 1")
