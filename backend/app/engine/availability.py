"""Will he still be there at our next pick?

Model
-----
Treat the pick at which a player comes off the board as a random variable
``T ~ Normal(ADP, sigma)``.  Uncertainty grows with ADP -- the consensus on the
1.01 is tight, the consensus on the 9th-round WR is not -- so
``sigma = SIGMA_BASE + SIGMA_SLOPE * ADP``.

Mid-draft we know more than ADP alone: we know the player is *still on the
board at the current pick*.  So the quantity we actually want is the
conditional survival probability

    P(T >= next_pick | T >= current_pick) = S(next_pick) / S(current_pick)

which correctly says "he's lasted this long, the odds he lasts a bit longer are
better than raw ADP implies".

We also track **market drift**: if the room is reaching relative to ADP, every
remaining player's effective ADP shifts earlier.  Drift is measured from the
picks already made, so a fast-moving draft tightens the model automatically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: sigma = SIGMA_BASE + SIGMA_SLOPE * adp  (in picks)
SIGMA_BASE = 4.0
SIGMA_SLOPE = 0.18
SIGMA_MAX = 45.0

#: Floor for the conditioning denominator, for numerical stability.
MIN_CONDITIONING_PROB = 0.02

#: Drift is damped and capped; a handful of reaches shouldn't swing the board.
DRIFT_DAMPING = 0.6
MAX_DRIFT = 18.0


def sigma_for(adp: float) -> float:
    return min(SIGMA_BASE + SIGMA_SLOPE * max(adp, 0.0), SIGMA_MAX)


def _norm_sf(x: float) -> float:
    """P(Z >= x) for a standard normal."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def survival(pick: float, adp: float, sigma: float | None = None) -> float:
    """P(the player is still on the board when pick `pick` is made)."""
    s = sigma if sigma is not None else sigma_for(adp)
    if s <= 0:
        return 1.0 if pick <= adp else 0.0
    # "Available at pick N" means he wasn't taken at picks 1..N-1.
    return _norm_sf(((pick - 0.5) - adp) / s)


@dataclass
class AvailabilityModel:
    """Availability probabilities for the current draft state."""

    current_pick: int = 1
    next_pick: int | None = None
    #: Positive when the room is drafting *ahead* of ADP (reaching).
    market_drift: float = 0.0

    @property
    def picks_until_next(self) -> int | None:
        if self.next_pick is None:
            return None
        return max(self.next_pick - self.current_pick - 1, 0)

    def effective_adp(self, adp: float | None, fallback_rank: float | None = None) -> float:
        """ADP adjusted for market drift, with a fallback for unranked players."""
        if adp is None or adp <= 0:
            # No market data: assume he goes a bit later than our own ranking.
            adp = (fallback_rank or 250.0) * 1.15
        return max(adp - self.market_drift, 1.0)

    def probability_available_at(
        self, pick: int, adp: float | None, fallback_rank: float | None = None
    ) -> float:
        """P(available at `pick` | still available at the current pick)."""
        eff = self.effective_adp(adp, fallback_rank)
        s = sigma_for(eff)
        if pick <= self.current_pick:
            return 1.0
        now = max(survival(self.current_pick, eff, s), MIN_CONDITIONING_PROB)
        later = survival(pick, eff, s)
        return max(0.0, min(1.0, later / now))

    def probability_available_next(
        self, adp: float | None, fallback_rank: float | None = None
    ) -> float:
        """P(available at our next pick). 1.0 if we have no further picks."""
        if self.next_pick is None:
            return 1.0
        return self.probability_available_at(self.next_pick, adp, fallback_rank)

    def probability_gone_next(
        self, adp: float | None, fallback_rank: float | None = None
    ) -> float:
        return round(1.0 - self.probability_available_next(adp, fallback_rank), 4)


def measure_market_drift(
    drafted: list[tuple[int, float | None]], min_samples: int = 8
) -> float:
    """How far ahead of ADP the room is drafting.

    `drafted` is [(overall_pick, adp)] for picks already made.  Returns a
    damped, capped median of `adp - overall_pick`: positive means players are
    going *earlier* than their ADP (the room is reaching).
    """
    deltas = [adp - pick for pick, adp in drafted if adp and adp > 0]
    if len(deltas) < min_samples:
        return 0.0
    deltas.sort()
    mid = len(deltas) // 2
    median = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2
    return max(-MAX_DRIFT, min(MAX_DRIFT, median * DRIFT_DAMPING))


def expected_best_available(
    candidates: list[tuple[float, float]],
) -> float:
    """Expected value of the best player still on the board at a future pick.

    `candidates` is [(value, probability_available)] and does **not** need to be
    sorted -- we sort descending by value here.  The expectation is

        E[best] = sum_i  v_i * p_i * prod_{j<i} (1 - p_j)

    i.e. player i is the best available exactly when he survives and everyone
    better than him does not.
    """
    if not candidates:
        return 0.0
    ordered = sorted(candidates, key=lambda c: c[0], reverse=True)
    expected = 0.0
    none_better_survived = 1.0
    for value, prob in ordered:
        prob = max(0.0, min(1.0, prob))
        expected += value * prob * none_better_survived
        none_better_survived *= 1.0 - prob
        if none_better_survived < 1e-6:
            break
    return round(expected, 3)


def expected_count_available(probabilities: list[float]) -> float:
    """Expected number of a group still on the board (linearity of expectation)."""
    return round(sum(max(0.0, min(1.0, p)) for p in probabilities), 2)
