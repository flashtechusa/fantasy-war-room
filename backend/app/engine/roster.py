"""Roster construction: lineups, needs, marginal value, bye conflicts.

The important idea here is that "roster need" is **not** a checklist.  Counting
empty slots and then reaching for the best body at an empty position is how you
end up with the QB12 in round 6.  Instead we measure need the way it actually
costs you points: *marginal value* -- how much would my optimal starting lineup
improve if I added the best available player at each position?

That number is naturally self-limiting.  If you have no TE but the best
available TE is barely above replacement, the marginal value is small and the
engine keeps taking better players elsewhere.  When it's your last chance to
fill a starting slot, the marginal value spikes on its own.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .league_shape import LeagueShape


@dataclass
class RosterPlayer:
    espn_player_id: int
    name: str
    position: str
    projected_points: float
    bye_week: int | None = None
    pro_team: str = ""
    vor: float = 0.0
    injury_status: str = "ACTIVE"


@dataclass
class LineupSlotAssignment:
    slot: str
    player: RosterPlayer | None


@dataclass
class OptimalLineup:
    starters: list[LineupSlotAssignment] = field(default_factory=list)
    bench: list[RosterPlayer] = field(default_factory=list)
    total_points: float = 0.0
    empty_slots: list[str] = field(default_factory=list)

    @property
    def weekly_points(self) -> float:
        """Projected points per game from the starting lineup."""
        return round(self.total_points / 17.0, 2)


def build_optimal_lineup(players: list[RosterPlayer], shape: LeagueShape) -> OptimalLineup:
    """Fill the starting lineup to maximise projected points.

    Slots are filled most-restrictive-first (dedicated positions, then FLEX,
    then superflex).  Because slot eligibility is nested that way, greedy is
    optimal here -- no search needed.
    """
    remaining = sorted(players, key=lambda p: p.projected_points, reverse=True)
    used: set[int] = set()
    lineup = OptimalLineup()

    def take(eligible: tuple[str, ...] | list[str]) -> RosterPlayer | None:
        for player in remaining:
            if id(player) in used:
                continue
            if player.position in eligible:
                used.add(id(player))
                return player
        return None

    for position in sorted(shape.dedicated):
        for _ in range(shape.dedicated[position]):
            player = take((position,))
            lineup.starters.append(LineupSlotAssignment(slot=position, player=player))
            if player is None:
                lineup.empty_slots.append(position)

    # Narrower flex slots first (RB/WR before FLEX before superflex).
    for flex in sorted(shape.flex, key=lambda f: len(f.eligible)):
        for _ in range(flex.count):
            player = take(flex.eligible)
            lineup.starters.append(LineupSlotAssignment(slot=flex.label, player=player))
            if player is None:
                lineup.empty_slots.append(flex.label)

    lineup.bench = [p for p in remaining if id(p) not in used]
    lineup.total_points = round(
        sum(s.player.projected_points for s in lineup.starters if s.player), 2
    )
    return lineup


@dataclass
class PositionNeed:
    position: str
    rostered: int
    starters_required: int
    starters_filled: int
    #: Points the optimal lineup gains from the best available player here.
    marginal_value: float
    #: 0-1 need, derived from marginal value (not from empty-slot counting).
    need_score: float
    #: True when we can no longer fill this starting slot if we keep waiting.
    urgent: bool
    note: str = ""


def positional_needs(
    roster: list[RosterPlayer],
    shape: LeagueShape,
    best_available: dict[str, float],
    replacement_points: dict[str, float] | None = None,
    rounds_remaining: int | None = None,
) -> dict[str, PositionNeed]:
    """Marginal value of adding one more player at each position.

    Measured **against replacement**, not against an empty slot.  The naive
    version -- lineup points with the player minus lineup points without -- makes
    every empty slot worth the player's entire projection, which early in a
    draft always crowns the highest-scoring position (QB) as the biggest need.
    Comparing to a replacement-level body at the same position instead answers
    the question that matters: how much better is this than what I can get for
    free in three rounds' time?
    """
    base_lineup = build_optimal_lineup(roster, shape)

    counts: dict[str, int] = defaultdict(int)
    for player in roster:
        counts[player.position] += 1

    filled: dict[str, int] = defaultdict(int)
    for assignment in base_lineup.starters:
        if assignment.player is not None:
            filled[assignment.player.position] += 1

    replacement_points = replacement_points or {}

    def lineup_with(position: str, points: float) -> float:
        hypothetical = roster + [
            RosterPlayer(
                espn_player_id=-1,
                name=f"Hypothetical {position}",
                position=position,
                projected_points=points,
            )
        ]
        return build_optimal_lineup(hypothetical, shape).total_points

    raw: dict[str, float] = {}
    for position in shape.positions_in_play:
        points = best_available.get(position)
        if points is None:
            raw[position] = 0.0
            continue
        baseline = lineup_with(position, replacement_points.get(position, 0.0))
        raw[position] = round(max(lineup_with(position, points) - baseline, 0.0), 2)

    peak = max(raw.values()) if raw else 0.0
    needs: dict[str, PositionNeed] = {}
    for position in shape.positions_in_play:
        required = shape.starters_at(position)
        slots_short = max(required - filled.get(position, 0), 0)
        urgent = bool(
            rounds_remaining is not None and slots_short > 0 and rounds_remaining <= slots_short + 1
        )
        need_score = round(raw[position] / peak, 3) if peak > 0 else 0.0

        if slots_short > 0:
            note = f"{slots_short} starting {position} slot(s) still open"
        elif raw[position] > 0:
            note = f"An upgrade here adds {raw[position]:.0f} pts to the lineup"
        else:
            note = f"{position} starters are set"

        needs[position] = PositionNeed(
            position=position,
            rostered=counts.get(position, 0),
            starters_required=required,
            starters_filled=min(filled.get(position, 0), required),
            marginal_value=raw[position],
            need_score=need_score,
            urgent=urgent,
            note=note,
        )
    return needs


@dataclass
class ByeConflict:
    week: int
    players: list[str]
    positions: list[str]

    @property
    def severity(self) -> str:
        return "high" if len(self.players) >= 3 else "moderate"


def bye_week_conflicts(roster: list[RosterPlayer], shape: LeagueShape) -> list[ByeConflict]:
    """Weeks where two or more projected *starters* are on bye."""
    lineup = build_optimal_lineup(roster, shape)
    starters = [s.player for s in lineup.starters if s.player is not None]

    by_week: dict[int, list[RosterPlayer]] = defaultdict(list)
    for player in starters:
        if player.bye_week:
            by_week[player.bye_week].append(player)

    conflicts = [
        ByeConflict(
            week=week,
            players=[p.name for p in players],
            positions=[p.position for p in players],
        )
        for week, players in sorted(by_week.items())
        if len(players) >= 2
    ]
    return conflicts


@dataclass
class RosterStrength:
    position: str
    starters_points: float
    league_average_points: float
    #: Positive = stronger than an average team in this league.
    edge: float
    grade: str


def positional_strength(
    roster: list[RosterPlayer],
    shape: LeagueShape,
    replacement_points: dict[str, float],
    average_starter_points: dict[str, float],
) -> list[RosterStrength]:
    """Compare our starters at each position to a typical team in this league."""
    lineup = build_optimal_lineup(roster, shape)
    by_position: dict[str, list[float]] = defaultdict(list)
    for assignment in lineup.starters:
        if assignment.player is not None:
            by_position[assignment.player.position].append(assignment.player.projected_points)

    out: list[RosterStrength] = []
    for position in shape.positions_in_play:
        mine = by_position.get(position, [])
        # Positions we haven't drafted yet aren't "weak" -- they're unfilled, and
        # the Remaining Needs view is what speaks to those.  Grading them here
        # would bury a genuinely good early roster under phantom deficits.
        if not mine:
            continue
        replacement = replacement_points.get(position, 0.0)
        expected_each = average_starter_points.get(position, replacement)

        mine_total = sum(mine)
        expected_total = expected_each * len(mine)
        edge = round(mine_total - expected_total, 1)

        spread = max(abs(expected_each) * 0.18, 12.0)
        if edge > spread * 1.5:
            grade = "elite"
        elif edge > spread * 0.4:
            grade = "strong"
        elif edge > -spread * 0.4:
            grade = "average"
        elif edge > -spread * 1.5:
            grade = "weak"
        else:
            grade = "critical"

        out.append(
            RosterStrength(
                position=position,
                starters_points=round(mine_total, 1),
                league_average_points=round(expected_total, 1),
                edge=edge,
                grade=grade,
            )
        )
    return out


def roster_construction_score(
    roster: list[RosterPlayer],
    shape: LeagueShape,
    strengths: list[RosterStrength],
    conflicts: list[ByeConflict],
    picks_made: int,
) -> tuple[float, list[str]]:
    """0-100 roster health, with the reasons that produced it.

    Deliberately simple and legible: start at a neutral 60, add for lineup
    quality and depth, subtract for holes and bye pile-ups.
    """
    notes: list[str] = []
    score = 60.0

    lineup = build_optimal_lineup(roster, shape)

    # Starter coverage matters most, but only once you've had picks to use.
    total_slots = max(shape.starter_slots, 1)
    filled = sum(1 for s in lineup.starters if s.player is not None)
    coverage = filled / total_slots
    expected_coverage = min(picks_made / total_slots, 1.0) if picks_made else 0.0
    coverage_delta = coverage - expected_coverage
    score += coverage_delta * 25
    if lineup.empty_slots:
        notes.append(f"Unfilled starting slots: {', '.join(sorted(set(lineup.empty_slots)))}")
    else:
        notes.append("Every starting slot is filled")

    # Lineup quality relative to the league.
    edge_total = sum(s.edge for s in strengths)
    score += max(-20.0, min(20.0, edge_total / 12.0))
    elite = [s.position for s in strengths if s.grade == "elite"]
    weak = [s.position for s in strengths if s.grade in {"weak", "critical"}]
    if elite:
        notes.append(f"Position advantage at {', '.join(elite)}")
    if weak:
        notes.append(f"Below league average at {', '.join(weak)}")

    # Depth: bench players who could actually start.
    startable_bench = sum(1 for p in lineup.bench if p.vor > 0)
    score += min(startable_bench * 1.8, 9.0)
    if startable_bench:
        notes.append(f"{startable_bench} bench player(s) projected above replacement")

    # Bye stacking.
    penalty = sum(4.0 if c.severity == "high" else 1.8 for c in conflicts)
    score -= min(penalty, 12.0)
    if conflicts:
        worst = max(conflicts, key=lambda c: len(c.players))
        notes.append(
            f"{len(worst.players)} starters share a week {worst.week} bye"
        )

    return (round(max(0.0, min(100.0, score)), 1), notes)
