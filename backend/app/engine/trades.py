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

import itertools
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


# ---------------------------------------------------------------------------
# Trade finder -- propose trades rather than only grading one
# ---------------------------------------------------------------------------

#: Only the top of each roster is realistically tradeable; a deep-bench scrub
#: changes no lineup, so including it just multiplies combinations for nothing.
_POOL_PER_SIDE = 14
#: Trade shapes offered: 1-for-1, 2-for-1, 1-for-2, 2-for-2.
_SHAPES = ((1, 1), (2, 1), (1, 2), (2, 2))
#: How many proxy-ranked candidates per team get the full both-sides lineup
#: evaluation. Bounds the expensive work to ~this * team_count.
_EVAL_BUDGET_PER_TEAM = 60


@dataclass
class TradeProposal:
    their_team_id: int
    their_label: str
    give: list[WeeklyPlayer]
    receive: list[WeeklyPlayer]
    my_week_delta: float
    my_season_delta: float
    their_week_delta: float
    their_season_delta: float
    #: The delta on the chosen horizon, for ranking + display.
    my_delta: float
    their_delta: float
    #: "mutual" (both lineups improve) or "longshot" (lopsided in your favour).
    kind: str
    headline: str
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def key(self) -> frozenset:
        ids = [p.espn_player_id for p in self.give] + [p.espn_player_id for p in self.receive]
        return frozenset(ids) | {("team", self.their_team_id)}


def _pool(roster: list[WeeklyPlayer], use_week: bool) -> list[WeeklyPlayer]:
    key = (lambda p: p.week_points) if use_week else (lambda p: p.season_points)
    return sorted(roster, key=key, reverse=True)[:_POOL_PER_SIDE]


def _pts(players: list[WeeklyPlayer], use_week: bool) -> float:
    return sum((p.week_points if use_week else p.season_points) for p in players)


def _leaves_hole(side: SideResult) -> bool:
    return any("unfilled" in n.lower() for n in side.notes)


def _lineup_context(
    roster: list[WeeklyPlayer], shape: LeagueShape, use_week: bool
) -> tuple[set[int], float]:
    """Who currently starts, and the points of the weakest starter.

    Cheap ranking uses this so a *bench* player (not a starter) costs ~nothing
    to give, and an incoming player only counts as a gain when it beats the
    weakest thing already in the lineup. That keeps fair, mutually-good swaps
    from being crowded out of the evaluation budget by lopsided value grabs.
    """
    lineup = build_optimal_lineup([p.as_roster_player(use_week) for p in roster], shape)
    start_ids = {a.player.espn_player_id for a in lineup.starters if a.player is not None}
    starter_pts = [
        (p.week_points if use_week else p.season_points)
        for p in roster
        if p.espn_player_id in start_ids
    ]
    floor = min(starter_pts) if starter_pts else 0.0
    return start_ids, floor


def _headline(give: list[WeeklyPlayer], receive: list[WeeklyPlayer]) -> str:
    g = " + ".join(f"{p.name} ({p.position})" for p in give)
    r = " + ".join(f"{p.name} ({p.position})" for p in receive)
    return f"Give {g} for {r}"


