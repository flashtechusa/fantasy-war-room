"""Replacement level and Value Over Replacement (VOR).

VOR is the backbone of the board: a player is worth what he produces *above the
guy you could have had for free at the same position*.  That baseline is not a
constant -- it falls out of the league's own shape.

How the baseline is derived
---------------------------
1. **Dedicated starters.**  `team_count x starting slots` at each position are
   guaranteed to be rostered.
2. **FLEX.**  The flex slots are filled by whoever is actually best among the
   flex-eligible leftovers, so we pool the players beyond each position's
   dedicated demand, sort by projected points, and take the top
   `team_count x flex slots`.  In a PPR league that pulls mostly WRs; in a
   TD-heavy league it pulls more RBs.  We let the projections decide.
3. **Realistic bench ownership.**  Benches are not filled evenly.  Nobody
   carries four kickers.  We allocate the league's bench spots greedily by
   provisional VOR, subject to per-position carry caps that reflect how managers
   actually build benches.
4. **Fixed point.**  Steps 2-3 need VOR, and VOR needs the baseline, so we
   iterate until the baseline stops moving (a handful of passes, always).

The replacement *value* is a 3-player smoothed average around the baseline rank
rather than one player's projection, so a single noisy projection can't move a
whole position's rankings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..espn.constants import FANTASY_POSITIONS
from .league_shape import LeagueShape

#: How many players at a position a league's benches will realistically absorb,
#: as a multiple of team_count, on top of the starting requirement.
#: Managers hoard RB/WR upside and stream K/DST.
BENCH_CARRY_CAP = {
    "QB": 0.85,
    "RB": 3.20,
    "WR": 3.20,
    "TE": 0.85,
    "K": 0.10,
    "DST": 0.20,
}

#: Smoothing window (players) around the baseline rank.
SMOOTHING_WINDOW = 3

MAX_ITERATIONS = 8


@dataclass
class PositionBaseline:
    position: str
    #: How many players at this position the league absorbs.
    demand: int
    #: Dedicated starters component.
    starter_demand: int
    #: Share of the flex slots this position wins.
    flex_demand: int
    #: Bench spots this position absorbs.
    bench_demand: int
    #: Projected points of the replacement-level player.
    replacement_points: float
    #: How many players at this position exist in the pool.
    pool_size: int = 0

    def explain(self) -> str:
        bits = [f"{self.starter_demand} starters"]
        if self.flex_demand:
            bits.append(f"{self.flex_demand} via FLEX")
        if self.bench_demand:
            bits.append(f"{self.bench_demand} on benches")
        return (
            f"{self.position}: {' + '.join(bits)} = {self.demand} rostered, "
            f"replacement {self.replacement_points:.1f} pts"
        )


@dataclass
class ReplacementLevels:
    positions: dict[str, PositionBaseline] = field(default_factory=dict)
    #: Best flex-eligible player who would go *undrafted* -- the same "first guy
    #: you could have for free" definition used for every other position.
    flex_replacement_points: float = 0.0
    #: Projection of the marginal FLEX *starter* -- what it takes to start in the
    #: flex, which is a much higher bar than replacement.
    flex_starter_bar: float = 0.0
    flex_demand_total: int = 0
    iterations: int = 0

    def replacement_for(self, position: str) -> float:
        baseline = self.positions.get(position)
        return baseline.replacement_points if baseline else 0.0

    def vor(self, position: str, points: float) -> float:
        return round(points - self.replacement_for(position), 2)

    def as_dict(self) -> dict:
        return {
            "positions": {
                pos: {
                    "position": b.position,
                    "demand": b.demand,
                    "starter_demand": b.starter_demand,
                    "flex_demand": b.flex_demand,
                    "bench_demand": b.bench_demand,
                    "replacement_points": round(b.replacement_points, 2),
                    "pool_size": b.pool_size,
                    "explanation": b.explain(),
                }
                for pos, b in self.positions.items()
            },
            "flex_replacement_points": round(self.flex_replacement_points, 2),
            "flex_starter_bar": round(self.flex_starter_bar, 2),
            "flex_demand_total": self.flex_demand_total,
            "iterations": self.iterations,
        }


def _smoothed_value(sorted_points: list[float], rank_index: int) -> float:
    """Average projection around a 0-based rank index, clamped to the pool."""
    if not sorted_points:
        return 0.0
    half = SMOOTHING_WINDOW // 2
    lo = max(0, rank_index - half)
    hi = min(len(sorted_points), rank_index + half + 1)
    if lo >= hi:
        return sorted_points[min(rank_index, len(sorted_points) - 1)]
    window = sorted_points[lo:hi]
    return sum(window) / len(window)


def compute_replacement_levels(
    players_by_position: dict[str, list[float]],
    shape: LeagueShape,
) -> ReplacementLevels:
    """Derive each position's replacement level from the league's own shape.

    `players_by_position` maps position -> projected points, each list sorted
    descending.  Only positions the league actually starts are baselined.
    """
    positions = [p for p in shape.positions_in_play if p in FANTASY_POSITIONS]
    points = {
        pos: sorted(players_by_position.get(pos, []), reverse=True) for pos in positions
    }

    starter_demand = {pos: shape.league_starter_demand(pos) for pos in positions}
    flex_demand = {pos: 0 for pos in positions}
    bench_demand = {pos: 0 for pos in positions}

    total_flex = shape.flex_slot_count * shape.team_count
    total_bench = shape.bench_slots * shape.team_count

    result = ReplacementLevels()
    previous: dict[str, int] | None = None

    for iteration in range(1, MAX_ITERATIONS + 1):
        # -- step 2: who actually wins the flex slots -----------------------
        flex_demand = {pos: 0 for pos in positions}
        if total_flex > 0:
            candidates: list[tuple[float, str]] = []
            for pos in positions:
                if not shape.flex_eligible(pos):
                    continue
                for value in points[pos][starter_demand[pos] :]:
                    candidates.append((value, pos))
            candidates.sort(reverse=True)
            for value, pos in candidates[:total_flex]:
                flex_demand[pos] += 1

        # -- step 3: bench allocation by provisional VOR --------------------
        provisional_baseline = {
            pos: starter_demand[pos] + flex_demand[pos] for pos in positions
        }
        provisional_repl = {
            pos: _smoothed_value(points[pos], provisional_baseline[pos]) for pos in positions
        }

        bench_demand = {pos: 0 for pos in positions}
        if total_bench > 0:
            caps = {
                pos: int(round(BENCH_CARRY_CAP.get(pos, 1.0) * shape.team_count))
                for pos in positions
            }
            candidates = []
            for pos in positions:
                for value in points[pos][provisional_baseline[pos] :]:
                    candidates.append((value - provisional_repl[pos], pos))
            candidates.sort(reverse=True)
            filled = 0
            for _vor, pos in candidates:
                if filled >= total_bench:
                    break
                if bench_demand[pos] >= caps[pos]:
                    continue
                bench_demand[pos] += 1
                filled += 1

        demand = {
            pos: starter_demand[pos] + flex_demand[pos] + bench_demand[pos] for pos in positions
        }
        if previous == demand:
            result.iterations = iteration
            break
        previous = demand
        result.iterations = iteration

    # -- finalise -----------------------------------------------------------
    for pos in positions:
        d = starter_demand[pos] + flex_demand[pos] + bench_demand[pos]
        result.positions[pos] = PositionBaseline(
            position=pos,
            demand=d,
            starter_demand=starter_demand[pos],
            flex_demand=flex_demand[pos],
            bench_demand=bench_demand[pos],
            replacement_points=round(_smoothed_value(points[pos], d), 2),
            pool_size=len(points[pos]),
        )

    # -- the FLEX baselines -------------------------------------------------
    # Two different, both useful, numbers:
    #   * starter bar   -- the marginal player who actually starts in the flex
    #   * replacement   -- the best flex body who goes undrafted entirely
    result.flex_demand_total = total_flex
    flex_positions = [p for p in positions if shape.flex_eligible(p)]
    if not flex_positions:
        # No flex slot in this league: the "flex" bar is still a useful number,
        # so report it against the positions that would fill one elsewhere.
        flex_positions = [p for p in positions if p in {"RB", "WR", "TE"}]

    if total_flex > 0 and flex_positions:
        starter_pool: list[float] = []
        for pos in flex_positions:
            starter_pool.extend(points[pos][starter_demand[pos] :])
        starter_pool.sort(reverse=True)
        result.flex_starter_bar = round(_smoothed_value(starter_pool, total_flex), 2)
    elif flex_positions:
        result.flex_starter_bar = round(
            max(result.positions[p].replacement_points for p in flex_positions), 2
        )

    if flex_positions:
        undrafted_pool: list[float] = []
        for pos in flex_positions:
            undrafted_pool.extend(points[pos][result.positions[pos].demand :])
        undrafted_pool.sort(reverse=True)
        result.flex_replacement_points = round(_smoothed_value(undrafted_pool, 0), 2)

    return result
