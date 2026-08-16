"""Positional scarcity and tier structure.

Two questions the board has to answer:

1. **Where are the cliffs?**  Fantasy value is not smooth -- it comes in tiers
   with gaps between them.  We find tiers by looking for unusually large gaps in
   projected points within a position, which is the same thing a human does
   staring at a ranked list, only consistently.

2. **How fast is this tier draining?**  A cliff only matters if you're about to
   fall off it.  Using the availability model we compute the expected number of
   picks until the current tier at a position is (nearly) gone.  "Elite TE tier
   empties in 5 picks" is what turns a static ranking into a draft decision.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

#: A tier break needs a gap this many times the position's typical gap.
TIER_GAP_MULTIPLIER = 1.9
#: ...and at least this many points, so noise near the bottom doesn't shatter.
TIER_MIN_GAP_POINTS = 8.0
MAX_TIERS = 9
#: A tier counts as "gone" once this share of it is off the board.
TIER_DRAIN_THRESHOLD = 0.75
#: How far ahead we're willing to look when timing a cliff.
MAX_LOOKAHEAD_PICKS = 60


@dataclass
class TierAssignment:
    """Tier number (1 = best) per player id, plus the tier boundaries."""

    by_player: dict[int, int] = field(default_factory=dict)
    tier_sizes: dict[int, int] = field(default_factory=dict)
    #: Points drop entering each tier, keyed by the tier being entered.
    tier_drop: dict[int, float] = field(default_factory=dict)

    def tier_of(self, player_id: int) -> int:
        return self.by_player.get(player_id, MAX_TIERS)


def assign_tiers(players: list[tuple[int, float]]) -> TierAssignment:
    """Cluster one position's players into tiers by gap size.

    `players` is [(player_id, projected_points)]; it is sorted here.
    """
    result = TierAssignment()
    ordered = sorted(players, key=lambda p: p[1], reverse=True)
    if not ordered:
        return result
    if len(ordered) == 1:
        result.by_player[ordered[0][0]] = 1
        result.tier_sizes[1] = 1
        return result

    gaps = [ordered[i][1] - ordered[i + 1][1] for i in range(len(ordered) - 1)]
    positive = [g for g in gaps if g > 0]
    typical = statistics.median(positive) if positive else 0.0
    threshold = max(typical * TIER_GAP_MULTIPLIER, TIER_MIN_GAP_POINTS)

    tier = 1
    result.by_player[ordered[0][0]] = tier
    result.tier_sizes[tier] = 1
    for idx in range(1, len(ordered)):
        gap = gaps[idx - 1]
        if gap >= threshold and tier < MAX_TIERS:
            tier += 1
            result.tier_drop[tier] = round(gap, 2)
        result.by_player[ordered[idx][0]] = tier
        result.tier_sizes[tier] = result.tier_sizes.get(tier, 0) + 1
    return result


@dataclass
class PositionScarcity:
    position: str
    #: Undrafted players at this position projected above replacement level.
    starters_left: int
    #: Players remaining in the best available tier at this position.
    tier_players_left: int
    #: Points you lose falling out of that tier.
    cliff_points: float
    #: Expected picks until that tier is ~gone (None = not within the horizon).
    picks_to_cliff: int | None
    #: Value lost at this position between now and our next pick.
    dropoff_to_next_pick: float
    #: 0-100, comparable across positions.
    scarcity_score: float
    #: Expected best-available projection at this position at our next pick.
    expected_best_next: float
    #: Best available projection right now.
    best_available_now: float

    def explain(self) -> str:
        bits = [f"{self.starters_left} startable {self.position}s left"]
        if self.picks_to_cliff is not None and self.cliff_points > 0:
            bits.append(
                f"tier drops {self.cliff_points:.0f} pts in ~{self.picks_to_cliff} picks"
            )
        if self.dropoff_to_next_pick > 0:
            bits.append(f"{self.dropoff_to_next_pick:.0f} pts of value lost by waiting")
        return "; ".join(bits)


def picks_until_tier_drains(
    tier_adps: list[float],
    current_pick: int,
    availability,
    threshold: float = TIER_DRAIN_THRESHOLD,
    max_lookahead: int = MAX_LOOKAHEAD_PICKS,
) -> int | None:
    """Expected picks until `threshold` of a tier is off the board.

    `availability` is an AvailabilityModel; we walk forward pick by pick and
    accumulate the expected number gone (linearity of expectation), which is
    cheap and needs no simulation.
    """
    if not tier_adps:
        return None
    target = max(1.0, len(tier_adps) * threshold)
    for offset in range(1, max_lookahead + 1):
        pick = current_pick + offset
        gone = sum(
            1.0 - availability.probability_available_at(pick, adp) for adp in tier_adps
        )
        if gone >= target:
            return offset
    return None


def normalise_scarcity(raw_by_position: dict[str, float]) -> dict[str, float]:
    """Map raw drop-off numbers onto a 0-100 scale comparable across positions."""
    if not raw_by_position:
        return {}
    values = list(raw_by_position.values())
    lo, hi = min(values), max(values)
    if hi - lo < 1e-6:
        return {pos: 50.0 for pos in raw_by_position}
    return {
        pos: round(100.0 * (value - lo) / (hi - lo), 1)
        for pos, value in raw_by_position.items()
    }