def find_trades(
    my_roster: list[WeeklyPlayer],
    teams: list[tuple[int, str, list[WeeklyPlayer]]],
    shape: LeagueShape,
    week: int,
    *,
    horizon: str = "season",
    include_longshots: bool = True,
    max_per_team: int = 3,
    max_results: int = 15,
) -> dict:
    """Propose trades with each other team that improve *your* starting lineup.

    A trade is "mutual" when it lifts both starting lineups -- the only kind a
    rational manager accepts, and the whole reason this beats eyeballing the
    league: you give from a position of surplus (a bench player never in your
    lineup) and receive into a position of need, and so does the other side.
    "longshot" trades help you but not them; they are surfaced separately and
    clearly labelled as unlikely.

    `horizon` is "season" (rest-of-season lineup points) or "week" (this week),
    which sets both the ranking and the accept/reject threshold. Nothing here is
    sent to ESPN; these are ranked proposals to execute yourself.
    """
    use_week = horizon == "week"
    my_pool = _pool(my_roster, use_week)
    my_start_ids, my_floor = _lineup_context(my_roster, shape, use_week)

    mutual: list[TradeProposal] = []
    longshots: list[TradeProposal] = []

    def _my_gain_proxy(give: list[WeeklyPlayer], receive: list[WeeklyPlayer]) -> float:
        # Incoming players only help if they beat the weakest current starter;
        # outgoing players only cost if they were actually starting.
        gain_in = sum(max(0.0, (p.week_points if use_week else p.season_points) - my_floor) for p in receive)
        loss_out = sum(
            max(0.0, (p.week_points if use_week else p.season_points) - my_floor)
            for p in give
            if p.espn_player_id in my_start_ids
        )
        return gain_in - loss_out

    for team_id, label, their_roster in teams:
        their_pool = _pool(their_roster, use_week)
        if not their_pool:
            continue

        # 1) Enumerate candidate shapes; drop only clearly-fleece-me combos on
        #    raw value (real fairness is judged per-side after the full rebuild).
        #    Rank by an estimate of *your* lineup gain so fair mutual swaps are
        #    not crowded out of the evaluation budget by lopsided value grabs.
        candidates: list[tuple[float, list, list]] = []
        for give_n, recv_n in _SHAPES:
            for give in itertools.combinations(my_pool, give_n):
                give_val = _pts(list(give), use_week)
                for receive in itertools.combinations(their_pool, recv_n):
                    balance = _pts(list(receive), use_week) - give_val
                    if balance < -12 or balance > 140:
                        continue
                    g, r = list(give), list(receive)
                    candidates.append((_my_gain_proxy(g, r), g, r))

        candidates.sort(key=lambda c: c[0], reverse=True)

        seen: set[frozenset] = set()
        found_here: list[TradeProposal] = []
        for _balance, give, receive in candidates[: _EVAL_BUDGET_PER_TEAM]:
            res = analyse_trade(
                my_roster=my_roster, give=give, receive=receive, shape=shape,
                week=week, their_roster=their_roster, their_label=label,
            )
            mine, theirs = res.my_side, res.their_side
            if theirs is None:
                continue
            my_d = mine.week_delta if use_week else mine.season_delta
            their_d = theirs.week_delta if use_week else theirs.season_delta

            if my_d <= NOISE_FLOOR:
                continue  # must actually help you
            if _leaves_hole(mine) or _leaves_hole(theirs):
                continue  # never propose a trade that opens a starting hole

            if their_d > NOISE_FLOOR:
                kind = "mutual"
            elif include_longshots and their_d > -NOISE_FLOOR * 3:
                kind = "longshot"
            else:
                continue  # clearly bad for them -- not worth proposing

            proposal = TradeProposal(
                their_team_id=team_id, their_label=label,
                give=give, receive=receive,
                my_week_delta=mine.week_delta, my_season_delta=mine.season_delta,
                their_week_delta=theirs.week_delta, their_season_delta=theirs.season_delta,
                my_delta=my_d, their_delta=their_d, kind=kind,
                headline=_headline(give, receive), reasons=res.reasons, notes=mine.notes,
            )
            if proposal.key() in seen:
                continue
            seen.add(proposal.key())
            found_here.append(proposal)

        # Prefer simpler trades: drop a bigger one if a smaller already seen
        # this team gets within a hair of its gain.
        found_here.sort(key=lambda p: (p.kind != "mutual", -p.my_delta, len(p.give) + len(p.receive)))
        kept: list[TradeProposal] = []
        for p in found_here:
            dominated = any(
                len(k.give) + len(k.receive) <= len(p.give) + len(p.receive)
                and k.my_delta >= p.my_delta - 1.0
                and set(x.espn_player_id for x in k.receive) & set(x.espn_player_id for x in p.receive)
                for k in kept
            )
            if not dominated:
                kept.append(p)
            if len(kept) >= max_per_team:
                break
        for p in kept:
            (mutual if p.kind == "mutual" else longshots).append(p)

    mutual.sort(key=lambda p: (-p.my_delta, -(p.my_delta + p.their_delta)))
    longshots.sort(key=lambda p: -p.my_delta)
    return {
        "horizon": horizon,
        "mutual": mutual[:max_results],
        "longshots": longshots[:max_results] if include_longshots else [],
    }
