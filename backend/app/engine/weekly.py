"""Phase 8 -- in-season decisions: start/sit, waivers, trades.

Everything here reuses the draft engine's machinery rather than inventing a
parallel one: the same league scoring rules, the same lineup optimiser, the
same replacement levels. What changes is the horizon -- a single week instead
of a season -- and the currency: *marginal lineup points*, which is the only
number that answers "does this actually help me win Sunday?".

The three questions this answers:

* **Start/sit**  -- who maximises my lineup this week, and which calls are close
  enough that the projection isn't really deciding?
* **Waivers**    -- which free agent improves my starting lineup the most, who
  do I drop for him, and what is he worth in FAAB?
* **Trades**     -- what does a proposed swap do to my lineup this week and for
  the rest of the season, on both sides?
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .league_shape import LeagueShape
from .roster import RosterPlayer, build_optimal_lineup

#: Two projections inside this many points are a coin flip, not a decision.
CLOSE_CALL_POINTS = 1.5

#: Designations that should never be started without a warning.
BENCH_WARNING_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION", "DOUBTFUL"}


@dataclass
class WeeklyPlayer:
    """A player evaluated for one specific week."""

    espn_player_id: int
    name: str
    position: str
    pro_team: str = ""
    week_points: float = 0.0
    season_points: float = 0.0
    vor: float = 0.0
    bye_week: int | None = None
    injury_status: str = "ACTIVE"
    percent_owned: float = 0.0
    availability: str = ""
    #: True when the projection came from a real weekly split rather than a
    #: season total divided by the games remaining.
    week_projection_is_real: bool = True

    @property
    def on_bye(self) -> bool:
        return self.bye_week is not None and self.week_points == 0.0

    def as_roster_player(self, use_week: bool = True) -> RosterPlayer:
        return RosterPlayer(
            espn_player_id=self.espn_player_id,
            name=self.name,
            position=self.position,
            projected_points=self.week_points if use_week else self.season_points,
            bye_week=self.bye_week,
            pro_team=self.pro_team,
            vor=self.vor,
            injury_status=self.injury_status,
        )


# ---------------------------------------------------------------------------
# Start / sit
# ---------------------------------------------------------------------------


@dataclass
class SlotDecision:
    slot: str
    player: WeeklyPlayer | None
    #: The best player who did NOT get this slot, when there was a real choice.
    next_best: WeeklyPlayer | None = None
    margin: float = 0.0
    close_call: bool = False
    warning: str = ""
    reason: str = ""


@dataclass
class StartSitResult:
    week: int
    starters: list[SlotDecision] = field(default_factory=list)
    bench: list[WeeklyPlayer] = field(default_factory=list)
    projected_points: float = 0.0
    #: Points gained versus the lineup you would get by starting your
    #: highest-season-projection players regardless of this week.
    points_vs_naive: float = 0.0
    warnings: list[str] = field(default_factory=list)
    close_calls: list[SlotDecision] = field(default_factory=list)
    unfilled_slots: list[str] = field(default_factory=list)


def optimise_lineup(
    roster: list[WeeklyPlayer], shape: LeagueShape, week: int
) -> StartSitResult:
    """Best starting lineup for one week, with the reasoning for each slot."""
    result = StartSitResult(week=week)
    if not roster:
        result.unfilled_slots = _all_slots(shape)
        return result

    optimal = build_optimal_lineup([p.as_roster_player() for p in roster], shape)
    by_id = {p.espn_player_id: p for p in roster}
    started_ids = {
        a.player.espn_player_id for a in optimal.starters if a.player is not None
    }

    for assignment in optimal.starters:
        weekly = by_id.get(assignment.player.espn_player_id) if assignment.player else None
        decision = SlotDecision(slot=assignment.slot, player=weekly)

        if weekly is None:
            decision.reason = f"No eligible player for {assignment.slot}"
            result.unfilled_slots.append(assignment.slot)
            result.starters.append(decision)
            continue

        # The best benched player who could have taken this slot instead.
        eligible = [
            p for p in roster
            if p.espn_player_id not in started_ids
            and _fits(p.position, assignment.slot, shape)
        ]
        if eligible:
            alternative = max(eligible, key=lambda p: p.week_points)
            decision.next_best = alternative
            decision.margin = round(weekly.week_points - alternative.week_points, 2)
            decision.close_call = abs(decision.margin) < CLOSE_CALL_POINTS

        status = (weekly.injury_status or "").upper()
        if weekly.on_bye:
            decision.warning = f"{weekly.name} is on bye in week {week}"
        elif status in BENCH_WARNING_STATUSES:
            decision.warning = f"{weekly.name} is listed {status.title()}"

        if decision.warning:
            result.warnings.append(decision.warning)

        if decision.next_best is None:
            decision.reason = f"Only eligible {assignment.slot} on your roster"
        elif decision.close_call:
            decision.reason = (
                f"Essentially a coin flip -- {decision.margin:+.1f} pts over "
                f"{decision.next_best.name}"
            )
            result.close_calls.append(decision)
        else:
            decision.reason = (
                f"{decision.margin:+.1f} pts over {decision.next_best.name} "
                f"({decision.next_best.week_points:.1f})"
            )

        result.starters.append(decision)

    result.bench = [p for p in roster if p.espn_player_id not in started_ids]
    result.bench.sort(key=lambda p: p.week_points, reverse=True)
    result.projected_points = round(optimal.total_points, 2)

    # What you'd score by just starting your best season-long players.
    naive = build_optimal_lineup(
        [p.as_roster_player(use_week=False) for p in roster], shape
    )
    naive_week_total = sum(
        by_id[a.player.espn_player_id].week_points
        for a in naive.starters
        if a.player is not None and a.player.espn_player_id in by_id
    )
    result.points_vs_naive = round(result.projected_points - naive_week_total, 2)
    return result


def _fits(position: str, slot: str, shape: LeagueShape) -> bool:
    if slot == position:
        return True
    for flex in shape.flex:
        if flex.label == slot and position in flex.eligible:
            return True
    return False


def _all_slots(shape: LeagueShape) -> list[str]:
    slots = []
    for position, count in shape.dedicated.items():
        slots.extend([position] * count)
    for flex in shape.flex:
        slots.extend([flex.label] * flex.count)
    return slots
