"""Waiver wire recommendations.

The question a waiver claim answers is not "is this guy good?" but "does adding
him, and dropping somebody to make room, score me more points?". So every
candidate is valued the same way: rebuild the optimal lineup with him on the
roster and the worst droppable player gone, and measure the difference.

Two horizons are reported, because they disagree often and for good reasons:

* **This week**   -- a streaming DST against a bad offense can win you Sunday.
* **Rest of season** -- a backup RB who just inherited a starting job is worth
  far more over fourteen weeks than he is this Sunday.

FAAB guidance is derived from your league's actual budget and the size of the
upgrade, not from a table someone published for a different league.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .league_shape import LeagueShape
from .roster import build_optimal_lineup
from .weekly import WeeklyPlayer

#: Never suggest dropping someone this far above replacement for a streamer.
PROTECTED_VOR = 25.0

#: FAAB: the share of your remaining budget a full-point upgrade is worth.
#: Tuned so a genuine league-winner lands near a third of the budget and a
#: streamer lands in low single digits.
FAAB_POINTS_PER_PERCENT = 1.6


@dataclass
class DropCandidate:
    player: WeeklyPlayer
    reason: str


@dataclass
class WaiverTarget:
    player: WeeklyPlayer
    #: Points added to this week's optimal lineup.
    week_gain: float
    #: Points added to the season-long optimal lineup.
    season_gain: float
    drop: WeeklyPlayer | None
    #: 0-100, comparable across candidates.
    priority: float
    faab_bid: int
    faab_pct: float
    reasons: list[str] = field(default_factory=list)
    verdict: str = "depth"      # must-add | starter | streamer | depth | stash


def _lineup_points(
    roster: list[WeeklyPlayer], shape: LeagueShape, use_week: bool
) -> float:
    return build_optimal_lineup(
        [p.as_roster_player(use_week=use_week) for p in roster], shape
    ).total_points


def _droppable(
    roster: list[WeeklyPlayer], shape: LeagueShape
) -> list[DropCandidate]:
    """Who can go. Never the players holding your lineup together."""
    lineup = build_optimal_lineup([p.as_roster_player(use_week=False) for p in roster], shape)
    starters = {a.player.espn_player_id for a in lineup.starters if a.player}

    out: list[DropCandidate] = []
    for player in roster:
        if player.espn_player_id in starters:
            continue
        if player.vor >= PROTECTED_VOR:
            continue
        reason = "bench depth"
        if (player.injury_status or "").upper() in {"OUT", "INJURY_RESERVE", "SUSPENSION"}:
            reason = f"listed {player.injury_status.title()}"
        elif player.vor <= 0:
            reason = "below replacement level"
        out.append(DropCandidate(player=player, reason=reason))

    out.sort(key=lambda c: c.player.season_points)
    return out


@dataclass
class PositionVerdict:
    """Why a position produced no recommendation -- or produced one."""

    position: str
    considered: int
    #: Best free agent at this position, by rest-of-season points.
    best_name: str = ""
    best_points: float = 0.0
    #: What you already start there, which is the bar he had to clear.
    incumbent_name: str = ""
    incumbent_points: float = 0.0
    helps: bool = False
    note: str = ""


def explain_by_position(
    roster: list[WeeklyPlayer],
    free_agents: list[WeeklyPlayer],
    shape: LeagueShape,
    recommended: set[int],
) -> list[PositionVerdict]:
    """Per position: the best man available, and why he did or didn't make it.

    A wire that returns eight defences and nothing else looks broken. Usually
    it isn't -- it means every other position on the roster already beats what
    is out there. That is a useful answer, and it was being thrown away: a
    candidate who does not improve the lineup is dropped silently, so the
    screen could only ever show the one position where the wire wins.
    """
    lineup = build_optimal_lineup(
        [p.as_roster_player(use_week=False) for p in roster], shape
    )
    weakest_starter: dict[str, WeeklyPlayer] = {}
    by_id = {p.espn_player_id: p for p in roster}
    for assignment in lineup.starters:
        if assignment.player is None:
            continue
        player = by_id.get(assignment.player.espn_player_id)
        if player is None:
            continue
        current = weakest_starter.get(player.position)
        if current is None or player.season_points < current.season_points:
            weakest_starter[player.position] = player

    out: list[PositionVerdict] = []
    for position in sorted({p.position for p in free_agents}):
        group = sorted(
            [p for p in free_agents if p.position == position],
            key=lambda p: p.season_points,
            reverse=True,
        )
        if not group:
            continue
        best = group[0]
        incumbent = weakest_starter.get(position)
        helps = any(p.espn_player_id in recommended for p in group)

        if helps:
            note = "on the list below"
        elif incumbent is None:
            note = f"you start nobody at {position}"
        else:
            margin = incumbent.season_points - best.season_points
            note = (
                f"{best.name} is the best out there and projects "
                f"{margin:.0f} pts less than {incumbent.name}, "
                f"who you already start"
            )

        out.append(
            PositionVerdict(
                position=position,
                considered=len(group),
                best_name=best.name,
                best_points=best.season_points,
                incumbent_name=incumbent.name if incumbent else "",
                incumbent_points=incumbent.season_points if incumbent else 0.0,
                helps=helps,
                note=note,
            )
        )
    return out


def recommend_waivers(
    roster: list[WeeklyPlayer],
    free_agents: list[WeeklyPlayer],
    shape: LeagueShape,
    week: int,
    faab_budget: int = 0,
    faab_remaining: int | None = None,
    roster_is_full: bool = True,
    limit: int = 15,
) -> list[WaiverTarget]:
    """Rank the wire by how much each addition actually improves your lineup."""
    if not free_agents:
        return []

    base_week = _lineup_points(roster, shape, use_week=True)
    base_season = _lineup_points(roster, shape, use_week=False)
    drops = _droppable(roster, shape)
    budget = faab_remaining if faab_remaining is not None else faab_budget

    targets: list[WaiverTarget] = []
    for candidate in free_agents:
        # Adding someone means dropping someone, unless there's an open spot.
        drop = drops[0].player if (roster_is_full and drops) else None
        trial = [p for p in roster if drop is None or p.espn_player_id != drop.espn_player_id]
        trial = trial + [candidate]

        week_gain = round(_lineup_points(trial, shape, use_week=True) - base_week, 2)
        season_gain = round(_lineup_points(trial, shape, use_week=False) - base_season, 2)

        if week_gain <= 0 and season_gain <= 0:
            continue     # doesn't improve anything; not a recommendation

        targets.append(
            WaiverTarget(
                player=candidate,
                week_gain=week_gain,
                season_gain=season_gain,
                drop=drop,
                priority=0.0,
                faab_bid=0,
                faab_pct=0.0,
            )
        )

    if not targets:
        return []

    # Priority blends the immediate and the durable; season value dominates
    # because a wrong week costs you once and a wrong season costs you all year.
    peak = max(t.season_gain * 0.7 + t.week_gain * 0.3 for t in targets) or 1.0
    for target in targets:
        blended = target.season_gain * 0.7 + target.week_gain * 0.3
        target.priority = round(max(0.0, min(100.0, 100.0 * blended / peak)), 1)

        if budget > 0:
            pct = min(blended / FAAB_POINTS_PER_PERCENT, 60.0)
            target.faab_pct = round(max(pct, 0.0), 1)
            target.faab_bid = max(1, int(round(budget * target.faab_pct / 100)))

        target.verdict = _verdict(target, shape)
        target.reasons = _reasons(target, week, budget)

    targets.sort(key=lambda t: t.priority, reverse=True)
    return targets[:limit]


def _verdict(target: WaiverTarget, shape: LeagueShape) -> str:
    if target.season_gain >= 25:
        return "must-add"
    if target.season_gain >= 10:
        return "starter"
    if target.week_gain >= 3 and target.season_gain < 5:
        return "streamer"
    if target.season_gain > 0 and target.week_gain <= 0:
        return "stash"
    return "depth"


def _reasons(target: WaiverTarget, week: int, budget: int) -> list[str]:
    player = target.player
    out: list[str] = []

    if target.week_gain > 0:
        out.append(
            f"Adds {target.week_gain:.1f} pts to your week {week} lineup"
        )
    else:
        out.append(f"Doesn't crack this week's lineup, but helps later")

    if target.season_gain > 0:
        out.append(f"Worth {target.season_gain:.1f} pts to your lineup rest-of-season")

    if target.drop is not None:
        out.append(
            f"Drop {target.drop.name} ({target.drop.position}) to make room"
        )
    else:
        out.append("You have an open roster spot")

    if player.percent_owned:
        if player.percent_owned < 15:
            out.append(f"Only {player.percent_owned:.0f}% rostered -- likely unclaimed")
        elif player.percent_owned > 55:
            out.append(f"{player.percent_owned:.0f}% rostered -- expect competition")

    if target.verdict == "streamer":
        out.append("Short-term play; drop him next week without regret")
    elif target.verdict == "stash":
        out.append("Stash: the value is in the weeks ahead, not this Sunday")

    if budget > 0 and target.faab_bid:
        out.append(f"Suggested bid {target.faab_bid} of your {budget} FAAB")

    return out
