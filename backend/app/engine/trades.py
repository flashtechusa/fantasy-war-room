"""Trade analyzer.

A trade is not "who won on raw points" -- it is what each roster's *starting
lineup* looks like afterwards. Trading your RB3 for someone's WR2 can be a big
win even though you gave up more total projected points, because the RB3 was
never in your lineup.

So both sides are evaluated the same way: rebuild the optimal lineup before and
after, over two horizons (this week and rest-of-season), and report the delta.
Positional consequences and the roster-count change are reported alongside,
because a trade that improves your lineup while leaving you one injury away
from a hole is worth knowing about.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .league_shape import LeagueShape
from .roster import build_optimal_lineup
from .weekly import WeeklyPlayer

#: Below this the projections simply aren't precise enough to call a winner.
NOISE_FLOOR = 3.0


@dataclass
class SideResult:
    label: str
    gives: list[WeeklyPlayer]
    gets: list[WeeklyPlayer]
    week_before: float
    week_after: float
    season_before: float
    season_after: float
    roster_change: int
    position_changes: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def week_delta(self) -> float:
        return round(self.week_after - self.week_before, 2)

    @property
    def season_delta(self) -> float:
        return round(self.season_after - self.season_before, 2)


@dataclass
class TradeResult:
    week: int
    my_side: SideResult
    their_side: SideResult | None
    verdict: str
    summary: str
    reasons: list[str] = field(default_factory=list)


def _lineup(roster: list[WeeklyPlayer], shape: LeagueShape, use_week: bool) -> float:
    return build_optimal_lineup(
        [p.as_roster_player(use_week=use_week) for p in roster], shape
    ).total_points


def _evaluate_side(
    label: str,
    roster: list[WeeklyPlayer],
    gives: list[WeeklyPlayer],
    gets: list[WeeklyPlayer],
    shape: LeagueShape,
) -> SideResult:
    given = {p.espn_player_id for p in gives}
    after = [p for p in roster if p.espn_player_id not in given] + list(gets)

    counts_before = Counter(p.position for p in roster)
    counts_after = Counter(p.position for p in after)
    changes = {
        position: counts_after.get(position, 0) - counts_before.get(position, 0)
        for position in set(counts_before) | set(counts_after)
        if counts_after.get(position, 0) != counts_before.get(position, 0)
    }

    side = SideResult(
        label=label,
        gives=gives,
        gets=gets,
        week_before=round(_lineup(roster, shape, True), 2),
        week_after=round(_lineup(after, shape, True), 2),
        season_before=round(_lineup(roster, shape, False), 2),
        season_after=round(_lineup(after, shape, False), 2),
        roster_change=len(after) - len(roster),
        position_changes=changes,
    )

    # Does the trade leave a starting slot unfillable?
    lineup_after = build_optimal_lineup(
        [p.as_roster_player(use_week=False) for p in after], shape
    )
    if lineup_after.empty_slots:
        side.notes.append(
            f"Leaves {', '.join(sorted(set(lineup_after.empty_slots)))} unfilled"
        )

    for position, delta in sorted(changes.items()):
        if delta < 0:
            remaining = counts_after.get(position, 0)
            starters = shape.starters_at(position)
            if remaining <= starters:
                side.notes.append(
                    f"Down to {remaining} {position} for {starters} starting slot(s) "
                    "-- no cover for an injury"
                )
    return side


def analyse_trade(
    my_roster: list[WeeklyPlayer],
    give: list[WeeklyPlayer],
    receive: list[WeeklyPlayer],
    shape: LeagueShape,
    week: int,
    their_roster: list[WeeklyPlayer] | None = None,
    their_label: str = "Their team",
) -> TradeResult:
    """Evaluate a proposed trade from both sides where possible."""
    mine = _evaluate_side("Your team", my_roster, give, receive, shape)

    theirs: SideResult | None = None
    if their_roster:
        # Mirror image: they give what you receive.
        theirs = _evaluate_side(their_label, their_roster, receive, give, shape)

    verdict, summary, reasons = _judge(mine, theirs, week)
    return TradeResult(
        week=week, my_side=mine, their_side=theirs,
        verdict=verdict, summary=summary, reasons=reasons,
    )


def _judge(
    mine: SideResult, theirs: SideResult | None, week: int
) -> tuple[str, str, list[str]]:
    season = mine.season_delta
    weekly = mine.week_delta
    reasons: list[str] = []

    if season > NOISE_FLOOR * 3:
        verdict, summary = "accept", "Clear win for you."
    elif season > NOISE_FLOOR:
        verdict, summary = "lean accept", "Modest but real upgrade."
    elif season < -NOISE_FLOOR * 3:
        verdict, summary = "reject", "This makes your lineup clearly worse."
    elif season < -NOISE_FLOOR:
        verdict, summary = "lean reject", "Small downgrade to your lineup."
    else:
        verdict, summary = "neutral", "Too close to call on projections alone."

    reasons.append(
        f"Rest of season: {season:+.1f} pts to your starting lineup "
        f"({mine.season_before:.0f} → {mine.season_after:.0f})"
    )
    reasons.append(f"Week {week}: {weekly:+.1f} pts")

    if weekly * season < 0:
        reasons.append(
            "This week and the rest of the season disagree -- decide which "
            "matters more given your record"
        )

    if mine.roster_change < 0:
        reasons.append(
            f"You end up {abs(mine.roster_change)} player(s) short and can add "
            "from the wire"
        )
    elif mine.roster_change > 0:
        reasons.append(
            f"You take on {mine.roster_change} extra player(s) -- you'll need to drop"
        )

    for note in mine.notes:
        reasons.append(note)

    if theirs is not None:
        reasons.append(
            f"{theirs.label}: {theirs.season_delta:+.1f} pts rest of season"
        )
        if theirs.season_delta > NOISE_FLOOR and season > NOISE_FLOOR:
            reasons.append("Both sides improve -- the kind of trade that gets accepted")
        elif theirs.season_delta < -NOISE_FLOOR * 3:
            reasons.append("Heavily lopsided in your favour; they are unlikely to accept")

    if verdict == "neutral":
        reasons.append(
            f"The gap is inside the {NOISE_FLOOR:.0f}-pt noise floor of the projections"
        )

    return (verdict, summary, reasons)
