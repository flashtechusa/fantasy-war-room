"""Phase 7 -- Monte Carlo mock drafts.

Opponents do **not** take the next name on a list.  Each simulated manager
samples from the players near the current pick, weighted by how far the pick is
from each player's ADP, and filtered by what that team still needs.  That
reproduces the two things that actually make drafts hard: players go earlier or
later than their ADP, and position runs happen.

From a few thousand of these we can answer the questions that matter before the
draft rather than during it:

* who is realistically available at each of *my* picks
* which positions are safe to wait on and which fall off a cliff
* what a good round-by-round plan looks like *for this league*

All of it is derived from the league's own scoring, roster and ADP data.  No
hard-coded "take a RB in round 1".
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .draft_math import picks_for_slot, slot_for_pick
from .league_shape import LeagueShape
from .roster import RosterPlayer, build_optimal_lineup
from .valuation import ValuationEngine

#: How many near-ADP candidates an opponent considers each pick.
CANDIDATE_WINDOW = 22
#: Spread (in picks) of the sampling kernel around a player's ADP.
ADP_TEMPERATURE = 9.0
#: Opponents leave K/DST until the last rounds, like real drafters.
LATE_POSITIONS = {"K", "DST"}
LATE_ROUND_MARGIN = 2


@dataclass
class SimPlayer:
    player_id: int
    name: str
    position: str
    points: float
    vor: float
    adp: float


@dataclass
class PickOutcome:
    overall_pick: int
    round_num: int
    #: How often each position was taken here by our strategy.
    position_counts: Counter = field(default_factory=Counter)
    #: Expected best available VOR by position at this pick.
    expected_best_vor: dict[str, float] = field(default_factory=dict)
    #: Players available at this pick in >= 50% of sims, best first.
    likely_available: list[tuple[str, str, float]] = field(default_factory=list)
    #: Mean VOR of the player our strategy actually took.
    mean_taken_vor: float = 0.0


@dataclass
class PositionWaitProfile:
    position: str
    #: Expected best-available VOR at each of my picks, in round order.
    by_round: list[float]
    #: Round at which the expected best available drops below 75% of round 1.
    cliff_round: int | None
    verdict: str  # "wait" | "balanced" | "dangerous"
    note: str


@dataclass
class SimulationResult:
    simulations: int
    my_slot: int
    team_count: int
    rounds: int
    picks: list[PickOutcome]
    wait_profiles: list[PositionWaitProfile]
    strategy: list[str]
    mean_roster_points: float
    p10_roster_points: float
    p90_roster_points: float
    mean_starter_points_by_position: dict[str, float]
    seed: int


def _softmax_weights(distances: list[float], temperature: float) -> list[float]:
    """Gaussian kernel on |pick - ADP|, normalised."""
    weights = [math.exp(-0.5 * (d / temperature) ** 2) for d in distances]
    total = sum(weights)
    if total <= 0:
        return [1.0 / len(weights)] * len(weights)
    return [w / total for w in weights]


class DraftSimulator:
    def __init__(
        self,
        engine: ValuationEngine,
        my_slot: int,
        rounds: int | None = None,
        seed: int = 12345,
    ) -> None:
        self.engine = engine
        self.shape: LeagueShape = engine.shape
        self.team_count = self.shape.team_count
        self.rounds = rounds or self.shape.draft_rounds
        self.my_slot = max(1, min(my_slot, self.team_count))
        self.seed = seed

        pool: list[SimPlayer] = []
        for player in engine.players:
            pid = player.espn_player_id
            points = engine.points_for(pid)
            adp = player.adp or (engine._our_rank.get(pid, 300) * 1.15)  # noqa: SLF001
            pool.append(
                SimPlayer(
                    player_id=pid,
                    name=player.name,
                    position=player.position,
                    points=points,
                    vor=engine.replacement.vor(player.position, points),
                    adp=float(adp),
                )
            )
        # Only the part of the pool that can plausibly be drafted.
        pool.sort(key=lambda p: p.adp)
        self.pool = pool[: max(self.team_count * self.rounds * 2, 200)]
        self.by_id = {p.player_id: p for p in self.pool}

        #: Aggregate caps on how many of a position one team will carry.
        self.position_caps = self._position_caps()

    def _position_caps(self) -> dict[str, int]:
        caps: dict[str, int] = {}
        bench = self.shape.bench_slots
        for position in self.shape.positions_in_play:
            starters = self.shape.starters_at(position)
            flex = self.shape.flex_slots_for(position)
            if position in LATE_POSITIONS:
                caps[position] = max(starters, 1)
            elif position in {"QB", "TE"}:
                caps[position] = starters + flex + (1 if bench >= 4 else 0)
            else:
                caps[position] = starters + flex + max(bench - 2, 1)
        return caps

    # -- one simulated draft ----------------------------------------------

    def _run_one(
        self, rng: random.Random
    ) -> tuple[list[SimPlayer], dict[int, tuple[list[SimPlayer], dict[str, float]]]]:
        taken: set[int] = set()
        rosters: dict[int, list[SimPlayer]] = defaultdict(list)
        my_picks_set = set(picks_for_slot(self.my_slot, self.team_count, self.rounds,
                                          self.shape.draft_type))
        availability_at_my_picks: dict[int, tuple[list[SimPlayer], dict[str, float]]] = {}
        my_selections: list[SimPlayer] = []

        total_picks = self.team_count * self.rounds
        cursor = 0  # index into self.pool, advanced past exhausted players

        for overall in range(1, total_picks + 1):
            round_num = (overall - 1) // self.team_count + 1
            slot = slot_for_pick(overall, self.team_count, self.shape.draft_type)
            rounds_left = self.rounds - round_num + 1

            while cursor < len(self.pool) and self.pool[cursor].player_id in taken:
                cursor += 1

            if overall in my_picks_set:
                available = [p for p in self.pool[cursor:] if p.player_id not in taken]
                # Snapshot what's on the board: the names most likely to still be
                # here (by ADP), and the genuine best VOR at each position -- not
                # merely the first one encountered, which ADP order would give.
                best_vor: dict[str, float] = {}
                for player in available:
                    current = best_vor.get(player.position)
                    if current is None or player.vor > current:
                        best_vor[player.position] = player.vor
                availability_at_my_picks[overall] = (available[:25], best_vor)
                choice = self._my_choice(available, rosters[slot], rounds_left)
            else:
                choice = self._opponent_choice(
                    taken, cursor, rosters[slot], rounds_left, overall, rng
                )

            if choice is None:
                continue
            taken.add(choice.player_id)
            rosters[slot].append(choice)
            if overall in my_picks_set:
                my_selections.append(choice)

        return my_selections, availability_at_my_picks

    def _needs_position(
        self, roster: list[SimPlayer], position: str, rounds_left: int
    ) -> bool:
        count = sum(1 for p in roster if p.position == position)
        if count >= self.position_caps.get(position, 3):
            return False
        if position in LATE_POSITIONS:
            starters = self.shape.starters_at(position)
            if count >= starters:
                return False
            # Only once the end of the draft is in sight.
            return rounds_left <= LATE_ROUND_MARGIN
        return True

    def _opponent_choice(
        self,
        taken: set[int],
        cursor: int,
        roster: list[SimPlayer],
        rounds_left: int,
        overall: int,
        rng: random.Random,
    ) -> SimPlayer | None:
        candidates: list[SimPlayer] = []
        for player in self.pool[cursor:]:
            if player.player_id in taken:
                continue
            if not self._needs_position(roster, player.position, rounds_left):
                continue
            candidates.append(player)
            if len(candidates) >= CANDIDATE_WINDOW:
                break
        if not candidates:
            for player in self.pool[cursor:]:
                if player.player_id not in taken:
                    return player
            return None

        distances = [abs(p.adp - overall) for p in candidates]
        weights = _softmax_weights(distances, ADP_TEMPERATURE)
        return rng.choices(candidates, weights=weights, k=1)[0]

    def _my_choice(
        self, available: list[SimPlayer], roster: list[SimPlayer], rounds_left: int
    ) -> SimPlayer | None:
        """Our in-sim strategy: best VOR, weighted by lineup marginal value.

        Kept deliberately fast -- it is called millions of times.  It mirrors
        the live engine's priorities (value first, need as a tiebreak, K/DST
        only at the end) without rebuilding the whole board.
        """
        best: SimPlayer | None = None
        best_score = -1e9
        counts = Counter(p.position for p in roster)

        for player in available[:45]:
            position = player.position
            cap = self.position_caps.get(position, 3)
            if counts[position] >= cap:
                continue
            if position in LATE_POSITIONS and rounds_left > LATE_ROUND_MARGIN:
                continue

            starters = self.shape.starters_at(position) + self.shape.flex_slots_for(position)
            unfilled = max(starters - counts[position], 0)
            # A starting slot we still have to fill is worth real points; a
            # bench upgrade is worth much less.
            need_bonus = 1.0 + (0.18 if unfilled > 0 else 0.0)
            # Forced to fill a slot before the draft ends.
            if unfilled > 0 and rounds_left <= unfilled + 1:
                need_bonus += 0.9

            score = player.vor * need_bonus
            if score > best_score:
                best_score = score
                best = player

        if best is None and available:
            return available[0]
        return best

    # -- aggregation -------------------------------------------------------

    def run(self, simulations: int = 1000) -> SimulationResult:
        rng = random.Random(self.seed)
        my_picks = picks_for_slot(
            self.my_slot, self.team_count, self.rounds, self.shape.draft_type
        )

        position_counts: dict[int, Counter] = {p: Counter() for p in my_picks}
        taken_vor: dict[int, list[float]] = {p: [] for p in my_picks}
        best_vor_by_pos: dict[int, dict[str, list[float]]] = {
            p: defaultdict(list) for p in my_picks
        }
        available_counts: dict[int, Counter] = {p: Counter() for p in my_picks}
        roster_totals: list[float] = []
        starter_points: dict[str, list[float]] = defaultdict(list)

        for _ in range(max(simulations, 1)):
            selections, availability = self._run_one(rng)

            for pick, (top_available, best_vor) in availability.items():
                for position, vor in best_vor.items():
                    best_vor_by_pos[pick][position].append(vor)
                for player in top_available:
                    available_counts[pick][(player.name, player.position)] += 1

            for pick, player in zip(my_picks, selections):
                position_counts[pick][player.position] += 1
                taken_vor[pick].append(player.vor)

            roster = [
                RosterPlayer(
                    espn_player_id=p.player_id,
                    name=p.name,
                    position=p.position,
                    projected_points=p.points,
                    vor=p.vor,
                )
                for p in selections
            ]
            lineup = build_optimal_lineup(roster, self.shape)
            roster_totals.append(lineup.total_points)
            for assignment in lineup.starters:
                if assignment.player:
                    starter_points[assignment.player.position].append(
                        assignment.player.projected_points
                    )

        picks: list[PickOutcome] = []
        for pick in my_picks:
            n = max(sum(position_counts[pick].values()), 1)
            likely = [
                (name, position, round(count / max(simulations, 1), 3))
                for (name, position), count in available_counts[pick].most_common(12)
                if count / max(simulations, 1) >= 0.5
            ]
            picks.append(
                PickOutcome(
                    overall_pick=pick,
                    round_num=(pick - 1) // self.team_count + 1,
                    position_counts=position_counts[pick],
                    expected_best_vor={
                        pos: round(sum(values) / len(values), 1)
                        for pos, values in best_vor_by_pos[pick].items()
                        if values
                    },
                    likely_available=likely,
                    mean_taken_vor=round(
                        sum(taken_vor[pick]) / max(len(taken_vor[pick]), 1), 1
                    ),
                )
            )
            picks[-1].position_counts = Counter(
                {pos: round(count / n, 3) for pos, count in position_counts[pick].items()}
            )

        wait_profiles = self._wait_profiles(picks)
        strategy = self._strategy_summary(picks, wait_profiles)

        roster_totals.sort()
        mean_total = sum(roster_totals) / max(len(roster_totals), 1)

        return SimulationResult(
            simulations=simulations,
            my_slot=self.my_slot,
            team_count=self.team_count,
            rounds=self.rounds,
            picks=picks,
            wait_profiles=wait_profiles,
            strategy=strategy,
            mean_roster_points=round(mean_total, 1),
            p10_roster_points=round(_percentile(roster_totals, 0.10), 1),
            p90_roster_points=round(_percentile(roster_totals, 0.90), 1),
            mean_starter_points_by_position={
                pos: round(sum(v) / len(v), 1) for pos, v in starter_points.items() if v
            },
            seed=self.seed,
        )

    def _wait_profiles(self, picks: list[PickOutcome]) -> list[PositionWaitProfile]:
        profiles: list[PositionWaitProfile] = []
        for position in self.shape.positions_in_play:
            # Kickers and defenses are streamed, not planned around -- a "when
            # should I take a K" curve is noise, so they get no profile.
            if position in LATE_POSITIONS:
                continue
            series = [pick.expected_best_vor.get(position, 0.0) for pick in picks]
            if not series or max(series) <= 1.0:
                continue
            opening = series[0]
            cliff_round: int | None = None
            for idx, value in enumerate(series):
                if opening > 0 and value < opening * 0.75:
                    cliff_round = picks[idx].round_num
                    break

            if cliff_round is None:
                verdict = "wait"
                note = (
                    f"The best available {position} barely degrades across your picks -- "
                    "there is no urgency here."
                )
            elif cliff_round <= 3:
                verdict = "dangerous"
                note = (
                    f"Expected best available {position} falls off by round {cliff_round}. "
                    "Waiting costs you real points."
                )
            else:
                verdict = "balanced"
                note = (
                    f"{position} holds value through round {cliff_round - 1}, then thins out."
                )

            profiles.append(
                PositionWaitProfile(
                    position=position,
                    by_round=[round(v, 1) for v in series],
                    cliff_round=cliff_round,
                    verdict=verdict,
                    note=note,
                )
            )
        return profiles

    def _strategy_summary(
        self, picks: list[PickOutcome], profiles: list[PositionWaitProfile]
    ) -> list[str]:
        """Round-by-round plan, read straight out of the simulations."""
        lines: list[str] = []
        for pick in picks[: min(len(picks), 8)]:
            ranked = sorted(pick.position_counts.items(), key=lambda kv: kv[1], reverse=True)
            ranked = [(pos, share) for pos, share in ranked if share >= 0.12]
            if not ranked:
                continue
            if len(ranked) == 1:
                plan = f"{ranked[0][0]} ({ranked[0][1]:.0%})"
            else:
                primary = "/".join(pos for pos, _ in ranked[:2])
                plan = f"{primary} ({ranked[0][1]:.0%} / {ranked[1][1]:.0%})"
            lines.append(f"Round {pick.round_num} (pick {pick.overall_pick}): prioritise {plan}")

        # Sweet spots: the last round a position still returns most of its value.
        for profile in profiles:
            if profile.position in LATE_POSITIONS:
                continue
            rounds_taken = [
                pick.round_num
                for pick in picks
                if pick.position_counts.get(profile.position, 0) >= 0.25
            ]
            if rounds_taken:
                lo, hi = min(rounds_taken), max(rounds_taken)
                window = f"round {lo}" if lo == hi else f"rounds {lo}-{hi}"
                lines.append(f"{profile.position} sweet spot: {window}")
            elif profile.verdict == "wait":
                lines.append(
                    f"{profile.position}: safe to leave until late -- the position holds value"
                )
        return lines


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(int(q * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[idx]
